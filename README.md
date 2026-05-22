# Shower Pi Controller

A Raspberry Pi controller for a shower entertainment system. The 5"
touch UI shows the time, shower air temperature, outside temperature
(current and daily high), and Bluetooth music controls with album art. The
Pi acts as a Bluetooth speaker for a phone and forwards the audio to a
Bluetooth amplifier.

## Hardware

- **Raspberry Pi 3B+** running Raspberry Pi OS Lite (64-bit) Bookworm
- **Elecrow RR050** 5" resistive HDMI touchscreen, 800×480, mounted portrait
- **PCF8591T** 8-bit I2C ADC
- **NTC thermistor** (~5kΩ at 25°C) in a voltage divider with a 10kΩ fixed
  resistor, feeding AIN0 of the PCF8591
- **External Bluetooth amplifier** I used a ZK-1002T powering a pair of HERTZ
  Dieci Series DCX-1653 6.5" Two-Way Coaxial Speakers

## Software architecture

Three layers:

1. **Frontend** — single-page web UI rendered by Chromium in kiosk mode.
   Laid out for 480×800 portrait. Talks to the backend over HTTP + WebSocket.
2. **Backend** — Python (FastAPI + uvicorn) service. Reads the temperature
   sensor, fetches outside weather (current + daily high), listens to BlueZ
   over D-Bus for media events, queries iTunes for album art, exposes a
   graceful-shutdown endpoint, and broadcasts state to the frontend.
3. **System layer** — BlueZ for the Bluetooth stack, PipeWire + WirePlumber
   for audio routing. The Pi is configured as both A2DP sink (phone → Pi)
   and A2DP source (Pi → amp) with AVRCP transport and absolute volume.

## OS configuration

- **Raspberry Pi OS Lite (64-bit), Bookworm**
- **X11 + Openbox**, no display manager — kiosk autostart from
  `~/.bash_profile` → `startx` → Openbox → Chromium in `--kiosk` mode.
- **Console autologin** via systemd drop-in at
  `/etc/systemd/system/getty@tty1.service.d/autologin.conf`.
- **Display rotated 90°**:
  - Framebuffer console rotation in `/boot/firmware/cmdline.txt`
    (`fbcon=rotate:3 video=HDMI-A-1:800x480M@60,rotate=90`).
  - X rotation in `~/.config/openbox/autostart`
    (`xrandr --output HDMI-1 --rotate left`).
- **Touch calibration** via a libinput `CalibrationMatrix` in
  `/etc/X11/xorg.conf.d/99-calibration.conf`. xrandr's rotation sets a
  Coordinate Transformation Matrix which is reset to identity in the
  Openbox autostart so the calibration matrix actually applies.
- **If not using Ethernet, then 5GHz Wi-Fi** — critical for stable Bluetooth
  audio. The Pi 3B+ shares a single radio between Wi-Fi and Bluetooth; running
  Wi-Fi on 2.4GHz causes audio stutters with the double-A2DP setup.

## UI

The screen has three regions:

1. **Top row, left card:** the current time (12-hour) with a small ⏻ icon
   above it and "Shut Down" label below. Tapping the card arms a two-tap
   shutdown confirmation — the card turns red and the label changes to
   "Tap again"; a second tap within 3 seconds triggers graceful shutdown,
   otherwise it disarms.
2. **Top row, right card:** temperature display. Defaults to outside
   weather showing `current→high°` (e.g., `64→78°`) with a ☁️ icon. Tap
   to toggle to shower water temperature (one big number) with a 🚿 icon.
   Tap again to return.
3. **Lower half:** Bluetooth media — album art, title/artist/album, and
   transport controls (previous, play/pause, next) plus volume controls.

## Repository layout

```
shower-pi-controller/
├── README.md                          Full project documentation
├── LICENSE                            MIT
├── .gitignore
├── install.sh                         One-shot installer for a fresh Pi
├── app/
│   ├── app.py                         FastAPI backend
│   ├── requirements.txt               Python deps
│   └── static/
│       └── index.html                 Frontend (480×800 portrait)
├── scripts/
│   ├── bt-reconnect.sh                Periodic BT reconnect → /usr/local/bin/
│   └── temp_calibrate.py              Thermistor calibration helper
└── system/
    ├── controller.service             systemd unit for backend
    ├── bt-reconnect.service           systemd unit for BT reconnect
    ├── bt-reconnect.timer             systemd timer (every 30s)
    ├── bt-class.service               pins BT class to Loudspeaker
    ├── sudoers-controller-shutdown    passwordless /sbin/shutdown
    ├── autologin.conf                 getty@tty1 autologin drop-in
    ├── bluetooth-main.conf            BlueZ config (bredr only)
    ├── 51-bluez-roles.lua             WirePlumber: a2dp_sink + a2dp_source + avrcp
    ├── 10-buffers.conf                PipeWire buffer size for stable BT
    ├── 99-calibration.conf            X11 libinput CalibrationMatrix
    ├── openbox-autostart              Rotation, CTM reset, kiosk
    ├── bash_profile                   Starts X on tty1
    ├── xinitrc                        exec openbox-session
    ├── cmdline.txt.reference          Snippet for /boot/firmware/cmdline.txt
    └── config.txt.snippet             Snippet for /boot/firmware/config.txt
```

## Quick install (fresh Pi)

After flashing Raspberry Pi OS Lite (64-bit) Bookworm via Raspberry Pi
Imager (configure hostname, SSH, Wi-Fi, and user during imaging), SSH into
the Pi and:

```sh
sudo apt update && sudo apt full-upgrade -y && sudo reboot
```

After reboot, SSH back in and:

```sh
git clone <this-repo-url> ~/shower-pi-controller
cd ~/shower-pi-controller
./install.sh
```

Then complete the manual steps the installer prints at the end (edit
cmdline.txt, append the config.txt snippet, set WEATHER_LOCATION, pair
your phone and amp, edit the BT MACs, recalibrate the thermistor if
needed, reboot).

## Hardware wiring

### PCF8591 + NTC thermistor

The PCF8591T is a bare 16-pin breakout — no onboard pull-ups or support
components — so we wire each chip pin explicitly. With the chip face up
and the dot/notch at the top-left (pin 1):

| PCF8591 pin | Function | Connect to |
|---|---|---|
| 1  | AIN0    | Voltage divider midpoint |
| 5  | A0      | GND |
| 6  | A1      | GND |
| 7  | A2      | GND |
| 8  | VSS     | GND |
| 9  | SDA     | Pi pin 3 (BCM 2) |
| 10 | SCL     | Pi pin 5 (BCM 3) |
| 12 | EXT     | GND |
| 13 | AGND    | GND |
| 14 | VREF    | Pi 3.3V |
| 16 | VDD     | Pi 3.3V |
| 2, 3, 4, 11, 15 | (unused) | leave unconnected |

Tying A0/A1/A2 to GND sets the I2C address to **0x48**.

### Voltage divider

```
Pi 3.3V ──[10kΩ fixed resistor]──┬──[NTC thermistor]── GND
                                  │
                                  └── PCF8591 pin 1 (AIN0)
```

The 10kΩ value sets the operating point near the middle of the ADC range
for shower temperatures (~30–50°C). Calibration in app.py assumes this
value — if you change R_FIXED, recalibrate.

### Touch / display

The Elecrow RR050 plugs onto the Pi GPIO header. It uses the SPI bus
(pins 19/21/23/24/26) and GPIO 25 for the touch IRQ. I2C pins (3 and 5)
remain free — that's where the PCF8591 connects.

## API endpoints

- `POST /system/shutdown` — calls `sudo shutdown -h now` (passwordless
  via `/etc/sudoers.d/controller-shutdown`)
- `POST /media/playpause`
- `POST /media/next`
- `POST /media/previous`
- `POST /media/volume/{up|down}` — adjusts AVRCP volume in 8-unit steps
  (~one iPhone notch per tap)
- `GET /` — frontend HTML
- `WS /ws` — live state broadcast (media metadata, art URL, shower temp,
  outside temp, outside high temp)

## Bluetooth pairing

### Phone (A2DP source)

```sh
bluetoothctl
[bluetooth]# power on
[bluetooth]# agent on
[bluetooth]# default-agent
[bluetooth]# discoverable yes
[bluetooth]# pairable yes
```

On your phone, open Bluetooth settings, scan, tap the Pi (hostname),
confirm. Back in bluetoothctl:

```
[bluetooth]# devices
[bluetooth]# trust <phone-mac>
[bluetooth]# exit
```

### Amplifier (A2DP sink)

Put the amp in pairing mode, then:

```sh
bluetoothctl
[bluetooth]# scan on
# wait for amp to appear in the [NEW] lines, note its MAC
[bluetooth]# scan off
[bluetooth]# pair <amp-mac>
[bluetooth]# trust <amp-mac>
[bluetooth]# connect <amp-mac>
[bluetooth]# exit
```

Verify it shows up as an audio sink:

```sh
wpctl status
```

Set it as the default sink (note the ID from `wpctl status`):

```sh
wpctl set-default <amp-sink-id>
```

### Auto-reconnect

Edit `/usr/local/bin/bt-reconnect.sh` and set `PHONE_MAC` and `AMP_MAC`
to the MACs from above. The timer runs every 30 seconds and reconnects
either device if it's offline.

## Temperature calibration

The defaults in `app.py` (`BETA = 3464`, `R0 = 4983`) were fitted to one
specific 5kΩ-class NTC thermistor. Your thermistor will likely have
slightly different characteristics.

To recalibrate:

1. Run `python3 scripts/temp_calibrate.py` with the Pi powered up and the
   sensor wired. It prints `ADC` and computed `R` (ohms) once a second.
2. Collect (R, T) pairs at known temperatures. Two is the minimum (ice
   water for the cold point, hot water with a thermometer for the warm
   point); more is better.
3. Fit Beta and R0 to your data. The Beta equation is:

   ```
   1/T = 1/T0 + (1/B) × ln(R / R0)
   ```

   where T is in Kelvin and T0 = 298.15K (25°C).

4. Update `BETA` and `R0` in `app.py` and restart the controller.

## Operating

- **Boot:** about 30–60 seconds from power-on to UI ready.
- **Service control:**
  ```sh
  sudo systemctl restart controller.service
  journalctl -u controller.service -f
  ```
- **Frontend cache:** Chromium's disk cache lives at `/tmp/chromium-cache`
  (a tmpfs that's wiped each boot), so frontend updates take effect on
  the next reboot without needing manual cache clearing.
- **Shutdown:** tap the Shut Down button (top-left card) on the UI. First
  tap arms (turns red, label "Tap again"); second tap within 3 seconds
  shuts down. When the green ACT LED stops blinking it's safe to flip the
  wall switch.

## Status

All features built and working:

- Display + rotation + touch + kiosk autostart
- FastAPI backend as a systemd service with WebSocket live state
- Bluetooth A2DP sink (phone → Pi) with auto-reconnect
- Bluetooth A2DP source (Pi → amp) with auto-reconnect
- Live track metadata + album art via BlueZ MediaPlayer1 + iTunes
- Transport controls (play/pause/next/previous)
- Volume control via AVRCP absolute volume in iPhone-friendly 8-unit steps
- Shower temperature via PCF8591 + NTC thermistor, calibrated to ±1°F
- Outside temperature + daily high via wttr.in, refreshed every 15 minutes
- Tappable temperature card toggles between Shower and Outside
- Clock with shutdown affordance in the top-left card
- Graceful shutdown via UI with two-tap confirmation

## Gotchas and troubleshooting

### Display

- **Console rotation needs `cmdline.txt`, not `config.txt`.** On Bookworm
  with the KMS driver, `display_hdmi_rotate=1` in `config.txt` is ignored
  for the framebuffer console. Use `fbcon=rotate:3` plus
  `video=HDMI-A-1:800x480M@60,rotate=90` in `/boot/firmware/cmdline.txt`
  instead. This is a single line — don't insert newlines when editing.
- **X rotation is separate from console rotation.** Once X starts,
  `cmdline.txt` rotation no longer applies. X rotation is done by
  `xrandr --output HDMI-1 --rotate left` in the Openbox autostart.

### Touchscreen

- **The Pi's X server uses libinput, not legacy evdev.** Old calibration
  syntax (`Option "Calibration" "minX maxX minY maxY"`) is silently
  ignored — must use libinput's `CalibrationMatrix` 3×3 matrix instead.
- **xinput_calibrator can produce garbage values** with libinput. If it
  outputs values in the tens of thousands or with no Y range, ignore it
  and calibrate manually by measuring raw touch coords at each corner via
  `evtest`, then computing the matrix.
- **`xrandr --rotate left` sets a Coordinate Transformation Matrix that
  overrides the libinput calibration.** Reset it to identity after rotation
  with `xinput set-prop ... "Coordinate Transformation Matrix" 1 0 0 0 1 0 0 0 1`
  in the Openbox autostart.

### I2C

- **`dtparam=i2c_arm=on` enables the bus driver but doesn't load
  `i2c-dev`.** Without `i2c-dev`, the `/dev/i2c-1` device node doesn't
  appear and `i2cdetect` fails. Add `i2c-dev` to `/etc/modules`.

### Bluetooth

- **`le-connection-abort-by-local`** when connecting from the Pi to a
  phone means BlueZ is trying LE for an audio device. Fix:
  `ControllerMode = bredr` in `/etc/bluetooth/main.conf`.
- **WirePlumber on Bookworm Lite is 0.4.x with Lua config**, not the
  newer 0.5+ `.conf` format. Custom Bluetooth role config goes in
  `/etc/wireplumber/bluetooth.lua.d/51-bluez-roles.lua`.
- **Device Class is auto-derived from registered service UUIDs.** Setting
  `Class =` in `main.conf` is overridden by BlueZ at startup. To force a
  class persistently, use `hciconfig hci0 class 0x200414` from a systemd
  service that runs after `bluetooth.service` (`bt-class.service` in
  this repo).
- **MediaTransport1 only exists when audio is actively streaming.** The
  controller looks up the transport at attach time but also on every
  volume call if it doesn't have one cached, so AVRCP volume works even
  when the controller starts before audio.
- **Both phone and amp create their own MediaTransport1 paths.** The
  phone's transport sits directly under the device path
  (`.../dev_AA_BB_../fdN`), while the Pi-side source endpoint on the amp
  sits under `.../dev_CC_DD_../sepN/fdN`. The controller filters strictly
  on `/fdN` (not `/sepN/fdN`) so it only ever controls the phone's volume.

### Audio

- **Pi 3B+ has one combined Wi-Fi/Bluetooth radio.** Running Wi-Fi on
  2.4GHz while doing double-A2DP causes severe audio stutters. Move
  Wi-Fi to 5GHz — single biggest improvement for audio stability.
  Force the band per-network:
  `sudo nmcli connection modify "MyNet" 802-11-wireless.band a`.

### Kiosk / Chromium

- **Emoji on Pi OS Lite requires `fonts-noto-color-emoji`.** Default Lite
  install has no emoji font. Additionally, the ⏻ (power symbol, U+23FB)
  used in the Shut Down card is not in the emoji font — install
  `fonts-noto-core` for it.
- **Don't wipe `~/.config/chromium` on every boot to clear cache.** It
  breaks Chromium's first-run state and can produce blank screens.
  Instead, redirect just the cache to a tmpfs:
  `--disk-cache-dir=/tmp/chromium-cache`.
- **When the kiosk shows a blank/black screen, check a laptop browser
  too.** If the laptop also shows blank, the problem is in the served
  HTML or backend, not Chromium or display config.

### Network / weather

- **`controller.service` must start after the network is online**, or the
  first weather fetch will fail with a DNS error and the next attempt
  isn't for 15 minutes. The service unit has
  `After=network-online.target` and `Wants=network-online.target` for
  exactly this reason.
- **wttr.in accepts ZIP codes, city names, and airport codes.** If a
  city name doesn't resolve, try the ZIP — it's the most reliable input
  format.

### Time

- **The Pi 3B+ has no real-time clock chip.** Time syncs via NTP once
  the network is up. After a reboot without network, the clock will show
  a wrong time until the network comes back and NTP catches up (usually
  within 30 seconds of network connectivity).

## License

MIT — see [LICENSE](LICENSE).
