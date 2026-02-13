import os
import time
from evdev import InputDevice, ecodes

from PiFinder.keyboard_interface import KeyboardInterface
from PiFinder.multiproclogging import MultiprocLogging


class KeyboardTouchEvdev(KeyboardInterface):
    """
    Touchscreen -> PiFinder keycodes, using evdev (no pygame/SDL).

    Assumes touch coordinates map to screen space 800x480 (we scale ABS ranges).
    Button panel occupies the right side of the screen.
    """

    def __init__(self, q, dev_path: str, hold_time_s: float = 1.0):
        self.q = q
        self.dev = InputDevice(dev_path)
        self.hold_time_s = hold_time_s

        # Determine abs ranges for scaling (if device reports different ABS ranges)
        ev_abs_caps = self.dev.capabilities().get(ecodes.EV_ABS, [])
        ax = self.dev.absinfo(ecodes.ABS_X) if ecodes.ABS_X in ev_abs_caps else None
        ay = self.dev.absinfo(ecodes.ABS_Y) if ecodes.ABS_Y in ev_abs_caps else None

        # Prefer multitouch axes if present
        ax_mt = (
            self.dev.absinfo(ecodes.ABS_MT_POSITION_X)
            if ecodes.ABS_MT_POSITION_X in ev_abs_caps
            else None
        )
        ay_mt = (
            self.dev.absinfo(ecodes.ABS_MT_POSITION_Y)
            if ecodes.ABS_MT_POSITION_Y in ev_abs_caps
            else None
        )

        use_ax = ax_mt or ax
        use_ay = ay_mt or ay

        self.abs_x_min, self.abs_x_max = (
            (use_ax.min, use_ax.max) if use_ax else (0, 799)
        )
        self.abs_y_min, self.abs_y_max = (
            (use_ay.min, use_ay.max) if use_ay else (0, 479)
        )

        # Touch state
        self._x = 0
        self._y = 0
        self._touch_down = False
        self._down_t0 = 0.0
        self._down_key = None
        self._long_sent = False

        # For MT protocol: tracking id >= 0 means down; -1 means up
        self._mt_tracking_id = None

        # Build button hitboxes + mapping
        self._build_layout()

    def _scale_x(self, x_raw: int) -> int:
        # Map device abs range -> 0..799
        if self.abs_x_max == self.abs_x_min:
            return 0
        x = int((x_raw - self.abs_x_min) * 799 / (self.abs_x_max - self.abs_x_min))
        return max(0, min(799, x))

    def _scale_y(self, y_raw: int) -> int:
        # Map device abs range -> 0..479
        if self.abs_y_max == self.abs_y_min:
            return 0
        y = int((y_raw - self.abs_y_min) * 479 / (self.abs_y_max - self.abs_y_min))
        return max(0, min(479, y))

    def _build_layout(self):
        # Rect helper: (x1,y1,x2,y2)
        def R(x1, y1, x2, y2):
            return (x1, y1, x2, y2)

        # Layout matches your picture, in absolute 800x480 coordinates.
        # Right panel is x=480..799, y=0..479

        # --- Top cluster (all same size) ---
        rect_up = R(591, 6, 687, 102)

        rect_left = R(486, 111, 582, 207)
        rect_enter = R(591, 111, 687, 207)  # ENTER
        rect_right = R(696, 111, 792, 207)

        rect_minus = R(486, 216, 582, 312)
        rect_down = R(591, 216, 687, 312)
        rect_plus = R(696, 216, 792, 312)

        # --- Bottom keypad (2 rows x 5 cols) ---
        rect_0 = R(487, 321, 544, 392)
        rect_1 = R(549, 321, 606, 392)
        rect_2 = R(611, 321, 668, 392)
        rect_3 = R(673, 321, 730, 392)
        rect_4 = R(735, 321, 792, 392)

        rect_5 = R(487, 401, 544, 472)
        rect_6 = R(549, 401, 606, 472)
        rect_7 = R(611, 401, 668, 472)
        rect_8 = R(673, 401, 730, 472)
        rect_9 = R(735, 401, 792, 472)

        # Map each rect to the PiFinder keycode it should emit.
        # NOTE: PiFinder main loop treats SQUARE as the "enter/other options" key.
        self.hit_map = [
            (rect_up, self.UP),
            (rect_down, self.DOWN),
            (rect_left, self.LEFT),
            (rect_right, self.RIGHT),
            (rect_enter, self.SQUARE),  # ENTER -> SQUARE
            (rect_plus, self.PLUS),
            (rect_minus, self.MINUS),
            (rect_0, 0),
            (rect_1, 1),
            (rect_2, 2),
            (rect_3, 3),
            (rect_4, 4),
            (rect_5, 5),
            (rect_6, 6),
            (rect_7, 7),
            (rect_8, 8),
            (rect_9, 9),
        ]

    def _hit(self, x: int, y: int):
        for (x1, y1, x2, y2), key in self.hit_map:
            if x1 <= x <= x2 and y1 <= y <= y2:
                return key
        return None

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

    def _touch_down_event(self):
        self._touch_down = True
        self._down_t0 = time.monotonic()
        self._long_sent = False
        self._down_key = self._hit(self._x, self._y)

    def _touch_up_event(self):
        if self._touch_down and self._down_key is not None and not self._long_sent:
            self._emit(self._down_key)
        self._touch_down = False
        self._down_key = None
        self._long_sent = False

    def run(self, log_queue):
        MultiprocLogging.configurer(log_queue)

        ev_key_caps = self.dev.capabilities().get(ecodes.EV_KEY, [])
        have_btn_touch = ecodes.BTN_TOUCH in ev_key_caps

        ev_abs_caps = self.dev.capabilities().get(ecodes.EV_ABS, [])
        have_mt_tracking = ecodes.ABS_MT_TRACKING_ID in ev_abs_caps

        for ev in self.dev.read_loop():
            # Update position from either single-touch or multi-touch axes
            if ev.type == ecodes.EV_ABS:
                if ev.code in (ecodes.ABS_X, ecodes.ABS_MT_POSITION_X):
                    self._x = self._scale_x(ev.value)
                elif ev.code in (ecodes.ABS_Y, ecodes.ABS_MT_POSITION_Y):
                    self._y = self._scale_y(ev.value)

                # Multi-touch touch down/up detection
                if have_mt_tracking and ev.code == ecodes.ABS_MT_TRACKING_ID:
                    # >=0 means finger down, -1 means up
                    if ev.value >= 0 and not self._touch_down:
                        self._mt_tracking_id = ev.value
                        self._touch_down_event()
                    elif ev.value == -1 and self._touch_down:
                        self._mt_tracking_id = None
                        self._touch_up_event()

            # BTN_TOUCH touch down/up detection (some devices)
            elif ev.type == ecodes.EV_KEY and have_btn_touch and ev.code == ecodes.BTN_TOUCH:
                if ev.value == 1 and not self._touch_down:
                    self._touch_down_event()
                elif ev.value == 0 and self._touch_down:
                    self._touch_up_event()

            # Long press check
            if self._touch_down and (self._down_key is not None) and (not self._long_sent):
                if time.monotonic() - self._down_t0 >= self.hold_time_s:
                    self._long_sent = True
                    self._emit(self._apply_long(self._down_key))


def run_keyboard(q, shared_state, log_queue, bloom_remap=False):
    # Use env var override, otherwise default to HyperPixel's i2c touchscreen path
    dev_path = os.environ.get(
        "PIFINDER_TOUCH_DEV", "/dev/input/by-path/platform-i2c@0-event"
    )
    KeyboardTouchEvdev(q, dev_path=dev_path).run(log_queue)
