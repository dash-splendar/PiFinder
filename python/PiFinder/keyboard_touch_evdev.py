import time
from evdev import InputDevice, ecodes
from PiFinder.keyboard_interface import KeyboardInterface
from PiFinder.multiproclogging import MultiprocLogging


class KeyboardTouchEvdev(KeyboardInterface):
    """
    Touchscreen -> PiFinder keycodes, using evdev (no pygame/SDL).

    Assumes screen coords are mapped to 800x480. If your device reports different
    ABS ranges, we scale.
    Right panel: x >= 480 is the button panel.
    """

    def __init__(self, q, dev_path: str, hold_time_s: float = 1.0):
        self.q = q
        self.dev = InputDevice(dev_path)
        self.hold_time_s = hold_time_s
        self.alt_mode = False

        # Determine abs ranges for scaling
        ax = self.dev.absinfo(ecodes.ABS_X) if self.dev.capabilities().get(ecodes.EV_ABS) else None
        ay = self.dev.absinfo(ecodes.ABS_Y) if self.dev.capabilities().get(ecodes.EV_ABS) else None
        self.abs_x_min, self.abs_x_max = (ax.min, ax.max) if ax else (0, 799)
        self.abs_y_min, self.abs_y_max = (ay.min, ay.max) if ay else (0, 479)

        self._build_layout()

        # touch state
        self._touch_down = False
        self._x = 0
        self._y = 0
        self._down_t0 = 0.0
        self._down_key = None
        self._long_sent = False

    def _scale_x(self, x_raw: int) -> int:
        # Map device abs range -> 0..799
        if self.abs_x_max == self.abs_x_min:
            return 0
        return int((x_raw - self.abs_x_min) * 799 / (self.abs_x_max - self.abs_x_min))

    def _scale_y(self, y_raw: int) -> int:
        # Map device abs range -> 0..479
        if self.abs_y_max == self.abs_y_min:
            return 0
        return int((y_raw - self.abs_y_min) * 479 / (self.abs_y_max - self.abs_y_min))

    def _build_layout(self):
        # simple rect helper: (x1,y1,x2,y2) inclusive-ish
        def R(x1, y1, x2, y2):
            return (x1, y1, x2, y2)

        # Right panel: x 480..799, y 0..479
        # Top ALT toggle
        self.rect_alt = R(490, 10, 790, 60)

        # D-pad area
        self.rect_up = R(600, 80, 690, 150)
        self.rect_left = R(510, 160, 600, 230)
        self.rect_square = R(600, 160, 690, 230)
        self.rect_right = R(690, 160, 790, 230)
        self.rect_down = R(600, 240, 690, 310)

        # +/- row
        self.rect_minus = R(510, 330, 645, 395)
        self.rect_plus = R(655, 330, 790, 395)

        # Numpad
        # 1 2 3
        # 4 5 6
        # 7 8 9
        #   0
        self.rect_1 = R(510, 405, 600, 470)
        self.rect_2 = R(605, 405, 695, 470)
        self.rect_3 = R(700, 405, 790, 470)

        # (You can change these later; for now we keep 1-3 only on bottom row to stay simple)
        # If you want full 0-9 keypad, tell me and I’ll give you a nicer grid.

        self.hit_map = [
            (self.rect_alt, "ALT_TOGGLE"),
            (self.rect_up, self.UP),
            (self.rect_down, self.DOWN),
            (self.rect_left, self.LEFT),
            (self.rect_right, self.RIGHT),
            (self.rect_square, self.SQUARE),
            (self.rect_plus, self.PLUS),
            (self.rect_minus, self.MINUS),
            (self.rect_1, 1),
            (self.rect_2, 2),
            (self.rect_3, 3),
        ]

    def _hit(self, x: int, y: int):
        for (x1, y1, x2, y2), key in self.hit_map:
            if x1 <= x <= x2 and y1 <= y <= y2:
                return key
        return None

    def _apply_alt(self, keycode: int) -> int:
        if not self.alt_mode:
            return keycode
        mapping = {
            self.LEFT: self.ALT_LEFT,
            self.RIGHT: self.ALT_RIGHT,
            self.UP: self.ALT_UP,
            self.DOWN: self.ALT_DOWN,
            self.SQUARE: self.ALT_SQUARE,
            self.PLUS: self.ALT_PLUS,
            self.MINUS: self.ALT_MINUS,
            0: self.ALT_0,
            1: self.ALT_1,
            2: self.ALT_2,
            3: self.ALT_3,
            4: self.ALT_4,
            5: self.ALT_5,
            6: self.ALT_6,
            7: self.ALT_7,
            8: self.ALT_8,
            9: self.ALT_9,
        }
        return mapping.get(keycode, keycode)

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

    def _emit(self, code: int):
        self.q.put(int(code))

    def run(self, log_queue):
        MultiprocLogging.configurer(log_queue)

        # Some touch devices use BTN_TOUCH, others use ABS_MT_* + SYN_REPORT
        have_btn_touch = ecodes.BTN_TOUCH in self.dev.capabilities().get(ecodes.EV_KEY, [])

        for ev in self.dev.read_loop():
            if ev.type == ecodes.EV_ABS:
                if ev.code in (ecodes.ABS_X, ecodes.ABS_MT_POSITION_X):
                    self._x = self._scale_x(ev.value)
                elif ev.code in (ecodes.ABS_Y, ecodes.ABS_MT_POSITION_Y):
                    self._y = self._scale_y(ev.value)

            elif ev.type == ecodes.EV_KEY and have_btn_touch and ev.code == ecodes.BTN_TOUCH:
                if ev.value == 1:  # down
                    self._touch_down = True
                    self._down_t0 = time.monotonic()
                    self._long_sent = False
                    hit = self._hit(self._x, self._y)
                    self._down_key = hit
                    if hit == "ALT_TOGGLE":
                        self.alt_mode = not self.alt_mode
                        self._down_key = None  # consume
                else:  # up
                    if self._touch_down and self._down_key is not None and not self._long_sent:
                        self._emit(self._apply_alt(self._down_key))
                    self._touch_down = False
                    self._down_key = None
                    self._long_sent = False

            # Long press check (cheap polling using time)
            if self._touch_down and (self._down_key is not None) and (not self._long_sent):
                if time.monotonic() - self._down_t0 >= self.hold_time_s:
                    self._long_sent = True
                    self._emit(self._apply_long(self._down_key))


def run_keyboard(q, shared_state, log_queue, bloom_remap=False):
    # Set your touch device path here or via env var
    import os
    dev_path = os.environ.get("PIFINDER_TOUCH_DEV", "/dev/input/by-path/platform-i2c@0-event")
    KeyboardTouchEvdev(q, dev_path=dev_path).run(log_queue)
