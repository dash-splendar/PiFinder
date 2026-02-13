import functools
from collections import namedtuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import luma.core.device
from luma.core.interface.serial import spi
from luma.oled.device import ssd1351
from luma.lcd.device import st7789

from PiFinder.ui.fonts import Fonts

try:
    import pygame
except Exception:
    pygame = None


# ---------------- HyperPixel4 output + virtual button layout ----------------

HYPERPIXEL_OUT_W, HYPERPIXEL_OUT_H = 800, 480

# Screen-space button rectangles (800x480 coordinates)
# Right panel is x=480..799, y=0..479
HYPERPIXEL_VIRTUAL_BUTTONS = [
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


def _load_button_font(size: int) -> ImageFont.ImageFont:
    """
    Prefer a real TTF so '2 sizes bigger' actually works.
    Falls back to PIL's default bitmap font if not available.
    """
    candidates = [
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/ttf/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/usr/share/fonts/ttf/DejaVuSans.ttf",
    ]
    for p in candidates:
        try:
            return ImageFont.truetype(p, size=size)
        except Exception:
            pass
    return ImageFont.load_default()


ColorMask = namedtuple("ColorMask", ["mask", "mode"])
RED_RGB: ColorMask = ColorMask(np.array([1, 0, 0]), "RGB")
RED_BGR: ColorMask = ColorMask(np.array([0, 0, 1]), "BGR")
GREY: ColorMask = ColorMask(np.array([1, 1, 1]), "RGB")


class Colors:
    def __init__(self, color_mask: ColorMask, resolution: tuple[int, int]):
        self.color_mask = color_mask[0]
        self.mode = color_mask[1]
        self.red_image = Image.new("RGB", resolution, self.get(255))

    @functools.cache
    def get(self, color_intensity):
        arr = self.color_mask * color_intensity
        return tuple(arr)


class PygameWindowDevice:
    mode = "RGB"

    def __init__(self, out_w=HYPERPIXEL_OUT_W, out_h=HYPERPIXEL_OUT_H, fullscreen=True):
        if pygame is None:
            raise RuntimeError("pygame required for HyperPixel4")

        pygame.init()
        flags = pygame.FULLSCREEN if fullscreen else 0
        self._screen = pygame.display.set_mode((out_w, out_h), flags)
        pygame.display.set_caption("PiFinder (HyperPixel4)")
        self.width = out_w
        self.height = out_h

    def display(self, pil_image: Image.Image):
        rgb = pil_image.convert("RGB")
        surf = pygame.image.frombuffer(rgb.tobytes(), rgb.size, "RGB")
        self._screen.blit(surf, (0, 0))
        pygame.display.flip()
        pygame.event.pump()

    def show(self):
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

        self.centerX = self.resolution[0] // 2
        self.centerY = self.resolution[1] // 2
        self.fov_res = min(self.resolution)
        self.resX, self.resY = self.resolution

    def set_brightness(self, brightness: int) -> None:
        return None


class DisplayHyperpixel4(DisplayBase):
    """
    Compatibility mode:
      - PiFinder UI renders at 128x128
      - scaled to 480x480 on the left
      - right 320px is reserved for virtual buttons
    """

    def __init__(self, native=False, fullscreen=True):
        if native:
            self.resolution = (HYPERPIXEL_OUT_W, HYPERPIXEL_OUT_H)
            self.titlebar_height = 40
            self.base_font_size = 24
        else:
            self.resolution = (128, 128)
            self.titlebar_height = 16
            self.base_font_size = 12

        super().__init__()

        self._native = native
        self._out_w, self._out_h = (HYPERPIXEL_OUT_W, HYPERPIXEL_OUT_H)
        self.device = PygameWindowDevice(self._out_w, self._out_h, fullscreen)

        if not self._native:
            self._bg = Image.new("RGB", (self._out_w, self._out_h), (0, 0, 0))

        self._orig_device_display = self.device.display

        def wrapped_display(pil_img):
            if self._native:
                self._orig_device_display(pil_img)
            else:
                self._orig_device_display(self._compose_compat_frame(pil_img))

        self.device.display = wrapped_display

    def _compose_compat_frame(self, ui_img: Image.Image) -> Image.Image:
        content = ui_img.convert("RGB").resize((480, 480), Image.NEAREST)
        frame = self._bg.copy()
        frame.paste(content, (0, 0))

        draw = ImageDraw.Draw(frame)

        # --- Style tweaks requested ---
        RED = (255, 0, 0)
        border_color = RED
        text_color = RED
        divider_color = RED
        border_width = 3  # slightly thicker to look crisp on 800x480

        # "2 sizes bigger" than the old default font: use a real TTF at 18pt
        font = _load_button_font(size=18)

        # divider line between UI + button panel
        draw.line((480, 0, 480, 479), fill=divider_color, width=2)

        def draw_button(rect, label):
            x1, y1, x2, y2 = rect
            draw.rectangle(rect, outline=border_color, width=border_width)

            bbox = draw.textbbox((0, 0), label, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            tx = x1 + (x2 - x1 - tw) / 2
            ty = y1 + (y2 - y1 - th) / 2
            draw.text((tx, ty), label, fill=text_color, font=font)

        for label, rect in HYPERPIXEL_VIRTUAL_BUTTONS:
            draw_button(rect, label)

        return frame


class DisplayPygame_128(DisplayBase):
    resolution = (128, 128)

    def __init__(self):
        from luma.emulator.device import pygame
        self.device = pygame(width=128, height=128, mode="RGB", scale=2)
        super().__init__()


class DisplayPygame_320(DisplayBase):
    resolution = (320, 240)

    def __init__(self):
        from luma.emulator.device import pygame
        self.device = pygame(width=320, height=240, mode="RGB")
        super().__init__()


class DisplaySSD1351(DisplayBase):
    resolution = (128, 128)

    def __init__(self):
        serial = spi(device=0, port=0, bus_speed_hz=40000000)
        self.device = ssd1351(serial, rotate=0, bgr=True)
        super().__init__()

    def set_brightness(self, level):
        self.device.contrast(level)


class DisplayST7789(DisplayBase):
    resolution = (320, 240)

    def __init__(self):
        serial = spi(device=0, port=0, bus_speed_hz=52000000)
        self.device = st7789(serial, bgr=True)
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
        return DisplayHyperpixel4(native=False, fullscreen=True)

    print("Hardware platform not recognized")
    return DisplaySSD1351()
