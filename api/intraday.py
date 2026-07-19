from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import time
import traceback

try:
    import yfinance as yf
    YF_OK = True
except ImportError:
    YF_OK = False

_CACHE: dict = {}
CACHE_TTL = 60

VALID_INTERVALS = {"1m", "2m", "5m", "10m", "15m", "30m", "60m", "90m", "1h", "1d", "5d", "1wk", "1mo"}
VALID_RANGES = {"1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "ytd", "max"}


def _cache_get(key):
    entry = _CACHE.get(key)
    if entry and time.time() - entry["ts"] < CACHE_TTL:
        return entry["data"]
    return None


def _cache_set(key, data):
    _CACHE[key] = {"data": data, "ts": time.time()}


def fetch_intraday(symbol: str, period: str, interval: str) -> dict:
    cache_key = f"{symbol}:{period}:{interval}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    ticker_sym = f"{symbol}.BK"
    tk = yf.Ticker(ticker_sym)
    hist = tk.history(period=period, interval=interval, auto_adjust=True)

    if hist.empty:
        raise ValueError(f"No data for {symbol} period={period} interval={interval}")

    timestamps = []
    opens, highs, lows, closes, volumes = [], [], [], [], []

    for idx, row in hist.iterrows():
        try:
            ts = int(idx.timestamp())
        except Exception:
            continue
        timestamps.append(ts)
        opens.append(round(float(row["Open"]), 4) if row["Open"] == row["Open"] else None)
        highs.append(round(float(row["High"]), 4) if row["High"] == row["High"] else None)
        lows.append(round(float(row["Low"]), 4) if row["Low"] == row["Low"] else None)
        closes.append(round(float(row["Close"]), 4) if row["Close"] == row["Close"] else None)
        volumes.append(int(row["Volume"]) if row["Volume"] == row["Volume"] else None)

    result = {
        "chart": {
            "result": [{
                "timestamp": timestamps,
                "indicators": {
                    "quote": [{
                        "open": opens,
                        "high": highs,
                        "low": lows,
                        "close": closes,
                        "volume": volumes,
                    }]
                }
            }],
            "error": None,
        }
    }
    _cache_set(cache_key, result)
    return result


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        raw_sym = (qs.get("symbol", [""])[0] or "").upper().strip().replace(".BK", "")
        period = (qs.get("range", ["1d"])[0] or "1d").lower()
        interval = (qs.get("interval", ["5m"])[0] or "5m").lower()

        if not raw_sym or not raw_sym.replace("", "").replace("-", "").isalnum():
            self._json(400, {"error": "missing or invalid ?symbol= parameter"})
            return

        if period not in VALID_RANGES:
            period = "1d"
        if interval not in VALID_INTERVALS:
            interval = "5m"

        try:
            if not YF_OK:
                raise RuntimeError("yfinance not installed")
            data = fetch_intraday(raw_sym, period, interval)
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
