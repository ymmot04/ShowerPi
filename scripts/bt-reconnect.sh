#!/bin/bash
# Reconnect to paired, trusted Bluetooth devices that aren't currently
# connected. Runs every 30 seconds via bt-reconnect.timer.

# Phone (A2DP source - sends audio to the Pi). Find your phone's MAC with:
#   bluetoothctl devices Paired
PHONE_MAC="XX:XX:XX:XX:XX:XX"

# Amplifier / speaker (A2DP sink - Pi sends audio to it)
AMP_MAC="XX:XX:XX:XX:XX:XX"

reconnect_if_needed() {
    local mac="$1"
    if [ -z "$mac" ] || [ "$mac" = "XX:XX:XX:XX:XX:XX" ]; then
        return
    fi
    local connected
    connected=$(bluetoothctl info "$mac" 2>/dev/null | grep "Connected: yes")
    if [ -z "$connected" ]; then
        bluetoothctl connect "$mac" >/dev/null 2>&1
    fi
}

reconnect_if_needed "$PHONE_MAC"
reconnect_if_needed "$AMP_MAC"
