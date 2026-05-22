#!/bin/bash
# Install/setup script for the Shower Pi Controller on a fresh Raspberry Pi OS
# Lite Bookworm install. Run as the target user (e.g. `showerpi`), NOT root.
# The script uses sudo where needed.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME="$(whoami)"
HOME_DIR="$HOME"

echo "==> Installing Shower Pi Controller as user: $USER_NAME"
echo "==> Repo dir: $REPO_DIR"
echo "==> Home dir: $HOME_DIR"
echo

if [ "$USER_NAME" = "root" ]; then
    echo "Run this as your normal user, not root. The script uses sudo as needed." >&2
    exit 1
fi

# ---------- packages ----------
echo "==> Installing apt packages..."
sudo apt update
sudo apt install -y --no-install-recommends \
  xserver-xorg x11-xserver-utils xinit \
  openbox \
  chromium-browser \
  unclutter \
  fonts-noto-color-emoji fonts-noto-core \
  bluez bluez-tools \
  pipewire pipewire-pulse pipewire-audio wireplumber \
  libspa-0.2-bluetooth \
  alsa-utils \
  i2c-tools python3-smbus \
  python3-pip python3-venv \
  curl git

# ---------- application ----------
echo
echo "==> Installing application to $HOME_DIR/controller..."
mkdir -p "$HOME_DIR/controller/static"
cp "$REPO_DIR/app/app.py"               "$HOME_DIR/controller/app.py"
cp "$REPO_DIR/app/static/index.html"    "$HOME_DIR/controller/static/index.html"
cp "$REPO_DIR/app/requirements.txt"     "$HOME_DIR/controller/requirements.txt"

echo "==> Creating Python virtualenv..."
python3 -m venv "$HOME_DIR/controller/.venv"
"$HOME_DIR/controller/.venv/bin/pip" install --upgrade pip
"$HOME_DIR/controller/.venv/bin/pip" install -r "$HOME_DIR/controller/requirements.txt"

# ---------- system files ----------
echo
echo "==> Installing system service files..."

# controller.service (substitute the actual username)
sudo sed "s/showerpi/$USER_NAME/g" "$REPO_DIR/system/controller.service" \
  | sudo tee /etc/systemd/system/controller.service >/dev/null

# autologin
sudo mkdir -p /etc/systemd/system/getty@tty1.service.d
sudo sed "s/showerpi/$USER_NAME/g" "$REPO_DIR/system/autologin.conf" \
  | sudo tee /etc/systemd/system/getty@tty1.service.d/autologin.conf >/dev/null

# Sudoers drop-in for passwordless shutdown
sudo sed "s/showerpi/$USER_NAME/g" "$REPO_DIR/system/sudoers-controller-shutdown" \
  | sudo tee /etc/sudoers.d/controller-shutdown >/dev/null
sudo chmod 0440 /etc/sudoers.d/controller-shutdown

# BT reconnect script
sudo cp "$REPO_DIR/scripts/bt-reconnect.sh" /usr/local/bin/bt-reconnect.sh
sudo chmod +x /usr/local/bin/bt-reconnect.sh
echo "  NOTE: edit /usr/local/bin/bt-reconnect.sh to set PHONE_MAC and AMP_MAC."

sudo cp "$REPO_DIR/system/bt-reconnect.service" /etc/systemd/system/
sudo cp "$REPO_DIR/system/bt-reconnect.timer"   /etc/systemd/system/
sudo cp "$REPO_DIR/system/bt-class.service"     /etc/systemd/system/

# Bluetooth main.conf
sudo cp "$REPO_DIR/system/bluetooth-main.conf" /etc/bluetooth/main.conf

# WirePlumber Bluetooth roles
sudo mkdir -p /etc/wireplumber/bluetooth.lua.d
sudo cp "$REPO_DIR/system/51-bluez-roles.lua" /etc/wireplumber/bluetooth.lua.d/

# PipeWire buffer config (system-wide)
sudo mkdir -p /etc/pipewire/pipewire.conf.d
sudo cp "$REPO_DIR/system/10-buffers.conf" /etc/pipewire/pipewire.conf.d/

# X11 touch calibration
sudo mkdir -p /etc/X11/xorg.conf.d
sudo cp "$REPO_DIR/system/99-calibration.conf" /etc/X11/xorg.conf.d/

# Openbox autostart, bash_profile, xinitrc
mkdir -p "$HOME_DIR/.config/openbox"
cp "$REPO_DIR/system/openbox-autostart" "$HOME_DIR/.config/openbox/autostart"
cp "$REPO_DIR/system/bash_profile"       "$HOME_DIR/.bash_profile"
cp "$REPO_DIR/system/xinitrc"            "$HOME_DIR/.xinitrc"
chmod +x "$HOME_DIR/.config/openbox/autostart"

# Add i2c-dev to /etc/modules so /dev/i2c-1 appears on boot
if ! grep -q "^i2c-dev" /etc/modules; then
    echo "==> Adding i2c-dev to /etc/modules"
    echo "i2c-dev" | sudo tee -a /etc/modules >/dev/null
fi

# ---------- enable services ----------
echo
echo "==> Enabling services..."
sudo systemctl daemon-reload
sudo systemctl enable controller.service
sudo systemctl enable bt-class.service
sudo systemctl enable bt-reconnect.timer
systemctl --user enable pipewire pipewire-pulse wireplumber

# ---------- final notes ----------
cat <<'EOF'

==> Done with automated install.

Manual steps that remain:

  1. Edit /boot/firmware/cmdline.txt — APPEND (single line):
       fbcon=rotate:3 video=HDMI-A-1:800x480M@60,rotate=90

  2. Append system/config.txt.snippet to /boot/firmware/config.txt
     (HDMI mode, I2C, SPI, touch overlay).

  3. Edit ~/controller/app.py and set WEATHER_LOCATION to your ZIP code or
     city name (currently "YOUR_ZIP_OR_CITY").

  4. Connect to a 5GHz Wi-Fi network if your Pi 3B+ supports it. Sharing
     2.4GHz between Wi-Fi and double-A2DP Bluetooth causes audio stutters.

  5. Pair your phone and amplifier with the Pi via bluetoothctl
     (see README for the procedure), then edit PHONE_MAC and AMP_MAC in
     /usr/local/bin/bt-reconnect.sh.

  6. Wire the PCF8591 + thermistor per the README. Recalibrate BETA and R0
     in ~/controller/app.py for your specific thermistor (the default
     values are fit to a 5kΩ-class NTC and may not match yours).

  7. Reboot.

EOF
