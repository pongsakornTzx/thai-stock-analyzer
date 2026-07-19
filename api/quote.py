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


def _cache_get(key):
    entry = _CACHE.get(key)
    if entry and time.time() - entry["ts"] < CACHE_TTL:
        return entry["data"]
    return None


def _cache_set(key, data):
    _CACHE[key] = {"data": data, "ts": time.time()}


def fetch_quote(symbol: str) -> dict:
    cached = _cache_get(symbol)
    if cached:
        return {**cached, "cached": True}

    ticker_sym = symbol if symbol.endswith(".BK") else f"{symbol}.BK"
    tk = yf.Ticker(ticker_sym)
    fi = tk.fast_info

    price = fi.last_price or fi.previous_close or 0
    prev = fi.previous_close or price
    change = round(price - prev, 2)
    change_pct = round((change / prev) * 100, 2) if prev else 0.0

    data = {
        "symbol": symbol,
        "price": round(price, 2),
        "change": change,
        "change_pct": change_pct,
        "open": round(getattr(fi, "open", None) or price, 2),
        "high": round(fi.day_high or price, 2),
        "low": round(fi.day_low or price, 2),
        "volume": int(fi.three_month_average_volume or 0),
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
            data = fetch_quote(symbol)
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
