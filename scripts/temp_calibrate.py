"""
Thermistor calibration helper.

Reads raw ADC values from a PCF8591 connected to a thermistor in a voltage
divider, prints the corresponding resistance. Use this when collecting
(R, T) pairs at known temperatures to refit Beta and R0 for your specific
thermistor.

Procedure:
  1. Wire the thermistor as described in the README.
  2. Run this script.
  3. Submerge the probe in ice water (32°F / 0°C), wait 20s, note R.
  4. Submerge the probe in hot water at a thermometer-measured temperature,
     wait 20s, note R.
  5. Fit Beta and R0 to the two (R, T) pairs (or more for accuracy).
  6. Update BETA and R0 in app.py.
"""

import smbus2
import time

bus = smbus2.SMBus(1)
PCF8591_ADDR = 0x48
R_FIXED = 10000  # Match app.py's R_FIXED


def read_adc(channel=0):
    """Read one 8-bit sample from the PCF8591."""
    control = 0x40 | (channel & 0x03)
    bus.write_byte(PCF8591_ADDR, control)
    bus.read_byte(PCF8591_ADDR)         # discard stale
    return bus.read_byte(PCF8591_ADDR)


try:
    while True:
        adc = read_adc(0)
        if adc >= 255:
            print(f"ADC={adc:3d}  (saturated)")
        elif adc == 0:
            print(f"ADC={adc:3d}  (zero — check wiring)")
        else:
            r_therm = R_FIXED * adc / (255 - adc)
            print(f"ADC={adc:3d}  R={r_therm:8.0f}Ω")
        time.sleep(1)
except KeyboardInterrupt:
    print()
    bus.close()
