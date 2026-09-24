import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import websockets

from .config import Settings


LOG = logging.getLogger(__name__)


class HomeAssistantClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.states: dict[str, dict[str, Any]] = {}
        self.forecast: list[dict[str, Any]] = []
        self.connected = False
        self.last_error: str | None = None
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._request_id = 1
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._ws: Any = None
        self._task: asyncio.Task[None] | None = None
        self._forecast_task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        # Called with (entity_id, old_state, new_state) for every watched entity.
        self.on_change: Any = None
        # Set only once the main event loop is running so forecast service
        # calls cannot race the connect-time manual recv handshake.
        self._ready = asyncio.Event()

    def start(self) -> None:
        if self._task or not self.settings.ha_token:
            if not self.settings.ha_token:
                self.last_error = "HA_TOKEN is not configured"
            return
        self._task = asyncio.create_task(self._run(), name="ha-websocket")
        self._forecast_task = asyncio.create_task(self._forecast_loop(), name="ha-forecast")

    async def stop(self) -> None:
        self._stop.set()
        for task in (self._task, self._forecast_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def subscribe(self) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)

    def state_subset(self) -> dict[str, dict[str, Any]]:
        wanted = self.settings.entity_ids
        return {entity_id: state for entity_id, state in self.states.items() if entity_id in wanted}

    async def call_service(
        self,
        domain: str,
        service: str,
        service_data: dict[str, Any],
        return_response: bool = False,
    ) -> dict[str, Any]:
        if not self._ws or not self.connected:
            raise RuntimeError("Home Assistant WebSocket is not connected")
        payload: dict[str, Any] = {
            "type": "call_service",
            "domain": domain,
            "service": service,
            "service_data": service_data,
        }
        if return_response:
            payload["return_response"] = True
        return await self._send(payload)

    async def fetch_forecast(self) -> None:
        """Pull the hourly forecast via weather.get_forecasts and broadcast it."""
        if not self.settings.weather_entity:
            return
        result = await self.call_service(
            "weather",
            "get_forecasts",
            {"entity_id": self.settings.weather_entity, "type": "hourly"},
            return_response=True,
        )
        response = (result.get("result") or {}).get("response") or {}
        entity_block = response.get(self.settings.weather_entity) or {}
        self.forecast = entity_block.get("forecast") or []
        await self._publish({"type": "forecast", "forecast": self.forecast})

    async def _forecast_loop(self) -> None:
        while not self._stop.is_set():
            await self._ready.wait()
            try:
                await self.fetch_forecast()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOG.warning("HA forecast refresh failed: %s", exc)
                await asyncio.sleep(30)
                continue
            await asyncio.sleep(600)

    async def _run(self) -> None:
        ws_url = self.settings.ha_base_url.replace("http://", "ws://").replace("https://", "wss://") + "/api/websocket"
        while not self._stop.is_set():
            try:
                await self._connect_once(ws_url)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.connected = False
                self._ready.clear()
                self.last_error = str(exc)
                LOG.warning("HA WebSocket disconnected: %s", exc)
                await self._publish({"type": "ha_status", "connected": False, "error": self.last_error})
                await asyncio.sleep(5)

    async def _connect_once(self, ws_url: str) -> None:
        self._ready.clear()
        async with websockets.connect(ws_url, ping_interval=30, ping_timeout=20, max_size=None) as ws:
            self._ws = ws
            auth_required = json.loads(await ws.recv())
            if auth_required.get("type") != "auth_required":
                raise RuntimeError(f"Unexpected HA auth preface: {auth_required}")
            await ws.send(json.dumps({"type": "auth", "access_token": self.settings.ha_token}))
            auth_result = json.loads(await ws.recv())
            if auth_result.get("type") != "auth_ok":
                raise RuntimeError(auth_result.get("message") or "HA authentication failed")

            self.connected = True
            self.last_error = None
            await self._publish({"type": "ha_status", "connected": True, "error": None})

            states_result = await self._request_before_event_loop({"type": "get_states"})
            for state in states_result.get("result", []):
                entity_id = state.get("entity_id")
                if entity_id:
                    self.states[entity_id] = state
            await self._publish({"type": "states", "states": self.state_subset()})
            await self._request_before_event_loop({"type": "subscribe_events", "event_type": "state_changed"})

            # Handshake done and the recv loop below now resolves _send futures;
            # only now is it safe for the forecast loop to issue service calls.
            self._ready.set()

            async for raw in ws:
                message = json.loads(raw)
                msg_id = message.get("id")
                if msg_id in self._pending:
                    future = self._pending.pop(msg_id)
                    if not future.done():
                        future.set_result(message)
                    continue
                if message.get("type") == "event":
                    await self._handle_event(message.get("event") or {})

    async def _send(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._ws:
            raise RuntimeError("Home Assistant WebSocket is not connected")
        msg_id = self._request_id
        self._request_id += 1
        payload = dict(payload)
        payload["id"] = msg_id
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[msg_id] = future
        await self._ws.send(json.dumps(payload))
        result = await asyncio.wait_for(future, timeout=20)
        if not result.get("success", True):
            raise RuntimeError(json.dumps(result.get("error") or result))
        return result

    async def _request_before_event_loop(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._ws:
            raise RuntimeError("Home Assistant WebSocket is not connected")
        msg_id = self._request_id
        self._request_id += 1
        payload = dict(payload)
        payload["id"] = msg_id
        await self._ws.send(json.dumps(payload))
        while True:
            result = json.loads(await asyncio.wait_for(self._ws.recv(), timeout=20))
            if result.get("id") != msg_id:
                continue
            if not result.get("success", True):
                raise RuntimeError(json.dumps(result.get("error") or result))
            return result

    async def _handle_event(self, event: dict[str, Any]) -> None:
        data = event.get("data") or {}
        entity_id = data.get("entity_id")
        new_state = data.get("new_state")
        if not entity_id or not new_state:
            return
        self.states[entity_id] = new_state
        if entity_id in self.settings.entity_ids:
            await self._publish({"type": "state_changed", "entity_id": entity_id, "state": new_state})
            if self.on_change:
                await self.on_change(entity_id, data.get("old_state"), new_state)

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Push an out-of-band message (e.g. a camera popup) to every
        connected dashboard over the same /api/live fan-out used for HA state."""
        await self._publish(message)

    async def _publish(self, message: dict[str, Any]) -> None:
        dead: list[asyncio.Queue[dict[str, Any]]] = []
        for queue in self._subscribers:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                dead.append(queue)
        for queue in dead:
            self._subscribers.discard(queue)
