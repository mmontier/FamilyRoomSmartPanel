# Montierth kitchen panel

Custom dashboard for the kitchen touchscreen (`smartpanel`, Pi 4, 1920x1080).
Replaced the HA Lovelace kiosk (`/kitchen-dasher/0`) on 2026-09-19: faster,
couch-readable, tap-to-slideshow, and a sharp fullscreen front-door pop-up.

```text
N100 (192.168.123.47)
├── panel_dashboard   :8777  this app (FastAPI + static page, host network)
├── rpi_client        :9010  grandkid photo viewer (iframed as the hero tile)
├── frigate           :5000  snapshots for the camera tiles
│   └── go2rtc        :1984  *_display streams (HEVC main -> H.264 1440x1080@15, on demand)
└── homeassistant     :8123  states + forecast + pop-up trigger (websocket)

smartpanel (Pi 4)  chromium --kiosk http://192.168.123.47:8777/
                   launch line: /home/admin/.config/labwc/autostart
```

Source of truth: `/home/pi/montierth_panel` on Brad's Beelink; deployed copy at
`/home/markmontierth/panel_dashboard` on the N100 (tar over tailscale ssh).

## What is on the screen

- Header: clock/date, outdoor temp + condition + 24 h high/low, indoor temp.
- Hero: grandkid slideshow. **Tap = slideshow mode (fullscreen). It stays there**
  — no auto-return — until a tap or a swipe down. Swipe left/right = next/back.
  The mode survives a page reload (localStorage).
- House: lock / garages / shop door, status only (green ok, amber open).
- Cameras: Frigate snapshots every 2 s. Tap = live fullscreen for 3 min.
- Footer: MU share price (far left, green above yesterday's close / red below),
  hourly forecast, power / CO2 / AQI.
- **Tap a number = its last 24 hours.** Outside/inside temperature and the three
  footer metrics open a line graph with the true low (blue) and high (red) and
  when they happened; outside temperature draws a 32° line and says "froze" if
  it got there. Power also shows kWh for the last 24 h (integrated from the kW
  history) and today's kWh (`energy_today` entity). Closes on tap or after 90 s.
  Data: `GET /api/history/<entity>` (HA recorder, allowlisted, cached 60 s).
- **Top of the hour:** the whole screen becomes a big digital clock for 15 s,
  then goes back to whatever it was showing. 7 am - 10 pm only.
- **Pop-up:** `binary_sensor.entry_person_occupancy` off->on => front door live
  fullscreen for 60 s over whatever is showing (slideshow mode included), then
  back. Tap anywhere closes it early. A re-trigger while up extends it.

All of that is data in `app/config.py` — entities, cameras, triggers.

## Change something

```bash
cd /home/markmontierth/panel_dashboard
$EDITOR app/config.py            # or app/static/*
docker compose up -d --build     # code is baked into the image
```

The page notices the new build on its next websocket reconnect (the rebuild
itself drops the socket) and reloads itself — no need to touch the Pi.

## Test

```bash
curl -s http://127.0.0.1:8777/api/health                       # includes the last stock quote
curl -X POST http://127.0.0.1:8777/api/stock/refresh          # fetch the price now, any hour
curl -X POST 'http://127.0.0.1:8777/api/popup/frontdoor?seconds=20'   # pop the camera
curl -X POST 'http://127.0.0.1:8777/api/hourclock?seconds=5'          # pop the big clock
curl -X POST  http://127.0.0.1:8777/api/popup-close
```

`/api/popup/<camera>` is also the hook for anything else that should pop a
camera (Node-RED doorbell press, etc).

## Stock price

`STOCK` in `app/config.py`: symbol, label, decimals, and the window in which it
is worth asking (`start`/`end`/`timezone`, weekdays only — 07:30-14:30 Mountain
covers the 09:30-16:00 Eastern session with a little margin). Polled once a
minute inside that window and once at startup, so the footer still shows the
last close overnight, at weekends and right after a rebuild.

Two keyless sources, tried in order per fetch: Nasdaq's own quote endpoint
(`api.nasdaq.com`), then Yahoo's chart endpoint on either host. Yahoo rate-limits
by IP and will answer 429 for an hour or so once it has had enough, which is why
it is the spare and not the primary. Both feeds are delayed ~15 minutes. A failed
fetch is logged and the last good price stays on screen.

The price is printed green when it is above the previous day's close and red
when below (`--ok` / `--alert`, the same pair the metrics use). If a source
hands back no close to compare against, it stays plain white rather than
guessing a direction.

## Hourly big clock

`HOUR_CLOCK` in `app/config.py`: `enabled`, `seconds` (15), and the hours it may
fire between (`start_hour` 7 to `end_hour` 22 inclusive, 24-h, the Pi's clock).
At :00 the `#hourclock-overlay` fades in over everything for `seconds`, then
fades out — the panel underneath is untouched, so it comes back exactly as it
was, slideshow mode included.

The timer is rearmed off the wall clock after each firing (`scheduleHourClock`)
rather than run on a 3600 s interval, so it cannot drift away from :00 over a
long uptime. It is skipped while a camera is fullscreen or a history graph is
open, and a camera pop-up landing on top of it dismisses it (z-index 90 vs the
camera's 100 — someone at the door outranks the time). A tap closes it early.

`POST /api/hourclock` shows it immediately on every open panel, whatever the
hour — that is the test hook, and the way Node-RED could pop it for anything
else worth looking up for a few seconds.

## Live video path

The cameras' main streams are HEVC 2592x1944, which a Pi 4 browser cannot play.
go2rtc (inside Frigate) transcodes on the N100 iGPU only while someone is
watching; the page does WebRTC through `/api/webrtc/<stream>` (SDP proxied, so
the page is single-origin). Measured on the panel 2026-09-19: ~38% total CPU
while live, ~6% idle. While the stream connects the overlay shows Frigate's
latest frames (~4/s, low-res), so the door is visible instantly. A failed
connection, or no first frame in 30 s, => automatic reconnect loop.

Startup is ~4.5 s per camera (RTSP connect + keyframe wait + transcode spin-up);
the ~4 fps poster covers it. **If one camera is much slower, check Smart Codec on
the NVR**: on 2026-09-19 driveway took 12.8 s because `SmartEncode[0]` (channel 1)
was on, which stretched keyframes to 10 s despite GOP=12; turning it off fixed it.
The NVR (Lorex D861A8, `192.168.123.60`, Dahua CGI API, digest auth with the same
`FRIGATE_RTSP_USER`/`FRIGATE_RTSP_PASSWORD` that Frigate's compose env holds):

```bash
# inside the frigate container so the credentials never leave it
curl -s -g --digest -u "$FRIGATE_RTSP_USER:$FRIGATE_RTSP_PASSWORD" \
  'http://192.168.123.60/cgi-bin/configManager.cgi?action=getConfig&name=SmartEncode'
#   ...action=getConfig&name=Encode            (GOP / FPS / bitrate per channel)
#   ...action=setConfig&SmartEncode[0].Enable=false
```

Channel index = NVR channel - 1 (0 = driveway, 1 = front door).

If video ever stutters: lower `#width/#height` or `-r` on the `*_display`
streams in `dad_stack/frigate/config/config.yml` (Frigate restart needed).

## Secrets

`HA_TOKEN_FILE=/home/markmontierth/cecret_lake/panel_dashboard/ha_token`
(long-lived HA token, one line). Never in this repo.

## Rollback to the HA dashboard

```bash
# on smartpanel, as root
cp /home/admin/.config/labwc/autostart.bak-ha-dashboard-20260919 /home/admin/.config/labwc/autostart
pkill -f "[l]wrespawn"; pkill -f "[c]hromium/chromium"
setsid runuser -u admin -- env WAYLAND_DISPLAY=wayland-0 DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000 \
  sh /home/admin/.config/labwc/autostart >/dev/null 2>&1 < /dev/null &
```

and re-enable the HA automation `Front Door Person Display Popup`.

## Check your work visually

After any change here, rebuild, then run `~/home_config/tools/shot.py panel` and open the PNG it prints. It renders
the live page at the touchscreen's real 1920x1080 in a headless browser on this server (Playwright; installed
2026-09-20). Do not hand a display change back without looking at it. Details: `~/home_config/CLAUDE.md`.
