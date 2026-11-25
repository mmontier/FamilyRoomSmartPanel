"""Site config for the Montierth kitchen panel.

Everything house-specific lives here: which HA entities appear on the panel,
which cameras exist, and what makes the fullscreen camera pop up. Edit, then
`docker compose up -d --build` (code is baked into the image).
"""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def _read_token() -> str:
    token = os.getenv("HA_TOKEN", "").strip()
    if token:
        return token
    token_file = os.getenv("HA_TOKEN_FILE", "").strip()
    if token_file and Path(token_file).is_file():
        return Path(token_file).read_text().strip()
    return ""


# ---- header / weather ------------------------------------------------------
WEATHER_ENTITY = "weather.forecast_home_2"
OUTDOOR_TEMP_ENTITY = "sensor.weather_station_outdoor_temperature"
INDOOR_TEMP_ENTITY = "sensor.weather_station_indoor_temperature"

# ---- "house" status rows ---------------------------------------------------
# kind picks the wording + colour: lock | garage | door
STATUS_ITEMS = [
# No "House" heading (2026-11-21, Mark). Shown in two columns (2026-11-24): first 4 rows = left column (locks), rest = right (garages).
    {"entity": "lock.front_door_lock", "label": "Front door", "kind": "lock"},
    {"entity": "lock.mudroom_door", "label": "Mudroom door", "kind": "lock"},     # August lock
    {"entity": "lock.garage_door", "label": "Hall door", "kind": "lock"},         # August lock (HA calls it Garage Door); Mark: "Hall door"
    {"entity": "lock.patio_door", "label": "Patio door", "kind": "lock"},        # August (Yale) lock, added 2026-11-24
    {"entity": "cover.large_garage_door_door", "label": "Large garage", "kind": "garage"},
    {"entity": "cover.small_garage_door_door", "label": "Small garage", "kind": "garage"},
    {"entity": "binary_sensor.shop_garage_door_zigbee_sensor_contact", "label": "Shop garage", "kind": "door"},
]

# ---- small metric readouts -------------------------------------------------
# warn/alert thresholds colour the number amber/red; omit for plain.
# Every metric (and both header temperatures) opens a 24 h graph when tapped.
# energy_today = a kWh entity shown beside the graph; the graph also integrates
# the kW history itself for a rolling last-24-h kWh figure.
# scale multiplies the raw state (W -> kW); sub = a small second line, built in
# app.js from the extra entities listed (Tesla ones come from Brad's Beelink
# collector, see ~/home_config/tesla-energy.md).
METRICS = [
    {"entity": "sensor.tesla_solar_power", "label": "Solar", "unit": "kW", "decimals": 1, "scale": 0.001,
     "energy_today": "sensor.tesla_solar_today", "sub": "solar",
     "extra": ["sensor.tesla_solar_today"]},
    {"entity": "sensor.tesla_battery_level", "label": "Powerwall", "unit": "%", "decimals": 0,
     "title": "Powerwall charge", "sub": "powerwall",
     "extra": ["sensor.tesla_battery_power", "sensor.tesla_backup_reserve", "binary_sensor.tesla_grid_outage"]},
    {"entity": "sensor.totalusage_power_minute_average", "label": "Power", "unit": "kW", "decimals": 1,
     "energy_today": "sensor.totalusage_energy_today"},
    {"entity": "sensor.family_room_carbon_dioxide", "label": "CO2", "unit": "ppm", "decimals": 0,
     "warn": 1000, "alert": 1500},
    {"entity": "sensor.family_room_aqi", "label": "AQI", "unit": "", "decimals": 0,
     "warn": 51, "alert": 101},
]

# ---- stock ticker (footer, far left) ---------------------------------------
# One symbol, price only, from Yahoo (no key). Polled every interval_seconds,
# but only on weekdays between start and end in `timezone` — outside that the
# last price just stays on screen. decimals = how it is printed.
STOCK = {
    "symbol": "MU",
    "label": "MU",
    "decimals": 2,
    "timezone": "America/Boise",
    "start": "07:30",
    "end": "14:30",
    "interval_seconds": 60,
}

# ---- big clock -------------------------------------------------------------
# Every `every_minutes` the whole screen becomes a big digital clock for
# `seconds`, then goes back to the normal panel. Fires only for hours between
# start_hour and end_hour inclusive (24-h, house time), so the kitchen stays
# dark overnight. Skipped while a camera is fullscreen, a history graph is
# open, or a door warning is up — something deliberate is already on screen.
# A tap dismisses it early.
HOUR_CLOCK = {
    "enabled": True,
    "seconds": 15,
    "every_minutes": 30,  # 30 = on the hour and the half hour; 60 = hourly
    "start_hour": 7,      # first hour it may fire (7 = 7:00 am)
    "end_hour": 22,       # last hour it may fire (22 = 10:00 pm, so 10:30 pm is the last one)
}

# ---- overnight door warning ------------------------------------------------
# From `start` until `end`, a door that is open (or a lock that is unlocked)
# takes the whole screen over with its name in huge type, for `seconds`, then
# goes quiet again. The screen clears itself the moment the last offending door
# is shut, and a tap does NOT dismiss it - only closing the door does.
# A door is warned about once per opening: shut it and open it again and it
# warns again, but leaving it open does not re-nag.
# Watches the House rows above, so a row added there is covered automatically.
# "Offending" is the same not-ok the House list already colours amber/red, so a
# jammed lock or a garage stuck half way counts too.
DOOR_WATCH = {
    "enabled": True,
    "start": "22:30",
    "end": "06:00",    # next morning; the window wraps midnight
    "seconds": 600,    # 10 minutes
}

# ---- cameras ---------------------------------------------------------------
# name = Frigate camera name (snapshot tile); stream = go2rtc stream the Pi can
# play fullscreen (the *_display H.264 transcodes in frigate/config/config.yml).
CAMERAS = [
    {"name": "frontdoor", "label": "Front door", "stream": "frontdoor_display"},
    {"name": "driveway", "label": "Driveway", "stream": "driveway_display"},
]

# entity off->on => fullscreen camera for `seconds`, then back to whatever was up.
POPUP_TRIGGERS = [
    {"entity": "binary_sensor.entry_person_occupancy", "camera": "frontdoor", "seconds": 60},
]
# A camera opened by tapping its tile closes itself after this long.
MANUAL_CAMERA_SECONDS = 180


@dataclass(frozen=True)
class Settings:
    ha_base_url: str = os.getenv("HA_BASE_URL", "http://127.0.0.1:8123").rstrip("/")
    ha_token: str = field(default_factory=_read_token)
    frigate_base_url: str = os.getenv("FRIGATE_BASE_URL", "http://127.0.0.1:5000").rstrip("/")
    go2rtc_base_url: str = os.getenv("GO2RTC_BASE_URL", "http://127.0.0.1:1984").rstrip("/")
    # Browser-facing slideshow URL. Blank = same host the page came from, on slideshow_port.
    slideshow_url: str = os.getenv("SLIDESHOW_URL", "")
    slideshow_port: int = int(os.getenv("SLIDESHOW_PORT", "9010"))
    weather_entity: str = WEATHER_ENTITY

    @property
    def entity_ids(self) -> set[str]:
        ids = {WEATHER_ENTITY, OUTDOOR_TEMP_ENTITY, INDOOR_TEMP_ENTITY}
        ids.update(item["entity"] for item in STATUS_ITEMS)
        ids.update(item["entity"] for item in METRICS)
        ids.update(item["energy_today"] for item in METRICS if item.get("energy_today"))
        ids.update(e for item in METRICS for e in item.get("extra", []))
        ids.update(item["entity"] for item in POPUP_TRIGGERS)
        return ids

    @property
    def history_entities(self) -> set[str]:
        """Entities the page may ask /api/history for."""
        return {OUTDOOR_TEMP_ENTITY, INDOOR_TEMP_ENTITY} | {item["entity"] for item in METRICS}


SETTINGS = Settings()
