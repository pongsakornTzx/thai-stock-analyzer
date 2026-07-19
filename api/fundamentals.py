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
CACHE_TTL = 600


def _cache_get(key):
    entry = _CACHE.get(key)
    if entry and time.time() - entry["ts"] < CACHE_TTL:
        return entry["data"]
    return None


def _cache_set(key, data):
    _CACHE[key] = {"data": data, "ts": time.time()}


def fetch_fundamentals(symbol: str) -> dict:
    cached = _cache_get(symbol)
    if cached:
        return {**cached, "cached": True}

    ticker_sym = symbol if symbol.endswith(".BK") else f"{symbol}.BK"
    tk = yf.Ticker(ticker_sym)
    info = tk.info

    def _r(v, digits=2):
        try:
            return round(float(v), digits) if v is not None else None
        except (TypeError, ValueError):
            return None

    data = {
        "symbol": symbol,
        "pe": _r(info.get("trailingPE")),
        "pbv": _r(info.get("priceToBook")),
        "dividend_yield": _r((info.get("dividendYield") or 0) * 100),
        "eps": _r(info.get("trailingEps")),
        "market_cap": info.get("marketCap"),
        "revenue": info.get("totalRevenue"),
        "debt_to_equity": _r(info.get("debtToEquity")),
        "roe": _r((info.get("returnOnEquity") or 0) * 100),
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
            data = fetch_fundamentals(symbol)
            self._json(200, data)
        except Exception as e:
            self._json(500, {"error": str(e), "trace": traceback.format_exc()[-500:]})

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "public, s-maxage=600, stale-while-revalidate=120")

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
