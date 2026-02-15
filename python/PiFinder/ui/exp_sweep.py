#!/usr/bin/python
# -*- coding:utf-8 -*-
"""
Exposure Sweep UI

Multi-step wizard for capturing exposure sweeps with reference SQM.
"""

import time
from enum import Enum
from pathlib import Path
from PiFinder import utils
from PiFinder.ui.base import UIModule


class SweepState(Enum):
    """Sweep wizard states"""

    ASK_SQM = "ask_sqm"  # Ask for reference SQM value
    CONFIRM = "confirm"  # Confirm ready to start
    CAPTURING = "capturing"  # Capturing sweep
    COMPLETE = "complete"  # Sweep done


class UIExpSweep(UIModule):
    """
    Exposure Sweep Wizard

    Steps:
    1. Ask user for reference SQM value from external meter
    2. Confirm ready to start
    3. Capture 100 image sweep
    4. Save metadata and exit
    """

    __title__ = "EXP SWEEP"
    __help_name__ = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.state = SweepState.ASK_SQM
        self.reference_sqm = None
        self.sqm_input = ""  # User input for SQM value
        self.sweep_started = False
        self.start_time = None
        self.sweep_dir = None  # Track the actual sweep directory
        self.initial_file_count = None  # Files that existed before we started
        self.total_images = 20  # Expected number of images
        self.estimated_duration = 60  # ~1 minute estimated

    def active(self):
        """Called when module becomes active"""
        self.state = SweepState.ASK_SQM
        self.update(force=True)

    def update(self, force=False):
        """Update display based on current state"""
        self.clear_screen()

        if self.state == SweepState.ASK_SQM:
            self._draw_ask_sqm()
        elif self.state == SweepState.CONFIRM:
            self._draw_confirm()
        elif self.state == SweepState.CAPTURING:
            self._draw_capturing()
        elif self.state == SweepState.COMPLETE:
            self._draw_complete()

        return self.screen_update(title_bar=True)

    def _draw_ask_sqm(self):
        """Draw SQM input screen (resolution-aware)"""
        x = self._s(10, min_px=4)
        gap = self._s(4, min_px=2)

        y = self.display_class.titlebar_height + self._s(6, min_px=2)

        # Heading
        self.draw.text(
            (x, y),
            "REFERENCE SQM",
            font=self.fonts.bold.font,
            fill=self.colors.get(255),
        )
        y += self.fonts.bold.height + self._s(8, min_px=3)

        # Prompt
        self.draw.text(
            (x, y),
            "Enter SQM from",
            font=self.fonts.base.font,
            fill=self.colors.get(192),
        )
        y += self.fonts.base.height + gap

        self.draw.text(
            (x, y),
            "external meter:",
            font=self.fonts.base.font,
            fill=self.colors.get(192),
        )
        y += self.fonts.base.height + self._s(10, min_px=4)

        # Show current input with decimal separator (XX.XX format)
        if self.sqm_input:
            if len(self.sqm_input) <= 2:
                display = self.sqm_input + "_" * (2 - len(self.sqm_input)) + "." + "__"
            else:
                display = (
                        self.sqm_input[:2]
                        + "."
                        + self.sqm_input[2:]
                        + "_" * (4 - len(self.sqm_input))
                )
        else:
            display = "__.__"

        self.draw.text(
            (x, y),
            f"SQM: {display}",
            font=self.fonts.large.font,
            fill=self.colors.get(255),
        )

        # Legend pinned to bottom
        leg_gap = self._s(2, min_px=1)
        y2 = self.display_class.resY - self.fonts.base.height - self._s(4, min_px=2)
        y1 = y2 - self.fonts.base.height - leg_gap

        self.draw.text(
            (x, y1),
            "0-9: Enter  -: Del",
            font=self.fonts.base.font,
            fill=self.colors.get(128),
        )
        self.draw.text(
            (x, y2),
            f"{self._SQUARE_}: OK  0: Skip",
            font=self.fonts.base.font,
            fill=self.colors.get(192),
        )

    def _draw_confirm(self):
        """Draw confirmation screen (resolution-aware)"""
        x = self._s(10, min_px=4)
        gap = self._s(4, min_px=2)

        y = self.display_class.titlebar_height + self._s(8, min_px=3)

        self.draw.text(
            (x, y),
            "READY?",
            font=self.fonts.bold.font,
            fill=self.colors.get(255),
        )
        y += self.fonts.bold.height + self._s(10, min_px=4)

        if self.reference_sqm:
            self.draw.text(
                (x, y),
                f"Ref SQM: {self.reference_sqm:.2f}",
                font=self.fonts.base.font,
                fill=self.colors.get(192),
            )
        else:
            self.draw.text(
                (x, y),
                "No reference SQM",
                font=self.fonts.base.font,
                fill=self.colors.get(128),
            )

        y += self.fonts.base.height + self._s(8, min_px=3)

        self.draw.text(
            (x, y),
            f"{self.total_images} images",
            font=self.fonts.base.font,
            fill=self.colors.get(192),
        )
        y += self.fonts.base.height + gap

        self.draw.text(
            (x, y),
            "~1 minute",
            font=self.fonts.base.font,
            fill=self.colors.get(192),
        )

        # Legend pinned to bottom
        y_leg = self.display_class.resY - self.fonts.base.height - self._s(4, min_px=2)
        self.draw.text(
            (x, y_leg),
            f"{self._SQUARE_}: START  0: CANCEL",
            font=self.fonts.base.font,
            fill=self.colors.get(192),
        )

    def _draw_capturing(self):
        """Draw capture progress (resolution-aware)"""
        if not self.sweep_started:
            self.sweep_started = True
            self.start_time = time.time()
            cmd = f"capture_exp_sweep:{self.reference_sqm if self.reference_sqm else 0.0}"
            self.command_queues["camera"].put(cmd)
            time.sleep(0.2)

        x = self._s(10, min_px=4)
        gap = self._s(4, min_px=2)

        y = self.display_class.titlebar_height + self._s(6, min_px=2)

        self.draw.text(
            (x, y),
            "CAPTURING...",
            font=self.fonts.bold.font,
            fill=self.colors.get(255),
        )
        y += self.fonts.bold.height + self._s(10, min_px=4)

        file_count = self._get_sweep_files_since_start()
        progress_pct = min(100, int((file_count / self.total_images) * 100))

        self.draw.text(
            (x, y),
            f"{file_count} / {self.total_images}",
            font=self.fonts.large.font,
            fill=self.colors.get(192),
        )
        y += self.fonts.large.height + self._s(10, min_px=4)

        # Progress bar: full width with margins
        bar_x = x
        bar_w = max(10, self.display_class.resX - (2 * x))
        bar_h = self._s(12, min_px=6)

        self.draw.rectangle(
            [bar_x, y, bar_x + bar_w, y + bar_h],
            outline=self.colors.get(128),
            fill=self.colors.get(0),
        )

        filled_w = int(bar_w * (progress_pct / 100.0))
        self.draw.rectangle(
            [bar_x, y, bar_x + filled_w, y + bar_h],
            fill=self.colors.get(128),
        )

        y += bar_h + self._s(8, min_px=3)

        # Estimated time remaining
        elapsed = int(time.time() - self.start_time)
        if file_count > 0:
            avg_time_per_image = elapsed / file_count
            remaining = int(avg_time_per_image * (self.total_images - file_count))
        else:
            remaining = self.estimated_duration

        mins = remaining // 60
        secs = remaining % 60
        self.draw.text(
            (x, y),
            f"~{mins}:{secs:02d} remaining",
            font=self.fonts.base.font,
            fill=self.colors.get(128),
        )

        if file_count >= self.total_images:
            self.state = SweepState.COMPLETE

    def _get_sweep_files_since_start(self):
        """Count PNG files in sweep directory created after we started"""
        try:
            captures_dir = Path(utils.data_dir) / "captures"
            if not captures_dir.exists():
                return 0

            # Find sweep directories created after we started (with tolerance)
            sweep_dirs = []
            for sweep_dir in captures_dir.glob("sweep_*"):
                # Check if directory was created after we started (minus 1 second tolerance)
                if sweep_dir.stat().st_ctime >= (self.start_time - 1):
                    sweep_dirs.append(sweep_dir)

            if not sweep_dirs:
                return 0

            # Use the most recent one (should be ours)
            most_recent_sweep = max(sweep_dirs, key=lambda p: p.stat().st_ctime)

            # Count processed PNG files (the ones we care about for progress)
            png_files = list(most_recent_sweep.glob("*_processed.png"))
            return len(png_files)
        except Exception:
            # If anything fails, return 0 to avoid crashing the UI
            return 0

    def _draw_complete(self):
        """Draw completion screen (resolution-aware)"""
        x = self._s(10, min_px=4)

        # Place message around upper-middle of the usable area
        usable_h = self.display_class.resY - self.display_class.titlebar_height
        y = self.display_class.titlebar_height + int(usable_h * 0.30)

        self.draw.text(
            (x, y),
            "SWEEP COMPLETE!",
            font=self.fonts.bold.font,
            fill=self.colors.get(255),
        )

        y += self.fonts.bold.height + self._s(10, min_px=4)

        self.draw.text(
            (x, y),
            "Metadata saved",
            font=self.fonts.base.font,
            fill=self.colors.get(192),
        )

        # Legend pinned to bottom
        y_leg = self.display_class.resY - self.fonts.base.height - self._s(4, min_px=2)
        self.draw.text(
            (x, y_leg),
            f"{self._SQUARE_}: EXIT",
            font=self.fonts.base.font,
            fill=self.colors.get(192),
        )

    # Key handlers
    def key_number(self, number):
        """Handle number keys"""
        if self.state == SweepState.ASK_SQM:
            if number == 0 and self.sqm_input == "":
                # Skip SQM input
                self.state = SweepState.CONFIRM
            elif len(self.sqm_input) < 4:  # Limit to 4 digits (XX.XX)
                self.sqm_input += str(number)
        elif self.state == SweepState.CONFIRM:
            if number == 0:
                # Cancel
                if self.remove_from_stack:
                    self.remove_from_stack()

    def key_minus(self):
        """Handle minus button - delete last digit"""
        if self.state == SweepState.ASK_SQM:
            if self.sqm_input:
                self.sqm_input = self.sqm_input[:-1]

    def key_square(self):
        """Handle square button"""
        if self.state == SweepState.ASK_SQM:
            # Accept SQM input and move to confirm
            if self.sqm_input and len(self.sqm_input) == 4:
                # Convert to XX.XX format
                try:
                    self.reference_sqm = float(
                        self.sqm_input[:2] + "." + self.sqm_input[2:]
                    )
                    self.state = SweepState.CONFIRM
                except ValueError:
                    # Invalid input, clear and try again
                    self.sqm_input = ""
            elif not self.sqm_input:
                # No input, skip to confirm
                self.state = SweepState.CONFIRM
        elif self.state == SweepState.CONFIRM:
            # Start capture
            self.state = SweepState.CAPTURING
        elif self.state == SweepState.COMPLETE:
            # Exit
            if self.remove_from_stack:
                self.remove_from_stack()
