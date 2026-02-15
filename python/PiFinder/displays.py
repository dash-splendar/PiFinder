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
    # "UI resolution" used by the PiFinder UI renderer.
    # For native-square rendering, this should be (N, N).
    resolution = (128, 128)

    color_mask = RED_RGB
    device = luma.core.device.device

    # Base metrics are defined for 128x128 and scaled to the current UI resolution.
    _BASE_UI = 128
    _BASE_TITLEBAR_H = 17
    _BASE_FONT = 10
    _BOLD_FONT = 12
    _SMALL_FONT = 8
    _LARGE_FONT = 15
    _HUGE_FONT = 35

    def __init__(self):
        self.resX, self.resY = self.resolution

        # Scale UI metrics relative to the smaller side (keeps things sane if non-square).
        ui_ref = min(self.resX, self.resY)
        self.ui_scale = ui_ref / float(self._BASE_UI)

        def _s(px: int, *, min_px: int = 1) -> int:
            return max(min_px, int(round(px * self.ui_scale)))

        # These become the canonical, resolution-aware metrics used elsewhere in UI code.
        self.titlebar_height = _s(self._BASE_TITLEBAR_H, min_px=10)
        self.base_font_size  = _s(self._BASE_FONT, min_px=8)
        self.bold_font_size  = _s(self._BOLD_FONT, min_px=8)
        self.small_font_size = _s(self._SMALL_FONT, min_px=6)
        self.large_font_size = _s(self._LARGE_FONT, min_px=10)
        self.huge_font_size  = _s(self._HUGE_FONT, min_px=18)

        self.colors = Colors(self.color_mask, self.resolution)
        self.fonts = Fonts(
            self.base_font_size,
            self.bold_font_size,
            self.small_font_size,
            self.large_font_size,
            self.huge_font_size,
            self.resX,
        )

        self.centerX = self.resX // 2
        self.centerY = self.resY // 2
        self.fov_res = min(self.resolution)


    def set_brightness(self, brightness: int) -> None:
        return None


class DisplayHyperpixel4(DisplayBase):
    """
    HyperPixel4 output:
      - Output framebuffer is out_w x out_h (default 800x480)
      - UI is rendered into a square ui_size x ui_size on the left (default ui_size=out_h=480)
      - Right side is reserved for virtual buttons (drawn here, but touch handling lives elsewhere)
    """

    def __init__(self, native: bool = False, fullscreen: bool = True,
                 out_w: int = 800, out_h: int = 480, ui_size: int | None = None):
        self._native = native
        self._out_w, self._out_h = out_w, out_h
        self._ui_size = int(ui_size) if ui_size is not None else int(out_h)  # 800x480 -> 480

        # The UI renderer should see a SQUARE canvas.
        if self._native:
            self.resolution = (self._ui_size, self._ui_size)   # <- key fix
        else:
            self.resolution = (128, 128)

        super().__init__()

        self.device = PygameWindowDevice(self._out_w, self._out_h, fullscreen)
        self._bg = Image.new("RGB", (self._out_w, self._out_h), (0, 0, 0))

        self._orig_device_display = self.device.display

        def wrapped_display(pil_img: Image.Image):
            # Always composite into the real output framebuffer (to add right-side buttons).
            self._orig_device_display(self._compose_frame(pil_img))

        self.device.display = wrapped_display

    def _compose_frame(self, ui_img: Image.Image) -> Image.Image:
        # Left square box
        if self._native:
            content = ui_img.convert("RGB")
            # If some UI path produced the wrong size, enforce it safely.
            if content.size != (self._ui_size, self._ui_size):
                content = content.resize((self._ui_size, self._ui_size), Image.NEAREST)
        else:
            # Compatibility: 128x128 UI -> scale up to ui_size x ui_size
            content = ui_img.convert("RGB").resize((self._ui_size, self._ui_size), Image.NEAREST)

        frame = self._bg.copy()
        frame.paste(content, (0, 0))

        draw = ImageDraw.Draw(frame)

        RED = (255, 0, 0)
        border_color = RED
        text_color = RED
        divider_color = RED

        # Scale button styling based on output height (keeps it consistent if out_h changes)
        scale = self._out_h / 480.0
        border_width = max(2, int(round(3 * scale)))
        divider_w = max(2, int(round(2 * scale)))
        font = _load_button_font(size=max(12, int(round(18 * scale))))

        # divider line between UI + button panel
        x_div = self._ui_size
        draw.line((x_div, 0, x_div, self._out_h - 1), fill=divider_color, width=divider_w)

        def draw_button(rect, label):
            x1, y1, x2, y2 = rect
            draw.rectangle(rect, outline=border_color, width=border_width)

            bbox = draw.textbbox((0, 0), label, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            tx = x1 + (x2 - x1 - tw) / 2
            ty = y1 + (y2 - y1 - th) / 2
            draw.text((tx, ty), label, fill=text_color, font=font)

        # NOTE: These rects are still in 800x480 coordinates.
        # If you want true arbitrary out_w/out_h later, we’ll generate these rects from a grid.
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
    if display_hardware == "hyperpixel4_native":
        return DisplayHyperpixel4(native=True, fullscreen=True)

    print("Hardware platform not recognized")
    return DisplaySSD1351()
