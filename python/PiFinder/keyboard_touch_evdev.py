import os
import time
from typing import Optional, Tuple, Dict, Any

from evdev import InputDevice, ecodes

from PiFinder.keyboard_interface import KeyboardInterface
from PiFinder.multiproclogging import MultiprocLogging

SCREEN_W = 800
SCREEN_H = 480


# Prefer importing the authoritative button rectangles from displays.py
try:
    from PiFinder.ui.displays import HYPERPIXEL_VIRTUAL_BUTTONS as BUTTONS
except Exception:
    BUTTONS = [
        ("UP",    (591,   6, 687, 102)),
        ("LEFT",  (486, 111, 582, 207)),
        ("ENTER", (591, 111, 687, 207)),
        ("RIGHT", (696, 111, 792, 207)),
        ("MINUS", (486, 216, 582, 312)),
        ("DOWN",  (591, 216, 687, 312)),
        ("PLUS",  (696, 216, 792, 312)),
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


def _scale(val: int, vmin: int, vmax: int, out_max: int) -> int:
    if vmax == vmin:
        return 0
    x = int((val - vmin) * out_max / (vmax - vmin))
    if x < 0:
        return 0
    if x > out_max:
        return out_max
    return x


class KeyboardTouchEvdev(KeyboardInterface):
    """
    HyperPixel4 landscape notes (based on your corner tests):
      - one raw axis corresponds to screen Y
      - the other corresponds to screen X but inverted

    We auto-detect axis swap from abs spans:
      - if one span is ~480 and the other is ~800, we treat it as HyperPixel-landscape
        and map raw -> screen as:
            screen_y <- long-span axis
            screen_x <- short-span axis (inverted)
    """

    def __init__(self, keyboard_queue, dev_path: str, hold_time_s: float = 1.0):
        super().__init__()
        self.q = keyboard_queue
        self.dev = InputDevice(dev_path)
        self.hold_time_s = hold_time_s

        def _try_absinfo(code):
            try:
                return self.dev.absinfo(code)
            except Exception:
                return None

        ax = _try_absinfo(ecodes.ABS_MT_POSITION_X) or _try_absinfo(ecodes.ABS_X)
        ay = _try_absinfo(ecodes.ABS_MT_POSITION_Y) or _try_absinfo(ecodes.ABS_Y)

        self.abs_x_min, self.abs_x_max = (ax.min, ax.max) if ax else (0, SCREEN_W - 1)
        self.abs_y_min, self.abs_y_max = (ay.min, ay.max) if ay else (0, SCREEN_H - 1)

        span_x = self.abs_x_max - self.abs_x_min
        span_y = self.abs_y_max - self.abs_y_min

        # Optional user overrides
        self.rot = _env_int("PIFINDER_TOUCH_ROT", 0) % 360
        self.swapxy = _env_bool("PIFINDER_TOUCH_SWAPXY", False)
        self.invertx = _env_bool("PIFINDER_TOUCH_INVERTX", False)
        self.inverty = _env_bool("PIFINDER_TOUCH_INVERTY", False)

        # HyperPixel landscape auto-detect:
        # If spans look like one ~480 and the other ~800, then enable mapping.
        looks_like_hp = (
            (span_x <= 520 and span_y >= 700) or
            (span_y <= 520 and span_x >= 700)
        )
        self._hp_auto = looks_like_hp and _env_bool("PIFINDER_TOUCH_HYPERPIXEL_AUTO", True)

        # If enabled, decide which raw axis is "long" (maps to screen_y)
        # Your corner tests match the case where the long-span axis maps to screen_y.
        self._hp_long_is_x = self._hp_auto and (span_x >= span_y)

        # Default invert-x for HyperPixel auto mode unless user explicitly set PIFINDER_TOUCH_INVERTX
        self._hp_default_invertx = self._hp_auto and ("PIFINDER_TOUCH_INVERTX" not in os.environ)

        # Touch state
        self._x = 0
        self._y = 0
        self._touch_down = False
        self._down_t0 = 0.0
        self._down_keycode: Optional[int] = None
        self._long_sent = False

        # Multi-touch state
        self._current_slot = 0
        self._slots: Dict[int, Dict[str, Any]] = {}
        self._pending = False

        self.label_to_keycode = {
            "UP": self.UP,
            "DOWN": self.DOWN,
            "LEFT": self.LEFT,
            "RIGHT": self.RIGHT,
            "ENTER": self.SQUARE,
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

    def _ensure_slot(self, i: int) -> None:
        if i not in self._slots:
            self._slots[i] = {"x": None, "y": None, "active": False}

    def _emit(self, code: int) -> None:
        self.q.put(int(code))

    def _safe_long(self, name: str, fallback: int) -> int:
        # Avoid crashes if KeyboardInterface doesn't define a given LNG_* constant
        return getattr(self, name, fallback)

    def _apply_long(self, keycode: int) -> int:
        mapping = {
            self.LEFT:   self._safe_long("LNG_LEFT", self.LEFT),
            self.RIGHT:  self._safe_long("LNG_RIGHT", self.RIGHT),
            self.UP:     self._safe_long("LNG_UP", self.UP),
            self.DOWN:   self._safe_long("LNG_DOWN", self.DOWN),
            self.SQUARE: self._safe_long("LNG_SQUARE", self.SQUARE),
            # These may not exist in your KeyboardInterface, so we safely fall back
            self.PLUS:   self._safe_long("LNG_PLUS", self.PLUS),
            self.MINUS:  self._safe_long("LNG_MINUS", self.MINUS),
            0: self._safe_long("LNG_0", 0),
            1: self._safe_long("LNG_1", 1),
            2: self._safe_long("LNG_2", 2),
            3: self._safe_long("LNG_3", 3),
            4: self._safe_long("LNG_4", 4),
            5: self._safe_long("LNG_5", 5),
            6: self._safe_long("LNG_6", 6),
            7: self._safe_long("LNG_7", 7),
            8: self._safe_long("LNG_8", 8),
            9: self._safe_long("LNG_9", 9),
        }
        return mapping.get(keycode, keycode)

    def _raw_to_screen(self, raw_x: int, raw_y: int) -> Tuple[int, int]:
        """
        Convert raw device coords to (screen_x, screen_y) in 0..799,0..479
        using HyperPixel auto mapping if detected.
        """
        if not self._hp_auto:
            x = _scale(raw_x, self.abs_x_min, self.abs_x_max, SCREEN_W - 1)
            y = _scale(raw_y, self.abs_y_min, self.abs_y_max, SCREEN_H - 1)
            return x, y

        # HyperPixel auto:
        # long-span axis -> screen_y, short-span axis -> screen_x (inverted)
        if self._hp_long_is_x:
            # raw_x is long -> screen_y
            y = _scale(raw_x, self.abs_x_min, self.abs_x_max, SCREEN_H - 1)
            x = _scale(raw_y, self.abs_y_min, self.abs_y_max, SCREEN_W - 1)
        else:
            # raw_y is long -> screen_y
            y = _scale(raw_y, self.abs_y_min, self.abs_y_max, SCREEN_H - 1)
            x = _scale(raw_x, self.abs_x_min, self.abs_x_max, SCREEN_W - 1)

        # default invert-x to match your measured corners (right side gives smaller raw)
        if self._hp_default_invertx:
            x = (SCREEN_W - 1) - x

        return x, y

    def _apply_transform(self, x: int, y: int) -> Tuple[int, int]:
        # Optional swap (screen space)
        if self.swapxy:
            x, y = y, x

        # Rotate around 800x480
        if self.rot == 90:
            x, y = y, (SCREEN_H - 1) - x
        elif self.rot == 180:
            x, y = (SCREEN_W - 1) - x, (SCREEN_H - 1) - y
        elif self.rot == 270:
            x, y = (SCREEN_W - 1) - y, x

        # Optional inversion (after rotate)
        if self.invertx:
            x = (SCREEN_W - 1) - x
        if self.inverty:
            y = (SCREEN_H - 1) - y

        # clamp
        x = max(0, min(SCREEN_W - 1, x))
        y = max(0, min(SCREEN_H - 1, y))
        return x, y

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

    def run(self, log_queue):
        MultiprocLogging.configurer(log_queue)

        for ev in self.dev.read_loop():
            if ev.type == ecodes.EV_ABS:
                if ev.code == ecodes.ABS_MT_SLOT:
                    self._current_slot = ev.value
                    self._ensure_slot(self._current_slot)
                    self._pending = True

                elif ev.code == ecodes.ABS_MT_TRACKING_ID:
                    self._ensure_slot(self._current_slot)
                    self._slots[self._current_slot]["active"] = (ev.value != -1)
                    self._pending = True

                elif ev.code in (ecodes.ABS_MT_POSITION_X, ecodes.ABS_X):
                    self._ensure_slot(self._current_slot)
                    self._slots[self._current_slot]["x"] = ev.value
                    self._pending = True

                elif ev.code in (ecodes.ABS_MT_POSITION_Y, ecodes.ABS_Y):
                    self._ensure_slot(self._current_slot)
                    self._slots[self._current_slot]["y"] = ev.value
                    self._pending = True

            elif ev.type == ecodes.EV_SYN and ev.code == ecodes.SYN_REPORT:
                # Pick first active slot with valid coords
                chosen = None
                for s in self._slots.values():
                    if s.get("active") and s.get("x") is not None and s.get("y") is not None:
                        chosen = s
                        break

                any_active = any(s.get("active") for s in self._slots.values())

                if chosen is not None:
                    raw_x = int(chosen["x"])
                    raw_y = int(chosen["y"])
                    x0, y0 = self._raw_to_screen(raw_x, raw_y)
                    self._x, self._y = self._apply_transform(x0, y0)

                # Down/up transitions (only on SYN_REPORT)
                if any_active and not self._touch_down:
                    self._touch_down_event()
                elif (not any_active) and self._touch_down:
                    self._touch_up_event()

            # Long press check
            if self._touch_down and (self._down_keycode is not None) and (not self._long_sent):
                if time.monotonic() - self._down_t0 >= self.hold_time_s:
                    self._long_sent = True
                    self._emit(self._apply_long(self._down_keycode))


def run_keyboard(q, shared_state, log_queue, bloom_remap=False):
    dev_path = os.environ.get("PIFINDER_TOUCH_DEV", "/dev/input/by-path/platform-i2c@0-event")
    KeyboardTouchEvdev(q, dev_path=dev_path).run(log_queue)
