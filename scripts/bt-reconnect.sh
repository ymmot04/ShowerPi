#!/bin/bash
# Keep the Pi connected to the amplifier (A2DP sink - the Pi sends audio to it).
# The phone is NOT reconnected here: connect to the Pi manually from the phone's
# Bluetooth settings. Runs every 30 seconds via bt-reconnect.timer.
#
# Find the amp's MAC with:  bluetoothctl devices Paired
AMP_MAC="XX:XX:XX:XX:XX:XX"

if [ -z "$AMP_MAC" ] || [ "$AMP_MAC" = "XX:XX:XX:XX:XX:XX" ]; then
    exit 0
fi

connected=$(bluetoothctl info "$AMP_MAC" 2>/dev/null | grep "Connected: yes")
if [ -z "$connected" ]; then
    bluetoothctl connect "$AMP_MAC" >/dev/null 2>&1
fi
