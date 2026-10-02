"""Stock quote for the footer.

One symbol, one number, and the only thing on the panel that talks to the open
internet. No key and no account anywhere: Nasdaq's own quote endpoint first,
Yahoo's chart endpoint as the spare (Yahoo rate-limits by IP and will answer
429 for a while once it has had enough). Quotes from both are delayed ~15 min,
like any free feed.

Fetched once at startup — so the number is on screen overnight and straight
after a rebuild — then once a minute during market hours only. Symbol, window
and poll interval are STOCK in config.py.
"""
import asyncio
import logging
from datetime import datetime, time as dtime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

LOG = logging.getLogger(__name__)

NASDAQ_URL = "https://api.nasdaq.com/api/quote/{symbol}/info"
YAHOO_URL = "https://{host}/v8/finance/chart/{symbol}"
YAHOO_HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
# Both sites answer 401/403/429 to an unbranded client; a plain browser UA is enough.
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/125.0 Safari/537.36",
           "Accept": "application/json,text/plain,*/*"}


def _parse_hhmm(value: str) -> dtime:
    hour, minute = (int(part) for part in value.split(":", 1))
    return dtime(hour, minute)


def _money(text: Any) -> float | None:
    """'$1,096.16' / '+52.20' / 'N/A' -> float or None."""
    if not isinstance(text, str):
        return float(text) if isinstance(text, (int, float)) else None
    cleaned = text.replace("$", "").replace(",", "").replace("%", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _nasdaq_pair(block: dict[str, Any]) -> tuple[float, float | None] | None:
    """(price, the close it is changed against) out of one Nasdaq data block."""
    price = _money(block.get("lastSalePrice"))
    if price is None:
        return None
    change = _money(block.get("netChange"))
    return price, round(price - change, 4) if change is not None else None


async def _from_nasdaq(client: httpx.AsyncClient, symbol: str) -> dict[str, Any]:
    r = await client.get(NASDAQ_URL.format(symbol=symbol), params={"assetclass": "stocks"})
    r.raise_for_status()
    data = (r.json() or {}).get("data") or {}
    status = (data.get("marketStatus") or "").strip()
    # primaryData is whatever is trading right now, and its netChange is measured
    # against the most recent close — which after the bell is *that same day's*
    # close. Reading it blindly compares a thin after-hours tick to the close it
    # just came from: on 2026-10-01 MU closed 1097.39 (+32.28, +3.03%), drifted to
    # 1092.71 by 4:29 pm ET, and the panel sat red all night on a 3% up day.
    # Outside the regular session we want the official close instead, which is
    # what secondaryData holds ("Closed at Oct 1, 2026 4:00 PM ET") changed
    # against the day before. Fully closed markets can leave secondaryData empty,
    # so primaryData stays as the fallback — there it is the close anyway.
    blocks = [("primary", data.get("primaryData") or {})]
    if "open" not in status.lower():
        blocks.insert(0, ("secondary", data.get("secondaryData") or {}))
    for which, block in blocks:
        pair = _nasdaq_pair(block)
        if pair is None:
            continue
        price, previous_close = pair
        return {"price": price, "previous_close": previous_close, "quote_time": None,
                "source": "nasdaq", "market_status": status or None, "block": which}
    raise ValueError("no lastSalePrice")


async def _from_yahoo(client: httpx.AsyncClient, symbol: str, host: str) -> dict[str, Any]:
    r = await client.get(YAHOO_URL.format(host=host, symbol=symbol),
                         params={"range": "1d", "interval": "5m"})
    r.raise_for_status()
    meta = (r.json()["chart"]["result"][0])["meta"]
    price = meta.get("regularMarketPrice")
    if price is None:
        raise ValueError("no regularMarketPrice")
    # No session trap here: regularMarketPrice is the regular session's price
    # (the close, once it has closed) and chartPreviousClose is the day before —
    # already the pair the panel wants, so both sources now agree on meaning.
    return {"price": float(price),
            "previous_close": meta.get("chartPreviousClose") or meta.get("previousClose"),
            # epoch ms of the trade, not of our poll
            "quote_time": (meta.get("regularMarketTime") or 0) * 1000 or None,
            "source": host, "market_status": meta.get("marketState")}


class StockClient:
    """Keeps the last known price and pushes it to every open panel."""

    def __init__(self, cfg: dict[str, Any], broadcast: Any):
        self.cfg = cfg
        self._broadcast = broadcast
        self.quote: dict[str, Any] | None = None
        self.last_error: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._tz = ZoneInfo(cfg.get("timezone") or "America/Boise")
        self._start = _parse_hhmm(cfg.get("start") or "07:30")
        self._end = _parse_hhmm(cfg.get("end") or "14:30")

    def start(self) -> None:
        if self._task or not self.cfg.get("symbol"):
            return
        self._task = asyncio.create_task(self._loop(), name="stock")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def trading_now(self) -> bool:
        """Weekday, inside the configured window, in house time.

        Market holidays are not special-cased: on those days the quote simply
        does not move, and the panel keeps showing the last price — which is
        the correct one.
        """
        now = datetime.now(self._tz)
        return now.weekday() < 5 and self._start <= now.time() <= self._end

    async def fetch(self) -> dict[str, Any]:
        """First source that answers wins; the others are only tried on failure."""
        symbol = self.cfg["symbol"]
        errors: list[str] = []
        async with httpx.AsyncClient(timeout=10, headers=HEADERS, follow_redirects=True) as client:
            # built lazily: a source we never reach must never be started
            sources: list[tuple[str, Any]] = [("nasdaq", lambda: _from_nasdaq(client, symbol))]
            sources += [(host, lambda h=host: _from_yahoo(client, symbol, h)) for host in YAHOO_HOSTS]
            for name, call in sources:
                try:
                    quote = await call()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    errors.append(f"{name}: {exc}")
                    continue
                quote["symbol"] = symbol
                quote["fetched"] = datetime.now(self._tz).isoformat(timespec="seconds")
                self.quote = quote
                self.last_error = None
                return quote
        raise RuntimeError("; ".join(errors))

    async def _fetch_and_publish(self) -> None:
        try:
            await self.fetch()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # every source down / rate limited / shape changed
            self.last_error = str(exc)
            LOG.warning("stock refresh failed (%s): %s", self.cfg.get("symbol"), exc)
            return  # keep showing the last good price
        await self._broadcast({"type": "stock", "quote": self.quote})

    async def _loop(self) -> None:
        await self._fetch_and_publish()
        interval = int(self.cfg.get("interval_seconds") or 60)
        while not self._stop.is_set():
            await asyncio.sleep(interval)
            # Outside market hours the price cannot move, so we do not ask —
            # unless we have never had one (sources down at startup), in which
            # case keep trying rather than leave the footer blank all night.
            if self.trading_now() or self.quote is None:
                await self._fetch_and_publish()
