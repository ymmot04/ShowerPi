"""
Shower Pi Controller backend.

Handles BlueZ media metadata + transport, AVRCP volume, shower temperature
(PCF8591 + NTC thermistor), outside temperature (current and daily high
from wttr.in), and graceful shutdown. Broadcasts state to the frontend
over WebSocket.
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
from pathlib import Path
from dbus_next.aio import MessageBus
from dbus_next import BusType, Variant
import asyncio
import json
import logging
import math
import time
import httpx
import smbus2

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("controller")

# ---------- configuration ----------

# Outside weather lookup via wttr.in. ZIP code, city name, or airport code.
WEATHER_LOCATION    = "YOUR_ZIP_OR_CITY"
WEATHER_REFRESH_SEC = 15 * 60

# Shower temperature: PCF8591 ADC + NTC thermistor in a voltage divider.
# Beta and R0 below are fitted from empirical calibration data; recalibrate
# for your specific thermistor by collecting (R, T) pairs and refitting.
PCF8591_ADDR = 0x48
R_FIXED      = 10000      # 10kΩ fixed resistor in the divider
BETA         = 3464       # thermistor Beta coefficient
R0           = 4983       # thermistor resistance at T0
T0_K         = 298.15     # 25°C in Kelvin
TEMP_REFRESH_SEC = 2

# AVRCP volume. iPhone has 16 internal steps; 127/16 ≈ 8 maps one tap to
# one iPhone notch.
VOLUME_STEP = 8
VOLUME_MAX  = 127

# ---------- shared state ----------

state = {
    "media": {
        "connected": False, "playing": False,
        "title": "", "artist": "", "album": "", "art_url": "",
        "duration_ms": 0, "position_ms": 0,
    },
    "temperature": {
        "shower_f": None,
        "outside_f": None,
        "outside_high_f": None,
        "outside_updated_at": None,
    },
}
clients: set[WebSocket] = set()

bt = {
    "bus": None, "player_iface": None, "player_path": None,
    "transport_props": None, "device_prefix": None,
}


async def broadcast():
    if not clients:
        return
    payload = json.dumps(state)
    dead = []
    for ws in clients:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)


# ---------- shower temperature ----------

_i2c_bus: smbus2.SMBus | None = None


def _read_adc_raw(channel: int = 0) -> int | None:
    """Read one 8-bit sample from the PCF8591. Blocking I/O — call via executor."""
    global _i2c_bus
    try:
        if _i2c_bus is None:
            _i2c_bus = smbus2.SMBus(1)
        control = 0x40 | (channel & 0x03)
        _i2c_bus.write_byte(PCF8591_ADDR, control)
        _i2c_bus.read_byte(PCF8591_ADDR)        # discard stale
        return _i2c_bus.read_byte(PCF8591_ADDR)
    except Exception as e:
        log.warning("PCF8591 read failed: %s", e)
        return None


def adc_to_fahrenheit(adc: int) -> float | None:
    """Convert an 8-bit PCF8591 reading to °F via the Beta equation."""
    if adc <= 0 or adc >= 255:
        return None
    r_therm = R_FIXED * adc / (255 - adc)
    t_k = 1.0 / (1.0/T0_K + (1.0/BETA) * math.log(r_therm/R0))
    t_c = t_k - 273.15
    return t_c * 9.0/5.0 + 32.0


async def temperature_loop():
    """Read the thermistor periodically, average to smooth ADC quantization
    jitter, broadcast on meaningful change."""
    loop = asyncio.get_event_loop()
    while True:
        samples = []
        for _ in range(5):
            adc = await loop.run_in_executor(None, _read_adc_raw, 0)
            if adc is not None:
                t = adc_to_fahrenheit(adc)
                if t is not None:
                    samples.append(t)
            await asyncio.sleep(0.05)

        if samples:
            avg = sum(samples) / len(samples)
            prev = state["temperature"]["shower_f"]
            if prev is None or abs(avg - prev) >= 0.3:
                state["temperature"]["shower_f"] = round(avg, 1)
                await broadcast()

        await asyncio.sleep(TEMP_REFRESH_SEC)


# ---------- album art ----------

_art_cache: dict[tuple[str, str], str] = {}
_art_client: httpx.AsyncClient | None = None


async def lookup_album_art(artist: str, title: str) -> str:
    if not artist or not title:
        return ""
    key = (artist.lower(), title.lower())
    if key in _art_cache:
        return _art_cache[key]

    global _art_client
    if _art_client is None:
        _art_client = httpx.AsyncClient(timeout=4.0)

    try:
        r = await _art_client.get(
            "https://itunes.apple.com/search",
            params={"term": f"{artist} {title}", "media": "music", "limit": 1},
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        url = ""
        if results:
            small = results[0].get("artworkUrl100", "")
            url = small.replace("100x100bb", "600x600bb") if small else ""
        _art_cache[key] = url
        return url
    except Exception as e:
        log.warning("Album art lookup failed: %s", e)
        return ""


async def maybe_update_art(prev_title: str, prev_artist: str):
    m = state["media"]
    if (m["title"], m["artist"]) == (prev_title, prev_artist):
        return
    url = await lookup_album_art(m["artist"], m["title"])
    m["art_url"] = url
    await broadcast()


# ---------- outside temperature ----------

async def fetch_outside_temp():
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"https://wttr.in/{WEATHER_LOCATION}",
                params={"format": "j1"},
                headers={"User-Agent": "curl/8 (shower-pi-controller)"},
            )
            r.raise_for_status()
            data = r.json()
            temp_f = float(data["current_condition"][0]["temp_F"])
            state["temperature"]["outside_f"] = temp_f
            try:
                high_f = float(data["weather"][0]["maxtempF"])
                state["temperature"]["outside_high_f"] = high_f
            except (KeyError, IndexError, ValueError):
                state["temperature"]["outside_high_f"] = None
            state["temperature"]["outside_updated_at"] = time.time()
            log.info(
                "Outside temp: %.1f°F (high %s°F) at %s",
                temp_f,
                state["temperature"]["outside_high_f"],
                WEATHER_LOCATION,
            )
            await broadcast()
    except Exception as e:
        log.warning("Weather fetch failed: %s", e)


async def weather_loop():
    while True:
        await fetch_outside_temp()
        # On failure (typically DNS race at boot), retry sooner than the
        # 15-minute steady-state refresh interval.
        if state["temperature"]["outside_f"] is None:
            await asyncio.sleep(30)
        else:
            await asyncio.sleep(WEATHER_REFRESH_SEC)


# ---------- BlueZ / MediaPlayer1 + MediaTransport1 ----------

def _v(d, key, default=""):
    if key not in d:
        return default
    v = d[key]
    return v.value if isinstance(v, Variant) else v


def apply_media_props(props):
    m = state["media"]
    if "Status" in props:
        m["playing"] = _v(props, "Status") == "playing"
    if "Position" in props:
        m["position_ms"] = int(_v(props, "Position", 0))
    if "Track" in props:
        track = _v(props, "Track", {})
        m["title"]  = _v(track, "Title",  m["title"])
        m["artist"] = _v(track, "Artist", m["artist"])
        m["album"]  = _v(track, "Album",  m["album"])
        m["duration_ms"] = int(_v(track, "Duration", m["duration_ms"]))


async def find_transport_props(device_prefix: str):
    """Scan D-Bus for a MediaTransport1 directly under the given device.

    Filters strictly on '<device_prefix>/fdN' so we never match the Pi-side
    A2DP source endpoint at '.../sepN/fdN' on the amp device.
    """
    bus = bt["bus"]
    if bus is None:
        return None
    try:
        intro_root = await bus.introspect("org.bluez", "/")
        root_obj = bus.get_proxy_object("org.bluez", "/", intro_root)
        om = root_obj.get_interface("org.freedesktop.DBus.ObjectManager")
        managed = await om.call_get_managed_objects()
        for obj_path, ifaces in managed.items():
            if obj_path.startswith(device_prefix + "/fd") and "org.bluez.MediaTransport1" in ifaces:
                intro = await bus.introspect("org.bluez", obj_path)
                tobj = bus.get_proxy_object("org.bluez", obj_path, intro)
                log.info("Found MediaTransport1 at %s", obj_path)
                return tobj.get_interface("org.freedesktop.DBus.Properties")
    except Exception as e:
        log.warning("MediaTransport1 lookup failed: %s", e)
    return None


async def attach_player(bus, path):
    log.info("Attaching MediaPlayer1 at %s", path)
    introspection = await bus.introspect("org.bluez", path)
    obj = bus.get_proxy_object("org.bluez", path, introspection)
    player = obj.get_interface("org.bluez.MediaPlayer1")
    props  = obj.get_interface("org.freedesktop.DBus.Properties")

    all_props = await props.call_get_all("org.bluez.MediaPlayer1")
    apply_media_props(all_props)
    state["media"]["connected"] = True
    await maybe_update_art("", "")

    def on_changed(interface, changed, invalidated):
        if interface != "org.bluez.MediaPlayer1":
            return
        prev_title  = state["media"]["title"]
        prev_artist = state["media"]["artist"]
        apply_media_props(changed)
        async def push():
            await broadcast()
            await maybe_update_art(prev_title, prev_artist)
        asyncio.create_task(push())

    props.on_properties_changed(on_changed)

    bt["bus"] = bus
    bt["player_iface"] = player
    bt["player_path"] = path

    device_prefix = path.rsplit("/", 1)[0]
    bt["device_prefix"] = device_prefix
    bt["transport_props"] = await find_transport_props(device_prefix)

    await broadcast()


async def detach_player():
    log.info("Detaching MediaPlayer1")
    bt["player_iface"] = None
    bt["player_path"] = None
    bt["transport_props"] = None
    bt["device_prefix"] = None
    m = state["media"]
    m["connected"] = False
    m["playing"]   = False
    m["title"] = m["artist"] = m["album"] = m["art_url"] = ""
    m["duration_ms"] = m["position_ms"] = 0
    await broadcast()


async def watch_bluez():
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

    introspection = await bus.introspect("org.bluez", "/")
    root = bus.get_proxy_object("org.bluez", "/", introspection)
    om = root.get_interface("org.freedesktop.DBus.ObjectManager")

    managed = await om.call_get_managed_objects()
    for path, ifaces in managed.items():
        if "org.bluez.MediaPlayer1" in ifaces:
            await attach_player(bus, path)
            break

    def on_added(path, ifaces):
        if "org.bluez.MediaPlayer1" in ifaces and bt["player_path"] is None:
            asyncio.create_task(attach_player(bus, path))

    def on_removed(path, ifaces):
        if "org.bluez.MediaPlayer1" in ifaces and path == bt["player_path"]:
            asyncio.create_task(detach_player())

    om.on_interfaces_added(on_added)
    om.on_interfaces_removed(on_removed)

    await asyncio.Event().wait()


# ---------- lifecycle ----------

@asynccontextmanager
async def lifespan(app: FastAPI):
    bt_task      = asyncio.create_task(watch_bluez())
    weather_task = asyncio.create_task(weather_loop())
    temp_task    = asyncio.create_task(temperature_loop())
    try:
        yield
    finally:
        bt_task.cancel()
        weather_task.cancel()
        temp_task.cancel()


app = FastAPI(lifespan=lifespan)


# ---------- endpoints ----------

@app.post("/system/shutdown")
async def system_shutdown():
    log.info("Shutdown requested via UI")
    await broadcast()
    async def do_shutdown():
        await asyncio.sleep(0.5)
        proc = await asyncio.create_subprocess_exec(
            "sudo", "-n", "/sbin/shutdown", "-h", "now"
        )
        await proc.wait()
    asyncio.create_task(do_shutdown())
    return {"ok": True}


async def _call_player(method_name):
    p = bt["player_iface"]
    if p is None:
        return {"ok": False, "error": "no player connected"}
    try:
        await getattr(p, f"call_{method_name}")()
        return {"ok": True}
    except Exception as e:
        log.exception("Player call failed")
        return {"ok": False, "error": str(e)}


@app.post("/media/playpause")
async def media_playpause():
    if state["media"]["playing"]:
        return await _call_player("pause")
    else:
        return await _call_player("play")


@app.post("/media/next")
async def media_next():
    return await _call_player("next")


@app.post("/media/previous")
async def media_previous():
    return await _call_player("previous")


@app.post("/media/volume/{direction}")
async def media_volume(direction: str):
    # On-demand transport lookup. MediaTransport1 only exists when audio is
    # streaming, so the cached reference may be stale or never populated.
    if bt["transport_props"] is None and bt["device_prefix"] is not None:
        bt["transport_props"] = await find_transport_props(bt["device_prefix"])

    props = bt["transport_props"]
    if props is None:
        return {"ok": False, "error": "no transport connected"}

    try:
        current = await props.call_get("org.bluez.MediaTransport1", "Volume")
        cur = int(current.value if hasattr(current, "value") else current)
    except Exception as e:
        log.warning("Read volume failed (clearing cache): %s", e)
        bt["transport_props"] = None
        return {"ok": False, "error": f"read failed: {e}"}

    if direction == "up":
        new = min(cur + VOLUME_STEP, VOLUME_MAX)
    elif direction == "down":
        new = max(cur - VOLUME_STEP, 0)
    else:
        return {"ok": False, "error": "direction must be up or down"}

    try:
        await props.call_set("org.bluez.MediaTransport1", "Volume", Variant("q", new))
        return {"ok": True, "volume": new}
    except Exception as e:
        log.warning("Set volume failed (clearing cache): %s", e)
        bt["transport_props"] = None
        return {"ok": False, "error": f"set failed: {e}"}


# ---------- WebSocket ----------

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    try:
        await ws.send_text(json.dumps(state))
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        clients.discard(ws)


# ---------- static ----------

STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")
