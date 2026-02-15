# Fonts class which, in its init, declares all kind of fonts which are
# used in the UI

from __future__ import annotations

from pathlib import Path
from PIL import ImageFont


def _find_repo_fonts_dir() -> Path:
    """
    Find the PiFinder 'fonts' directory regardless of current working dir.

    We search upward from this file for a sibling 'fonts' directory that contains
    the expected RobotoMono Nerd Font files.
    """
    here = Path(__file__).resolve()

    candidates = [
        here.parent.parent / "fonts",                 # .../PiFinder/fonts (if bundled in package)
        here.parent.parent.parent / "fonts",          # .../python/fonts
        here.parent.parent.parent.parent / "fonts",   # repo root /fonts
    ]

    required = {
        "RobotoMonoNerdFontMono-Bold.ttf",
        "RobotoMonoNerdFontMono-Regular.ttf",
    }

    for c in candidates:
        try:
            if c.is_dir():
                present = {p.name for p in c.iterdir() if p.is_file()}
                if required.issubset(present):
                    return c
        except OSError:
            pass

    # If not found, return first candidate so error shows resolved path clearly
    return candidates[0]


class Font:
    """
    Stores a image font object + some typographic details

    if height/width is not provided, it's calculated
    """

    def __init__(
        self,
        ttf_file: str,
        size: int,
        screen_width: int = 128,
        height: int = 0,
        width: int = 0,
    ):
        p = Path(ttf_file)

        if not p.exists():
            raise FileNotFoundError(
                f"Font file not found: {p} (resolved from '{ttf_file}')"
            )

        self.font = ImageFont.truetype(
            str(p), size, layout_engine=ImageFont.Layout.BASIC
        )

        # Calculate height/width
        # getbbox returns (x0, y0, x1, y1)
        bbox = self.font.getbbox("MMMMMMMMMM")
        calc_h = bbox[3] - bbox[1]
        calc_w = int(round((bbox[2] - bbox[0]) / 10.0))

        self.height = calc_h if height == 0 else height
        self.width = calc_w if width == 0 else width

        # Avoid divide-by-zero or zero-length lines
        self.line_length = max(1, int(screen_width / max(1, self.width)))


class Fonts:
    def __init__(
        self,
        base_size=10,
        bold_size=12,
        small_size=8,
        large_size=15,
        huge_size=35,
        screen_width=128,
    ):
        # Resolve fonts directory robustly
        font_path = _find_repo_fonts_dir()

        boldttf = font_path / "RobotoMonoNerdFontMono-Bold.ttf"
        regularttf = font_path / "RobotoMonoNerdFontMono-Regular.ttf"

        self.base = Font(str(boldttf), base_size, screen_width)
        self.bold = Font(str(boldttf), bold_size, screen_width)
        self.large = Font(str(regularttf), large_size, screen_width)
        self.small = Font(str(boldttf), small_size, screen_width)
        self.huge = Font(str(boldttf), huge_size, screen_width)

        self.icon_bold_large = Font(
            str(boldttf), int(base_size * 1.5), screen_width
        )
