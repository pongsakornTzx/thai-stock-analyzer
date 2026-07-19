from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import time
import traceback
import concurrent.futures

try:
    import yfinance as yf
    import pandas as pd
    YF_OK = True
except ImportError:
    YF_OK = False

_CACHE: dict = {}
CACHE_TTL = 60
MAX_SYMBOLS = 30


def _cache_get(key):
    entry = _CACHE.get(key)
    if entry and time.time() - entry["ts"] < CACHE_TTL:
        return entry["data"]
    return None


def _cache_set(key, data):
    _CACHE[key] = {"data": data, "ts": time.time()}


def _rsi(series, period: int = 14) -> float:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))
    val = rsi.dropna()
    return round(float(val.iloc[-1]), 2) if len(val) else 50.0


def _macd(series):
    exp12 = series.ewm(span=12, adjust=False).mean()
    exp26 = series.ewm(span=26, adjust=False).mean()
    line = exp12 - exp26
    sig = line.ewm(span=9, adjust=False).mean()
    hist = line - sig
    return (round(float(line.iloc[-1]), 4),
            round(float(sig.iloc[-1]), 4),
            round(float(hist.iloc[-1]), 4))


def fetch_combined(symbol: str) -> dict:
    cached = _cache_get(symbol)
    if cached:
        return {**cached, "cached": True}

    ticker_sym = f"{symbol}.BK"
    tk = yf.Ticker(ticker_sym)
    fi = tk.fast_info

    price = fi.last_price or fi.previous_close or 0
    prev = fi.previous_close or price
    change = round(price - prev, 2)
    change_pct = round((change / prev) * 100, 2) if prev else 0.0

    hist = tk.history(period="3mo", interval="1d", auto_adjust=True)
    rsi_val, macd_line, macd_signal, macd_hist = 50.0, 0.0, 0.0, 0.0
    bb_upper = bb_mid = bb_lower = ma20_val = ma50_val = price
    vol_ratio = 1.0
    mom = 0.0

    if not hist.empty and len(hist) >= 20:
        close = hist["Close"]
        rsi_val = _rsi(close)
        macd_line, macd_signal, macd_hist = _macd(close)
        ma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        bb_upper = round(float((ma20 + 2 * std20).iloc[-1]), 2)
        bb_mid = round(float(ma20.iloc[-1]), 2)
        bb_lower = round(float((ma20 - 2 * std20).iloc[-1]), 2)
        ma20_val = bb_mid
        if len(close) >= 50:
            ma50_val = round(float(close.rolling(50).mean().iloc[-1]), 2)
        vol = hist["Volume"]
        avg20 = vol.rolling(20).mean().iloc[-1]
        if avg20:
            vol_ratio = round(float(vol.iloc[-1] / avg20), 2)
        if len(close) >= 11:
            mom = round(float((close.iloc[-1] / close.iloc[-11] - 1) * 100), 2)

    data = {
        "symbol": symbol,
        "price": round(price, 2),
        "change": change,
        "change_pct": change_pct,
        "open": round(getattr(fi, "open", None) or price, 2),
        "high": round(fi.day_high or price, 2),
        "low": round(fi.day_low or price, 2),
        "volume": int(fi.three_month_average_volume or 0),
        "rsi": rsi_val,
        "macd": macd_line,
        "macd_signal": macd_signal,
        "macd_hist": macd_hist,
        "macd_histogram": macd_hist,
        "macd_line": macd_line,
        "bb_upper": bb_upper,
        "bb_mid": bb_mid,
        "bb_lower": bb_lower,
        "ma20": ma20_val,
        "ma50": ma50_val,
        "volume_ratio": vol_ratio,
        "momentum_10d": mom,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+07:00",
                                   time.localtime(time.time() + 7 * 3600)),
        "cached": False,
    }
    _cache_set(symbol, {k: v for k, v in data.items() if k != "cached"})
    return data


def fetch_batch(symbols: list) -> list:
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(symbols), 8)) as ex:
        future_map = {ex.submit(fetch_combined, s): s for s in symbols}
        for fut in concurrent.futures.as_completed(future_map):
            sym = future_map[fut]
            try:
                results.append(fut.result())
            except Exception as e:
                results.append({"symbol": sym, "error": str(e)})
    return results


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        raw = qs.get("symbols", [""])[0] or ""
        symbols = [s.strip().upper().replace(".BK", "")
                   for s in raw.split(",") if s.strip()]
        symbols = [s for s in symbols if s.isalpha()][:MAX_SYMBOLS]

        if not symbols:
            self._json(400, {"error": "missing or invalid ?symbols= parameter (comma-separated)"})
            return
        try:
            if not YF_OK:
                raise RuntimeError("yfinance not installed")
            data = fetch_batch(symbols)
            self._json(200, data)
        except Exception as e:
            self._json(500, {"error": str(e), "trace": traceback.format_exc()[-500:]})

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "public, s-maxage=60, stale-while-revalidate=30")

    def _json(self, status: int, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass
