-- /etc/wireplumber/bluetooth.lua.d/51-bluez-roles.lua
-- Enable A2DP both as sink (phone -> Pi) and source (Pi -> amplifier),
-- plus AVRCP for transport and absolute volume. Disable headset roles
-- so phones see this as a clean speaker device.
bluez_monitor.properties = {
  ["bluez5.roles"]            = "[ a2dp_sink a2dp_source avrcp ]",
  ["bluez5.enable-sbc-xq"]    = true,
  ["bluez5.hfphsp-backend"]   = "none",
  ["bluez5.headset-roles"]    = "[ ]",
}
