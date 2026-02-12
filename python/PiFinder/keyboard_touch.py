# python/PiFinder/keyboard_touch.py

import time
import pygame

from PiFinder.keyboard_interface import KeyboardInterface
from PiFinder.multiproclogging import MultiprocLogging


class KeyboardTouch(KeyboardInterface):
    """
    Touchscreen -> PiFinder keycode mapper.

    Layout assumptions (compat mode):
      - Left  0..479   : PiFinder UI (scaled 128->480), not used for button hit targets by default
      - Right 480..799 : Touch button panel (virtual 17 buttons + ALT toggle)

    Events:
      - Tap button -> normal key
      - Long-press (>hold_time_s) -> LNG_* variant (if available, else normal)
      - ALT toggle ON -> ALT_* variant (if available, else normal)
    """

    def __init__(self, q, hold_time_s=1.0, repeat_updown=True, repeat_delay_s=0.25):
        self.q = q
        self.hold_time_s = float(hold_time_s)

        # Optional: emulate keypad "hold repeats" for UP/DOWN like keyboard_pi.py
        self.repeat_updown = bool(repeat_updown)
        self.repeat_delay_s = float(repeat_delay_s)

        # Simple state
        self.alt_mode = False
        self._active = None  # dict with press info

        # Build button rectangles (800x480). Right panel starts at x=480.
        self._build_layout()

    def _build_layout(self):
        # Right panel geometry
        panel_x = 480
        panel_w = 320
        panel_h = 480

        # A little padding
        pad = 10
        x0 = panel_x + pad
        y0 = pad
        w = panel_w - 2 * pad

        # Top strip: ALT toggle (soft)
        alt_h = 50
        self.rect_alt = pygame.Rect(x0, y0, w, alt_h)
        y = y0 + alt_h + pad

        # D-pad block (3x3 grid) + center SQUARE
        dpad_h = 180
        cell = (w - 2 * pad) // 3
        dpad_x = x0
        dpad_y = y
        # Positions
        self.rect_up = pygame.Rect(dpad_x + cell + pad, dpad_y, cell, cell)
        self.rect_left = pygame.Rect(dpad_x, dpad_y + cell + pad, cell, cell)
        self.rect_square = pygame.Rect(dpad_x + cell + pad, dpad_y + cell + pad, cell, cell)
        self.rect_right = pygame.Rect(dpad_x + 2 * (cell + pad), dpad_y + cell + pad, cell, cell)
        self.rect_down = pygame.Rect(dpad_x + cell + pad, dpad_y + 2 * (cell + pad), cell, cell)
        y = dpad_y + dpad_h + pad

        # Plus / Minus row
        pm_h = 70
        pm_w = (w - pad) // 2
        self.rect_minus = pygame.Rect(x0, y, pm_w, pm_h)
        self.rect_plus = pygame.Rect(x0 + pm_w + pad, y, pm_w, pm_h)
        y = y + pm_h + pad

        # Numpad area: 3 columns x 4 rows (1-9 then 0 bottom-center)
        # Map in a typical keypad style:
        # 1 2 3
        # 4 5 6
        # 7 8 9
        #   0
        num_rows = 4
        num_cols = 3
        num_h = panel_h - y - pad
        key_h = (num_h - (num_rows - 1) * pad) // num_rows
        key_w = (w - (num_cols - 1) * pad) // num_cols

        # Row 1: 1 2 3
        self.rect_1 = pygame.Rect(x0 + 0 * (key_w + pad), y + 0 * (key_h + pad), key_w, key_h)
        self.rect_2 = pygame.Rect(x0 + 1 * (key_w + pad), y + 0 * (key_h + pad), key_w, key_h)
        self.rect_3 = pygame.Rect(x0 + 2 * (key_w + pad), y + 0 * (key_h + pad), key_w, key_h)
        # Row 2: 4 5 6
        self.rect_4 = pygame.Rect(x0 + 0 * (key_w + pad), y + 1 * (key_h + pad), key_w, key_h)
        self.rect_5 = pygame.Rect(x0 + 1 * (key_w + pad), y + 1 * (key_h + pad), key_w, key_h)
        self.rect_6 = pygame.Rect(x0 + 2 * (key_w + pad), y + 1 * (key_h + pad), key_w, key_h)
        # Row 3: 7 8 9
        self.rect_7 = pygame.Rect(x0 + 0 * (key_w + pad), y + 2 * (key_h + pad), key_w, key_h)
        self.rect_8 = pygame.Rect(x0 + 1 * (key_w + pad), y + 2 * (key_h + pad), key_w, key_h)
        self.rect_9 = pygame.Rect(x0 + 2 * (key_w + pad), y + 2 * (key_h + pad), key_w, key_h)
        # Row 4: 0 centered
        self.rect_0 = pygame.Rect(x0 + 1 * (key_w + pad), y + 3 * (key_h + pad), key_w, key_h)

        # Hit-test table: rect -> base keycode
        self.hit_map = [
            (self.rect_alt, "ALT_TOGGLE"),

            (self.rect_up, self.UP),
            (self.rect_down, self.DOWN),
            (self.rect_left, self.LEFT),
            (self.rect_right, self.RIGHT),
            (self.rect_square, self.SQUARE),

            (self.rect_plus, self.PLUS),
            (self.rect_minus, self.MINUS),

            (self.rect_0, 0),
            (self.rect_1, 1),
            (self.rect_2, 2),
            (self.rect_3, 3),
            (self.rect_4, 4),
            (self.rect_5, 5),
            (self.rect_6, 6),
            (self.rect_7, 7),
            (self.rect_8, 8),
            (self.rect_9, 9),
        ]

    def _hit_test(self, x, y):
        for rect, key in self.hit_map:
            if rect.collidepoint(x, y):
                return key
        return None

    def _apply_alt(self, keycode: int) -> int:
        if not self.alt_mode:
            return keycode

        # Map base key to ALT_ variant if present
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
        # Map base key to LNG_ variant if present
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

    def _emit(self, keycode: int):
        # Push to PiFinder main loop
        self.q.put(int(keycode))

    def _start_press(self, keycode: int):
        now = time.monotonic()
        self._active = {
            "keycode": int(keycode),
            "t0": now,
            "long_sent": False,
            "next_repeat": now + self.repeat_delay_s,
        }

    def _end_press(self):
        """
        On release:
          - if long already fired -> do nothing
          - else send normal (or ALT) key
        """
        if not self._active:
            return

        keycode = self._active["keycode"]
        long_sent = self._active["long_sent"]
        self._active = None

        if long_sent:
            return

        # Short tap => base/ALT
        self._emit(self._apply_alt(keycode))

    def _tick_active(self):
        """
        Called periodically while pressed:
          - if held > hold_time and not yet long_sent: emit LNG_* (respect ALT mode? typically long overrides ALT)
          - if repeating UP/DOWN: emit repeated base (ALT applied) after repeat_delay
        """
        if not self._active:
            return

        now = time.monotonic()
        keycode = self._active["keycode"]

        # Long-press
        if (not self._active["long_sent"]) and (now - self._active["t0"] >= self.hold_time_s):
            self._active["long_sent"] = True
            # For long, usually you want the long function, not ALT. If you want ALT+LONG later, we can add it.
            self._emit(self._apply_long(keycode))
            return

        # Optional repeat for UP/DOWN (like physical keypad)
        if self.repeat_updown and keycode in (self.UP, self.DOWN):
            if now >= self._active["next_repeat"]:
                self._active["next_repeat"] = now + self.repeat_delay_s
                self._emit(self._apply_alt(keycode))

    def run_keyboard(self, log_queue):
        MultiprocLogging.configurer(log_queue)

        # IMPORTANT: pygame must be initialized in the same process that reads events.
        # In your build you already initialize pygame for display; that's in a different process.
        # That's OK: this keyboard process can init pygame too just for event reads.
        pygame.init()

        # Make sure we can receive events even if we don't create a visible window here.
        # If this causes issues on your Pi, we'll switch to evdev/libinput instead.
        try:
            pygame.display.init()
            if not pygame.display.get_init():
                pygame.display.init()
        except Exception:
            pass

        # Some systems require a display mode to receive mouse/touch events.
        # We create a tiny hidden window; main display window is in the other process.
        # If this conflicts on your system, tell me and I'll switch this to evdev.
        try:
            pygame.display.set_mode((1, 1))
        except Exception:
            # If we cannot create even a tiny mode, we'll still try polling events.
            pass

        while True:
            # Pump and handle events
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    # ignore; systemd will handle restarts
                    continue

                # Touch generally arrives as mouse click events.
                if event.type == pygame.MOUSEBUTTONDOWN:
                    x, y = event.pos
                    hit = self._hit_test(x, y)
                    if hit is None:
                        continue

                    if hit == "ALT_TOGGLE":
                        self.alt_mode = not self.alt_mode
                        # Optional: provide feedback to UI later via shared_state if you want
                        continue

                    # Begin tracking press for long-press/repeat
                    self._start_press(hit)

                elif event.type == pygame.MOUSEBUTTONUP:
                    # End current press (if any)
                    self._end_press()

            # Periodic tick for long-press / repeats
            self._tick_active()

            time.sleep(0.01)


def run_keyboard(q, shared_state, log_queue, bloom_remap=False):
    # bloom_remap exists in other modules; ignore here for now.
    KeyboardTouch(q).run_keyboard(log_queue)
