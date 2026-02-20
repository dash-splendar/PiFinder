#!/usr/bin/env python3

import sys
import pigpio

BACKLIGHT_GPIO = 19
PWM_FREQUENCY = 20000  # 20 kHz (flicker-free for displays)
PWM_RANGE = 1_000_000  # pigpio hardware PWM range


def usage():
    print("Usage: python3 set_backlight.py <0-100>")
    sys.exit(1)


def percent_to_duty(percent: int) -> int:
    """
    Convert 0–100% to pigpio 0–1_000_000 duty cycle.
    """
    return int((percent / 100.0) * PWM_RANGE)


def main():
    if len(sys.argv) != 2:
        usage()

    try:
        percent = int(sys.argv[1])
    except ValueError:
        usage()

    if percent < 0 or percent > 100:
        usage()

    pi = pigpio.pi()
    if not pi.connected:
        print("Error: pigpiod is not running.")
        sys.exit(1)

    duty = percent_to_duty(percent)

    # Apply hardware PWM
    pi.hardware_PWM(BACKLIGHT_GPIO, PWM_FREQUENCY, duty)

    print(f"Backlight set to {percent}%")

    pi.stop()


if __name__ == "__main__":
    main()
