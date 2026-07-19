from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import time
import traceback

try:
    import yfinance as yf
    import pandas as pd
    YF_OK = True
except ImportError:
    YF_OK = False

_CACHE: dict = {}
CACHE_TTL = 60


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
    signal = line.ewm(span=9, adjust=False).mean()
    hist = line - signal
    return (round(float(line.iloc[-1]), 4),
            round(float(signal.iloc[-1]), 4),
            round(float(hist.iloc[-1]), 4))


def fetch_indicators(symbol: str) -> dict:
    cached = _cache_get(symbol)
    if cached:
        return {**cached, "cached": True}

    ticker_sym = symbol if symbol.endswith(".BK") else f"{symbol}.BK"
    tk = yf.Ticker(ticker_sym)
    hist = tk.history(period="3mo", interval="1d", auto_adjust=True)
    if hist.empty or len(hist) < 30:
        raise ValueError(f"Not enough history for {symbol}")

    close = hist["Close"]
    rsi_val = _rsi(close)
    macd_line, macd_signal, macd_hist = _macd(close)

    ma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    bb_upper = round(float((ma20 + 2 * std20).iloc[-1]), 2)
    bb_mid = round(float(ma20.iloc[-1]), 2)
    bb_lower = round(float((ma20 - 2 * std20).iloc[-1]), 2)
    ma50 = round(float(close.rolling(50).mean().iloc[-1]), 2)

    vol = hist["Volume"]
    avg20 = vol.rolling(20).mean().iloc[-1]
    vol_ratio = round(float(vol.iloc[-1] / avg20), 2) if avg20 else 1.0

    mom = round(float((close.iloc[-1] / close.iloc[-11] - 1) * 100), 2) if len(close) >= 11 else 0.0

    last_price = float(close.iloc[-1])
    if last_price > ma20.iloc[-1] and macd_hist > 0:
        trend = "bullish"
    elif last_price < ma20.iloc[-1] and macd_hist < 0:
        trend = "bearish"
    else:
        trend = "neutral"

    signal_str = "Buy" if rsi_val < 70 and macd_hist > 0 else ("Sell" if rsi_val > 70 or macd_hist < 0 else "Hold")

    data = {
        "symbol": symbol,
        "rsi": rsi_val,
        "macd": macd_line,
        "macd_signal": macd_signal,
        "macd_hist": macd_hist,
        "macd_histogram": macd_hist,
        "macd_line": macd_line,
        "bb_upper": bb_upper,
        "bb_mid": bb_mid,
        "bb_lower": bb_lower,
        "ma20": bb_mid,
        "ma50": ma50,
        "volume_ratio": vol_ratio,
        "momentum_10d": mom,
        "trend": trend,
        "signal": signal_str,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+07:00",
                                   time.localtime(time.time() + 7 * 3600)),
        "cached": False,
    }
    _cache_set(symbol, {k: v for k, v in data.items() if k != "cached"})
    return data


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        symbol = (qs.get("symbol", [""])[0] or "").upper().strip().replace(".BK", "")
        if not symbol or not symbol.isalpha():
            self._json(400, {"error": "missing or invalid ?symbol= parameter"})
            return
        try:
            if not YF_OK:
                raise RuntimeError("yfinance not installed")
            data = fetch_indicators(symbol)
            self._json(200, data)
        except Exception as e:
            self._json(500, {"error": str(e), "trace": traceback.format_exc()[-500:]})

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "public, s-maxage=60, stale-while-revalidate=30")

    def _json(self, status: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass
