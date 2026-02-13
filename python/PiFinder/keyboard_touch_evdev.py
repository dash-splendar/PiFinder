import os
import time
from typing import Optional, Tuple, Dict, Any

from evdev import InputDevice, ecodes

from PiFinder.keyboard_interface import KeyboardInterface
from PiFinder.multiproclogging import MultiprocLogging

SCREEN_W = 800
SCREEN_H = 480


# Try to import the authoritative button rects from the display module so drawing + hit test match.
try:
    from PiFinder.ui.displays import HYPERPIXEL_VIRTUAL_BUTTONS as BUTTONS
except Exception:
    # Fallback: keep a local copy (should match displays.py)
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
    """
    Scale val from [vmin, vmax] -> [0, out_max], clamped.
    """
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
    Touchscreen -> PiFinder keycodes (HyperPixel4 landscape-friendly).

    HyperPixel4 in landscape often reports:
      - one axis with range ~0..479 (short)
      - the other with range ~0..799 (long)
    but those axes may be swapped relative to the screen, and one axis may be inverted.

    We:
      1) read absinfo ranges
      2) auto-detect "swapped axes" when ranges resemble 480 vs 800
      3) in that case, map raw_x->screen_y and raw_y->screen_x and default invert-x
      4) still allow env overrides:
          PIFINDER_TOUCH_ROT = 0|90|180|270
          PIFINDER_TOUCH_SWAPXY = 0/1     (applies AFTER raw->screen mapping)
          PIFINDER_TOUCH_INVERTX = 0/1
          PIFINDER_TOUCH_INVERTY = 0/1
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

        # Raw ranges from device (what the driver reports)
        self.raw_x_min, self.raw_x_max = (ax.min, ax.max) if ax else (0, SCREEN_W - 1)
        self.raw_y_min, self.raw_y_max = (ay.min, ay.max) if ay else (0, SCREEN_H - 1)

        raw_x_span = self.raw_x_max - self.raw_x_min
        raw_y_span = self.raw_y_max - self.raw_y_min

        # Heuristic: HyperPixel landscape "swap" case tends to look like spans ~800 and ~480 but swapped.
        # If one span is "long-ish" (>=700) and the other is "short-ish" (<=520), we treat it specially.
        looks_like_hyperpixel_spans = (
            (raw_x_span >= 700 and raw_y_span <= 520) or
            (raw_y_span >= 700 and raw_x_span <= 520)
        )

        # Auto mapping choice:
        # If raw_x is the LONG axis (≈800), that usually corresponds to screen_y (≈480) per your test.
        # And raw_y is SHORT (≈480), corresponds to screen_x (≈800) and is inverted.
        self._hyperpixel_swap_mapping = bool(looks_like_hyperpixel_spans and raw_x_span >= raw_y_span)

        # User overrides (post-mapping screen-space transforms)
        self.rot = _env_int("PIFINDER_TOUCH_ROT", 0) % 360
        self.swapxy = _env_bool("PIFINDER_TOUCH_SWAPXY", False)
        self.invertx = _env_bool("PIFINDER_TOUCH_INVERTX", False)
        self.inverty = _env_bool("PIFINDER_TOUCH_INVERTY", False)

        # If we detected the HyperPixel swap mapping and user didn't explicitly set invert,
        # default invert X to match your observed corners (raw_y decreases to the right).
        self._default_invertx_for_hyperpixel = self._hyperpixel_swap_mapping and ("PIFINDER_TOUCH_INVERTX" not in os.environ)

        # Touch state
        self._x = 0
        self._y = 0
        self._touch_down = False
        self._down_t0 = 0.0
        self._down_keycode: Optional[int] = None
        self._long_sent = False

        # Multi-touch slot state
        self._current_slot = 0
        self._slots: Dict[int, Dict[str, Any]] = {}  # slot -> {"x":int|None, "y":int|None, "active":bool}
        self._pending = False

        # Map labels -> PiFinder keycodes
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

    # ----------------- coordinate mapping -----------------

    def _raw_to_screen(self, raw_x: int, raw_y: int) -> Tuple[int, int]:
        """
        Convert raw device coords to screen pixel coords (0..799, 0..479),
        handling HyperPixel landscape swapped axes case.
        """
        if self._hyperpixel_swap_mapping:
            # raw_x (long span) -> screen_y (0..479)
            y = _scale(raw_x, self.raw_x_min, self.raw_x_max, SCREEN_H - 1)
            # raw_y (short span) -> screen_x (0..799)
            x = _scale(raw_y, self.raw_y_min, self.raw_y_max, SCREEN_W - 1)

            # default invert-x for your observed corner mapping
            if self._default_invertx_for_hyperpixel:
                x = (SCREEN_W - 1) - x
        else:
            x = _scale(raw_x, self.raw_x_min, self.raw_x_max, SCREEN_W - 1)
            y = _scale(raw_y, self.raw_y_min, self.raw_y_max, SCREEN_H - 1)

        return x, y

    def _apply_transform(self, x: int, y: int) -> Tuple[int, int]:
        # Optional swap in SCREEN space (rare, but kept for manual overrides)
        if self.swapxy:
            x, y = y, x

        # Rotate around full screen dimensions (800x480)
        if self.rot == 90:
            x, y = y, (SCREEN_H - 1) - x
        elif self.rot == 180:
            x, y = (SCREEN_W - 1) - x, (SCREEN_H - 1) - y
        elif self.rot == 270:
            x, y = (SCREEN_W - 1) - y, x

        # Optional manual inversion (post-rotate)
        if self.invertx:
            x = (SCREEN_W - 1) - x
        if self.inverty:
            y = (SCREEN_H - 1) - y

        # clamp
        if x < 0:
            x = 0
        elif x > SCREEN_W - 1:
            x = SCREEN_W - 1

        if y < 0:
            y = 0
        elif y > SCREEN_H - 1:
            y = SCREEN_H - 1

        return x, y

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
                if not self._pending:
                    # still do long-press timing checks below
                    pass
                self._pending = False

                # Choose first active slot with valid coords
                chosen = None
                for s in self._slots.values():
                    if s.get("active") and s.get("x") is not None and s.get("y") is not None:
                        chosen = s
                        break
                if chosen is not None:
                    raw_x = int(chosen["x"])
                    raw_y = int(chosen["y"])

                    x0, y0 = self._raw_to_screen(raw_x, raw_y)
                    self._x, self._y = self._apply_transform(x0, y0)

                    # Touch down/up state transitions:
                    # Prefer MT tracking id (slot active flag). If any slot active, we're "down".
                    any_active = any(s.get("active") for s in self._slots.values())
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
