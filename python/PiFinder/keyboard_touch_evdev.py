import os
import time
from typing import Optional, Tuple

from evdev import InputDevice, ecodes

from PiFinder.keyboard_interface import KeyboardInterface
from PiFinder.multiproclogging import MultiprocLogging


# Screen-space button rectangles (800x480 coordinates), inclusive bounds.
# Right panel is x=480..799, y=0..479
BUTTONS = [
    # --- Top cluster (all same size) ---
    ("UP",    (591,   6, 687, 102)),

    ("LEFT",  (486, 111, 582, 207)),
    ("ENTER", (591, 111, 687, 207)),
    ("RIGHT", (696, 111, 792, 207)),

    ("MINUS", (486, 216, 582, 312)),
    ("DOWN",  (591, 216, 687, 312)),
    ("PLUS",  (696, 216, 792, 312)),

    # --- Bottom keypad (2 rows x 5 cols) ---
    ("0",     (487, 321, 544, 392)),
    ("1",     (549, 321, 606, 392)),
    ("2",     (611, 321, 668, 392)),
    ("3",     (673, 321, 730, 392)),
    ("4",     (735, 321, 792, 392)),

    ("5",     (487, 401, 544, 472)),
    ("6",     (549, 401, 606, 472)),
    ("7",     (611, 401, 668, 472)),
    ("8",     (673, 401, 730, 472)),
    ("9",     (735, 401, 792, 472)),
]


def _hit_test_button(x: int, y: int) -> Optional[str]:
    for label, (x1, y1, x2, y2) in BUTTONS:
        if x1 <= x <= x2 and y1 <= y <= y2:
            return label
    return None


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    v = os.environ.get(name)
    if v is None:
        return default
    try:
        return int(v)
    except Exception:
        return default


class KeyboardTouchEvdev(KeyboardInterface):
    """
    Touchscreen -> PiFinder keycodes, using evdev.

    Notes:
    - HyperPixel touch typically reports 0..799, 0..479 already.
    - We still scale from absinfo ranges to be safe.
    - Touch-down/up detection supports:
        * ABS_MT_TRACKING_ID (preferred)
        * BTN_TOUCH
        * ABS_PRESSURE fallback
    - Optional transforms (if rotation ever needed):
        PIFINDER_TOUCH_ROT = 0|90|180|270
        PIFINDER_TOUCH_SWAPXY = 0/1
        PIFINDER_TOUCH_INVERTX = 0/1
        PIFINDER_TOUCH_INVERTY = 0/1
    """

    def __init__(self, keyboard_queue, dev_path: str, hold_time_s: float = 1.0):
        super().__init__()
        self.q = keyboard_queue
        self.dev = InputDevice(dev_path)
        self.hold_time_s = hold_time_s

        # --- Robust abs range detection (do NOT rely on capabilities membership) ---
        def _try_absinfo(code):
            try:
                return self.dev.absinfo(code)
            except Exception:
                return None

        ax = _try_absinfo(ecodes.ABS_MT_POSITION_X) or _try_absinfo(ecodes.ABS_X)
        ay = _try_absinfo(ecodes.ABS_MT_POSITION_Y) or _try_absinfo(ecodes.ABS_Y)

        # If device reports exactly 0..799/0..479 (your case), this becomes identity.
        self.abs_x_min, self.abs_x_max = (ax.min, ax.max) if ax else (0, 799)
        self.abs_y_min, self.abs_y_max = (ay.min, ay.max) if ay else (0, 479)

        # --- Optional transforms ---
        self.rot = _env_int("PIFINDER_TOUCH_ROT", 0) % 360
        self.swapxy = _env_bool("PIFINDER_TOUCH_SWAPXY", False)
        self.invertx = _env_bool("PIFINDER_TOUCH_INVERTX", False)
        self.inverty = _env_bool("PIFINDER_TOUCH_INVERTY", False)

        # --- Touch state ---
        self._x = 0
        self._y = 0
        self._touch_down = False
        self._down_t0 = 0.0
        self._down_keycode: Optional[int] = None
        self._long_sent = False

        # pressure fallback
        self._pressure = 0

        # Map labels -> PiFinder keycodes
        self.label_to_keycode = {
            "UP": self.UP,
            "DOWN": self.DOWN,
            "LEFT": self.LEFT,
            "RIGHT": self.RIGHT,
            "ENTER": self.SQUARE,   # PiFinder uses SQUARE as enter/options
            "PLUS": self.PLUS,
            "MINUS": self.MINUS,
            "0": 0,
            "1": 1,
            "2": 2,
            "3": 3,
            "4": 4,
            "5": 5,
            "6": 6,
            "7": 7,
            "8": 8,
            "9": 9,
        }

    # ----------------- coordinate mapping -----------------

    def _scale_x(self, x_raw: int) -> int:
        if self.abs_x_max == self.abs_x_min:
            return 0
        x = int((x_raw - self.abs_x_min) * 799 / (self.abs_x_max - self.abs_x_min))
        return max(0, min(799, x))

    def _scale_y(self, y_raw: int) -> int:
        if self.abs_y_max == self.abs_y_min:
            return 0
        y = int((y_raw - self.abs_y_min) * 479 / (self.abs_y_max - self.abs_y_min))
        return max(0, min(479, y))

    def _apply_transform(self, x: int, y: int) -> Tuple[int, int]:
        # Swap first (if requested)
        if self.swapxy:
            x, y = y, x

        # Rotate around the full screen dimensions (800x480)
        # Coordinates are in 0..799 and 0..479
        if self.rot == 90:
            x, y = y, 479 - x
        elif self.rot == 180:
            x, y = 799 - x, 479 - y
        elif self.rot == 270:
            x, y = 799 - y, x

        # Invert after rotation
        if self.invertx:
            x = 799 - x
        if self.inverty:
            y = 479 - y

        return max(0, min(799, x)), max(0, min(479, y))

    # ----------------- key emission -----------------

    def _emit(self, code: int) -> None:
        self.q.put(int(code))

    def _apply_long(self, keycode: int) -> int:
        mapping = {
            self.LEFT: self.LNG_LEFT,
            self.RIGHT: self.LNG_RIGHT,
            self.UP: self.LNG_UP,
            self.DOWN: self.LNG_DOWN,
            self.SQUARE: self.LNG_SQUARE,
            self.PLUS: self.LNG_PLUS,
            self.MINUS: self.LNG_MINUS,
            0: self.LNG_0,
            1: self.LNG_1,
            2: self.LNG_2,
            3: self.LNG_3,
            4: self.LNG_4,
            5: self.LNG_5,
            6: self.LNG_6,
            7: self.LNG_7,
            8: self.LNG_8,
            9: self.LNG_9,
        }
        return mapping.get(keycode, keycode)

    def _touch_down_event(self) -> None:
        self._touch_down = True
        self._down_t0 = time.monotonic()
        self._long_sent = False

        label = _hit_test_button(self._x, self._y)
        self._down_keycode = self.label_to_keycode.get(label) if label else None

    def _touch_up_event(self) -> None:
        if self._touch_down and self._down_keycode is not None and not self._long_sent:
            self._emit(self._down_keycode)

        self._touch_down = False
        self._down_keycode = None
        self._long_sent = False

    # ----------------- main loop -----------------

    def run(self, log_queue):
        MultiprocLogging.configurer(log_queue)

        # We do NOT depend on capability membership checks; we just react to events we see.
        x_raw = None
        y_raw = None

        for ev in self.dev.read_loop():
            if ev.type == ecodes.EV_ABS:
                # Position updates
                if ev.code in (ecodes.ABS_MT_POSITION_X, ecodes.ABS_X):
                    x_raw = ev.value
                elif ev.code in (ecodes.ABS_MT_POSITION_Y, ecodes.ABS_Y):
                    y_raw = ev.value
                elif ev.code in (ecodes.ABS_PRESSURE,):
                    self._pressure = ev.value

                # Touch down/up via MT tracking id
                if ev.code == ecodes.ABS_MT_TRACKING_ID:
                    if ev.value >= 0 and not self._touch_down:
                        self._touch_down_event()
                    elif ev.value == -1 and self._touch_down:
                        self._touch_up_event()

                # If we have coordinates, update pixel coords
                if x_raw is not None and y_raw is not None:
                    x_px = self._scale_x(x_raw)
                    y_px = self._scale_y(y_raw)
                    self._x, self._y = self._apply_transform(x_px, y_px)

                # Fallback touch down/up using pressure (only if no tracking-id behavior)
                # Some panels will drive ABS_PRESSURE > 0 while finger is down.
                if ev.code == ecodes.ABS_PRESSURE:
                    if self._pressure > 0 and not self._touch_down:
                        self._touch_down_event()
                    elif self._pressure == 0 and self._touch_down:
                        self._touch_up_event()

            elif ev.type == ecodes.EV_KEY:
                # Touch down/up via BTN_TOUCH
                if ev.code == ecodes.BTN_TOUCH:
                    if ev.value == 1 and not self._touch_down:
                        self._touch_down_event()
                    elif ev.value == 0 and self._touch_down:
                        self._touch_up_event()

            # Long press check
            if self._touch_down and (self._down_keycode is not None) and (not self._long_sent):
                if time.monotonic() - self._down_t0 >= self.hold_time_s:
                    self._long_sent = True
                    self._emit(self._apply_long(self._down_keycode))


def run_keyboard(q, shared_state, log_queue, bloom_remap=False):
    # Use env var override, otherwise default to HyperPixel's i2c touchscreen path
    dev_path = os.environ.get("PIFINDER_TOUCH_DEV", "/dev/input/by-path/platform-i2c@0-event")
    KeyboardTouchEvdev(q, dev_path=dev_path).run(log_queue)
