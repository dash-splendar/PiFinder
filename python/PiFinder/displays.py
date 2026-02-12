import functools
from collections import namedtuple

import numpy as np
from PIL import Image

import luma.core.device
from luma.core.interface.serial import spi
from luma.oled.device import ssd1351
from luma.lcd.device import st7789

from PiFinder.ui.fonts import Fonts


#DSEdits
try:
    import pygame
except Exception:
    pygame = None

ColorMask = namedtuple("ColorMask", ["mask", "mode"])
RED_RGB: ColorMask = ColorMask(np.array([1, 0, 0]), "RGB")
RED_BGR: ColorMask = ColorMask(np.array([0, 0, 1]), "BGR")
GREY: ColorMask = ColorMask(np.array([1, 1, 1]), "RGB")


class Colors:
    def __init__(self, color_mask: ColorMask, resolution: tuple[int, int]):
        self.color_mask = color_mask[0]
        self.mode = color_mask[1]
        self.red_image = Image.new("RGB", (resolution[0], resolution[1]), self.get(255))

    @functools.cache
    def get(self, color_intensity):
        arr = self.color_mask * color_intensity
        result = tuple(arr)
        return result

class PygameWindowDevice:
    """
    Minimal 'device' shim compatible with PiFinder's expectations:
      - .mode (string)
      - .width / .height
      - .display(PIL.Image)
      - .show() (optional)
    """
    mode = "RGB"

    def __init__(self, out_w=800, out_h=480, fullscreen=True):
        if pygame is None:
            raise RuntimeError("pygame is required for DisplayHyperpixel4 but is not installed")

        # If running without X/Wayland and you want direct framebuffer,
        # you can set these env vars in your systemd service instead:
        #   SDL_VIDEODRIVER=fbcon
        #   SDL_FBDEV=/dev/fb0
        #   SDL_NOMOUSE=0
        pygame.init()

        flags = pygame.FULLSCREEN if fullscreen else 0
        self._screen = pygame.display.set_mode((out_w, out_h), flags)
        pygame.display.set_caption("PiFinder (HyperPixel4)")
        self.width = out_w
        self.height = out_h

    def display(self, pil_image: Image.Image):
        # PiFinder passes a PIL image; convert to RGB.
        rgb = pil_image.convert("RGB")

        # Convert PIL -> pygame Surface and draw full-frame
        surf = pygame.image.frombuffer(rgb.tobytes(), rgb.size, "RGB")
        self._screen.blit(surf, (0, 0))
        pygame.display.flip()

        # Keep SDL responsive and allow touch/mouse events to flow
        pygame.event.pump()

    def show(self):
        # PiFinder may call this on wake/sleep; no-op is fine.
        return


class DisplayBase:
    resolution = (128, 128)
    color_mask = RED_RGB
    titlebar_height = 17
    base_font_size = 10
    bold_font_size = 12
    small_font_size = 8
    large_font_size = 15
    huge_font_size = 35
    device = luma.core.device.device

    def __init__(self):
        self.colors = Colors(self.color_mask, self.resolution)
        self.fonts = Fonts(
            self.base_font_size,
            self.bold_font_size,
            self.small_font_size,
            self.large_font_size,
            self.huge_font_size,
            self.resolution[0],
        )

        # calculated display params
        self.centerX = int(self.resolution[0] / 2)
        self.centerY = int(self.resolution[1] / 2)
        self.fov_res = min(self.resolution[0], self.resolution[1])

        self.resX = self.resolution[0]
        self.resY = self.resolution[1]

    def set_brightness(self, brightness: int) -> None:
        return None


class DisplayHyperpixel4(DisplayBase):
    """
    HyperPixel 4.0 display backend.

    Two modes:
      - compat (default): PiFinder renders at 128x128; we scale into 480x480 on an 800x480 panel.
      - native: PiFinder renders at 800x480 (requires UI/layout changes elsewhere).
    """

    def __init__(self, native=False, fullscreen=True):
        if native:
            self.resolution = (800, 480)
            self.titlebar_height = 40
            self.base_font_size = 24
        else:
            self.resolution = (128, 128)
            self.titlebar_height = 16
            self.base_font_size = 12

        super().__init__()

        self._native = native
        self._out_w, self._out_h = (800, 480)

        self.device = PygameWindowDevice(out_w=self._out_w, out_h=self._out_h, fullscreen=fullscreen)

        if not self._native:
            self._bg = Image.new("RGB", (self._out_w, self._out_h), (0, 0, 0))

        # Save the real device display method so we can call it without recursion
        self._orig_device_display = self.device.display

        def wrapped_display(pil_img):
            if self._native:
                self._orig_device_display(pil_img)
            else:
                self._orig_device_display(self._compose_compat_frame(pil_img))

        # Monkeypatch so existing PiFinder code can keep calling device.display(...)
        self.device.display = wrapped_display

    def show(self):
        if hasattr(self.device, "show"):
            self.device.show()

    def _compose_compat_frame(self, ui_img: Image.Image) -> Image.Image:
        content = ui_img.convert("RGB").resize((480, 480), resample=Image.NEAREST)
        frame = self._bg.copy()
        frame.paste(content, (0, 0))
        return frame

    # Optional helper: safe version that won't recurse
    def display_image(self, img: Image.Image):
        if self._native:
            self._orig_device_display(img)
        else:
            self._orig_device_display(self._compose_compat_frame(img))



class DisplayPygame_128(DisplayBase):
    resolution = (128, 128)

    def __init__(self):
        from luma.emulator.device import pygame

        # init display  (SPI hardware)
        pygame = pygame(
            width=128,
            height=128,
            rotate=0,
            mode="RGB",
            transform="scale2x",
            scale=2,
            frame_rate=60,
        )
        self.device = pygame
        super().__init__()


class DisplayPygame_320(DisplayBase):
    resolution = (320, 240)

    def __init__(self):
        from luma.emulator.device import pygame

        # init display  (SPI hardware)
        pygame = pygame(
            width=320,
            height=240,
            rotate=0,
            mode="RGB",
            frame_rate=60,
        )
        self.device = pygame
        super().__init__()


class DisplaySSD1351(DisplayBase):
    resolution = (128, 128)

    def __init__(self):
        # init display  (SPI hardware)
        serial = spi(device=0, port=0, bus_speed_hz=40000000)
        device_serial = ssd1351(serial, rotate=0, bgr=True)

        device_serial.capabilities(
            width=self.resolution[0], height=self.resolution[1], rotate=0, mode="RGB"
        )
        self.device = device_serial
        super().__init__()

    def set_brightness(self, level):
        """
        Sets oled brightness
        0-255
        """
        self.device.contrast(level)


class DisplayST7789_128(DisplayBase):
    resolution = (128, 128)

    def __init__(self):
        # init display  (SPI hardware)
        serial = spi(device=0, port=0, bus_speed_hz=52000000)
        device_serial = st7789(serial, bgr=True)

        device_serial.capabilities(
            width=self.resolution[0], height=self.resolution[1], rotate=0, mode="RGB"
        )
        self.device = device_serial
        super().__init__()


class DisplayST7789(DisplayBase):
    resolution = (320, 240)
    titlebar_height = 22
    base_font_size = 16
    bold_font_size = 19
    small_font_size = 13
    large_font_size = 24
    huge_font_size = 70

    def __init__(self):
        # init display  (SPI hardware)
        serial = spi(device=0, port=0, bus_speed_hz=52000000)
        device_serial = st7789(serial, bgr=True)

        device_serial.capabilities(
            width=self.resolution[0], height=self.resolution[1], rotate=0, mode="RGB"
        )
        self.device = device_serial
        super().__init__()


def get_display(display_hardware: str) -> DisplayBase:
    if display_hardware == "pg_128":
        return DisplayPygame_128()

    if display_hardware == "pg_320":
        return DisplayPygame_320()

    if display_hardware == "ssd1351":
        return DisplaySSD1351()

    if display_hardware == "st7789":
        return DisplayST7789()

    if display_hardware == "hyperpixel4":
        # Start with compat mode so you don't have to refactor all UI modules yet.
        return DisplayHyperpixel4(native=False, fullscreen=True)

    else:
        print("Hardware platform not recognized")
        return DisplaySSD1351()
