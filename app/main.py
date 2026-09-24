import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config
from .config import SETTINGS
from .ha_client import HomeAssistantClient
from .stock import StockClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
LOG = logging.getLogger("panel")
# camera tiles poll a snapshot every 2 s; per-request lines would bury everything else
logging.getLogger("httpx").setLevel(logging.WARNING)

STATIC = Path(__file__).parent / "static"
HA = HomeAssistantClient(SETTINGS)
CAMERAS = {cam["name"]: cam for cam in config.CAMERAS}
STOCK = StockClient(config.STOCK, lambda msg: HA.broadcast(msg))
HTTP: httpx.AsyncClient | None = None


async def _on_change(entity_id: str, old: dict[str, Any] | None, new: dict[str, Any]) -> None:
    """off -> on of a trigger entity pops its camera fullscreen on every panel."""
    for trig in config.POPUP_TRIGGERS:
        if trig["entity"] != entity_id:
            continue
        if new.get("state") == "on" and (old or {}).get("state") != "on":
            LOG.info("popup %s (trigger %s)", trig["camera"], entity_id)
            await HA.broadcast({"type": "camera_popup", "camera": trig["camera"],
                                "seconds": trig["seconds"], "reason": "trigger"})


@asynccontextmanager
async def lifespan(_: FastAPI):
    global HTTP
    HTTP = httpx.AsyncClient(timeout=6)
    HA.on_change = _on_change
    HA.start()
    STOCK.start()
    yield
    await STOCK.stop()
    await HA.stop()
    await HTTP.aclose()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html = (STATIC / "index.html").read_text()
    # cache-bust by mtime so a rebuild + reload always picks up new assets
    for name in ("app.js", "styles.css"):
        html = html.replace(f"/static/{name}", f"/static/{name}?v={int((STATIC / name).stat().st_mtime)}")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "ha_connected": HA.connected, "ha_error": HA.last_error,
            "stock": STOCK.quote, "stock_error": STOCK.last_error,
            "stock_trading_now": STOCK.trading_now()}


@app.get("/api/bootstrap")
def bootstrap() -> dict[str, Any]:
    return {
        "ha": {"connected": HA.connected, "error": HA.last_error},
        "states": HA.state_subset(),
        "forecast": HA.forecast,
        "stock": {"label": config.STOCK.get("label") or config.STOCK.get("symbol"),
                  "decimals": config.STOCK.get("decimals", 2), "quote": STOCK.quote},
        "entities": {
            "weather": config.WEATHER_ENTITY,
            "outdoor_temp": config.OUTDOOR_TEMP_ENTITY,
            "indoor_temp": config.INDOOR_TEMP_ENTITY,
        },
        "status_items": config.STATUS_ITEMS,
        "metrics": config.METRICS,
        "cameras": config.CAMERAS,
        "manual_camera_seconds": config.MANUAL_CAMERA_SECONDS,
        "slideshow_url": SETTINGS.slideshow_url,
        "slideshow_port": SETTINGS.slideshow_port,
        # changes when a rebuild ships new assets; the page reloads itself on it
        "build": max(int(p.stat().st_mtime) for p in STATIC.iterdir() if p.is_file()),
    }


@app.websocket("/api/live")
async def live(websocket: WebSocket) -> None:
    await websocket.accept()
    await websocket.send_text(json.dumps({"type": "states", "states": HA.state_subset()}))
    try:
        async for message in HA.subscribe():
            await websocket.send_text(json.dumps(message))
    except WebSocketDisconnect:
        return


@app.get("/api/camera/{name}/snapshot.jpg")
async def camera_snapshot(name: str, h: int = 480) -> Response:
    """Frigate's latest frame, proxied so the page only ever talks to us."""
    if name not in CAMERAS:
        raise HTTPException(404, "unknown camera")
    assert HTTP is not None
    try:
        r = await HTTP.get(f"{SETTINGS.frigate_base_url}/api/{name}/latest.jpg", params={"h": min(max(h, 120), 1080)})
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"frigate: {exc}") from exc
    return Response(r.content, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


HISTORY_BUCKETS = 240  # 24 h -> one point per 6 min: plenty for a 1600 px graph
_history_cache: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}


def _parse_ts(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


@app.get("/api/history/{entity_id}")
async def history(entity_id: str, hours: int = 24) -> dict[str, Any]:
    """Recorder history for the tap-to-graph pop-up: downsampled line, true
    min/max (from the raw samples, so a brief frost is never averaged away) and,
    for kW sensors, the energy under the curve."""
    if entity_id not in SETTINGS.history_entities:
        raise HTTPException(404, "unknown entity")
    hours = min(max(hours, 1), 72)
    cached = _history_cache.get((entity_id, hours))
    if cached and time.monotonic() - cached[0] < 60:
        return cached[1]
    assert HTTP is not None
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    try:
        r = await HTTP.get(
            f"{SETTINGS.ha_base_url}/api/history/period/{start.isoformat()}",
            params={"filter_entity_id": entity_id, "end_time": end.isoformat(),
                    "minimal_response": "", "no_attributes": ""},
            headers={"Authorization": f"Bearer {SETTINGS.ha_token}"}, timeout=20)
        r.raise_for_status()
        rows = (r.json() or [[]])[0]
    except (httpx.HTTPError, ValueError, IndexError) as exc:
        raise HTTPException(502, f"home assistant: {exc}") from exc

    start_ts, end_ts = start.timestamp(), end.timestamp()
    samples: list[tuple[float, float]] = []
    for row in rows:
        try:
            samples.append((max(_parse_ts(row["last_changed"]), start_ts), float(row["state"])))
        except (KeyError, ValueError, TypeError):
            continue  # unavailable / unknown
    out: dict[str, Any] = {"entity_id": entity_id, "start": start_ts * 1000, "end": end_ts * 1000,
                           "points": [], "min": None, "max": None, "integral_hours": None}
    if samples:
        lo, hi = min(samples, key=lambda p: p[1]), max(samples, key=lambda p: p[1])
        out["min"] = {"t": lo[0] * 1000, "v": lo[1]}
        out["max"] = {"t": hi[0] * 1000, "v": hi[1]}
        width = (end_ts - start_ts) / HISTORY_BUCKETS
        buckets: dict[int, list[tuple[float, float]]] = {}
        for t, v in samples:
            buckets.setdefault(min(int((t - start_ts) / width), HISTORY_BUCKETS - 1), []).append((t, v))
        for idx in sorted(buckets):
            pts = buckets[idx]
            out["points"].append([round(sum(t for t, _ in pts) / len(pts) * 1000),
                                  round(sum(v for _, v in pts) / len(pts), 3)])
        # the averaged line would pass under a short spike; pin it to the true extremes
        out["points"] += [[round(lo[0] * 1000), lo[1]], [round(hi[0] * 1000), hi[1]]]
        out["points"].sort()
        out["points"].append([round(end_ts * 1000), samples[-1][1]])  # a state holds until it changes
        # value x hours, each state held until the next one (kW in -> kWh out)
        edges = samples + [(end_ts, samples[-1][1])]
        out["integral_hours"] = round(sum(v * (edges[i + 1][0] - t) for i, (t, v) in enumerate(samples)) / 3600, 2)
    _history_cache[(entity_id, hours)] = (time.monotonic(), out)
    return out


@app.post("/api/webrtc/{stream}")
async def webrtc_offer(stream: str, request: Request) -> Response:
    """SDP offer -> go2rtc -> SDP answer. Proxied so the page stays single-origin
    and go2rtc's API port never has to be reachable from the panel."""
    if stream not in {cam["stream"] for cam in config.CAMERAS}:
        raise HTTPException(404, "unknown stream")
    assert HTTP is not None
    try:
        r = await HTTP.post(f"{SETTINGS.go2rtc_base_url}/api/webrtc", params={"src": stream},
                            content=await request.body(), headers={"Content-Type": "application/sdp"},
                            timeout=15)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"go2rtc: {exc}") from exc
    return Response(r.content, media_type="application/sdp")


@app.post("/api/popup/{name}")
async def popup(name: str, seconds: int = 60) -> dict[str, Any]:
    """Pop a camera from outside (Node-RED, curl, a test)."""
    if name not in CAMERAS:
        raise HTTPException(404, "unknown camera")
    await HA.broadcast({"type": "camera_popup", "camera": name, "seconds": seconds, "reason": "api"})
    return {"ok": True}


@app.post("/api/stock/refresh")
async def stock_refresh() -> dict[str, Any]:
    """Fetch the price now, whatever the hour (testing, or 'what is it doing')."""
    try:
        quote = await STOCK.fetch()
    except Exception as exc:
        raise HTTPException(502, f"stock: {exc}") from exc
    await HA.broadcast({"type": "stock", "quote": quote})
    return {"ok": True, "quote": quote}


@app.post("/api/popup-close")
async def popup_close() -> dict[str, Any]:
    await HA.broadcast({"type": "camera_close"})
    return {"ok": True}
