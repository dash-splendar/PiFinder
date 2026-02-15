#!/usr/bin/python
# -*- coding:utf-8 -*-
# mypy: ignore-errors
"""
This module contains all the UI Module classes

"""

import queue
import time
import numpy as np
from PIL import ImageChops

from PiFinder.ui.marking_menus import MarkingMenuOption, MarkingMenu
from PiFinder import plot
from PiFinder.ui.base import UIModule


def align_on_radec(ra, dec, command_queues, config_object, shared_state) -> bool:
    """
    Handles the intricacies of:
    * Telling the solver to figure out alignment pixel
    * Wait for it to be done
    * Set the config item and the shared state
    * return the pixel or -1/-1 for error
    """

    # Clear out any pending responses
    while True:
        try:
            command = command_queues["align_response"].get(block=False)
        except queue.Empty:
            break

    # Send command to solver to work out the camera pixel for this target
    command_queues["align_command"].put(
        [
            "align_on_radec",
            ra,
            dec,
        ]
    )

    received_response = False
    start_time = time.time()
    while not received_response:
        # only wait two seconds
        if time.time() - start_time > 2:
            command_queues["align_command"].put(
                [
                    "align_cancel",
                    ra,
                    dec,
                ]
            )
            command_queues["console"].put(_("Align Timeout"))
            return False

        try:
            command = command_queues["align_response"].get(block=False)
        except queue.Empty:
            command = False

        if command is not False:
            received_response = True
            if command[0] == "aligned":
                target_pixel = command[1]

    if target_pixel[0] == -1:
        # Failed to align
        return False

    # success, set all the things...
    command_queues["console"].put(_("Alignment Set"))
    shared_state.set_solve_pixel(target_pixel)
    config_object.set_option("solve_pixel", target_pixel)
    return True


class UIAlign(UIModule):
    __title__ = "ALIGN"
    __help_name__ = "align"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.last_update = time.time()
        self.starfield = plot.Starfield(self.colors, self.display_class.resolution)
        self.solution = None
        self.fov_list = [5, 10.2, 20, 30, 60]
        self.fov_index = 1
        self.fov_set_time = time.time()
        self.fov_target_time = None
        self.desired_fov = self.fov_list[self.fov_index]
        self.fov = self.desired_fov
        self.set_fov(self.desired_fov)
        self.align_mode = False
        self.visible_stars = None
        self.star_list = np.empty((0, 2))
        self.alignment_star = None
        solve_pixel = self.config_object.get_option("solve_pixel", (256, 256))
        # solve_pixel is stored in camera/solver pixel space (legacy assumes ~512x512 with center at 256,256).
        # Map solve_pixel -> UI pixel space for any square UI resolution.
        solve_px = self.config_object.get_option("solve_pixel", (256, 256))
        cam_space = 512.0  # legacy assumption: full camera solve space is 512x512

        self.marker_position = (
            (solve_px[1] / cam_space) * self.display_class.resX,
            (solve_px[0] / cam_space) * self.display_class.resY,
        )

        # Marking menu definition
        self.marking_menu = MarkingMenu(
            left=MarkingMenuOption(),
            down=MarkingMenuOption(),
            right=MarkingMenuOption(),
        )

    def draw_reticle(self):
        radii = [40, 95, 190]
        cx, cy = 240, 240

        for radius in radii:
            bbox = [
                cx - radius,
                cy - radius,
                cx + radius,
                cy + radius
            ]
            self.draw.arc(bbox, start=0, end=360, fill=self.colors.get(255), width=3)

            inset = 30
            w = 4

            # top
            self.draw.line(
                [cx, cy - radius, cx, cy - radius + inset],
                fill=self.colors.get(255),
                width=w
            )

            # bottom
            self.draw.line(
                [cx, cy + radius - inset, cx, cy + radius],
                fill=self.colors.get(255),
                width=w
            )

            # left
            self.draw.line(
                [cx - radius, cy, cx - radius + inset, cy],
                fill=self.colors.get(255),
                width=w
            )

            # right
            self.draw.line(
                [cx + radius - inset, cy, cx + radius, cy],
                fill=self.colors.get(255),
                width=w
            )

    def draw_marker(self):
        """
        draw the reticle if desired
        """
        if not self.solution:
            return

        if self.alignment_star is not None:
            # update the marker position
            self.marker_position = self.starfield.radec_to_xy(
                self.alignment_star["ra_degrees"], self.alignment_star["dec_degrees"]
            )

        x_pos = round(self.marker_position[0])
        y_pos = round(self.marker_position[1])

        # Draw cross
        arm_outer = 30
        arm_inner = 12
        w = 3

        # Draw cross (scale from legacy 128px UI)
        outer = self._s(8, min_px=3)
        inner = self._s(3, min_px=1)

        self.draw.line(
            [x_pos, y_pos - outer, x_pos, y_pos - inner],
            fill=self.colors.get(255),
        )
        self.draw.line(
            [x_pos, y_pos + inner, x_pos, y_pos + outer],
            fill=self.colors.get(255),
        )
        self.draw.line(
            [x_pos - outer, y_pos, x_pos - inner, y_pos],
            fill=self.colors.get(255),
        )
        self.draw.line(
            [x_pos + inner, y_pos, x_pos + outer, y_pos],
            fill=self.colors.get(255),
        )

    def set_fov(self, fov):
        self.fov = fov
        self.starfield.set_fov(fov)
        return

        # TODO: Fix zoom animation
        self.starting_fov = self.fov
        self.fov_starting_time = time.time()
        self.desired_fov = fov

    def animate_fov(self):
        # TODO: Fix zoom animation
        return
        if self.fov == self.desired_fov:
            return

        fov_progress = (time.time() - self.fov_starting_time) * 20
        if self.desired_fov > self.starting_fov:
            current_fov = self.starting_fov + fov_progress
            if current_fov > self.desired_fov:
                current_fov = self.desired_fov
        else:
            current_fov = self.starting_fov - fov_progress
            if current_fov < self.desired_fov:
                current_fov = self.desired_fov
        self.fov = current_fov
        self.starfield.set_fov(current_fov)

    def update(self, force=False):
        if force:
            self.last_update = 0

        if self.shared_state.solve_state():
            self.animate_fov()
            constellation_brightness = 64
            self.solution = self.shared_state.solution()
            last_solve_time = self.solution["solve_time"]
            if (
                last_solve_time > self.last_update
                and self.solution["Roll"] is not None
                and self.solution["RA"] is not None
                and self.solution["Dec"] is not None
            ) or force:
                # This needs to be called first to set RA/DEC/ROLL
                if self.align_mode:
                    # We want to use the CAMERA solve as
                    # it's not updated by the IMU and we'll be moving
                    # the reticle to the star
                    image_obj, self.visible_stars = self.starfield.plot_starfield(
                        self.solution["camera_solve"]["RA"],
                        self.solution["camera_solve"]["Dec"],
                        self.solution["camera_solve"]["Roll"],
                        constellation_brightness,
                        shade_frustrum=True,
                    )
                else:
                    image_obj, self.visible_stars = self.starfield.plot_starfield(
                        self.solution["camera_center"]["RA"],
                        self.solution["camera_center"]["Dec"],
                        self.solution["camera_center"]["Roll"],
                        constellation_brightness,
                        shade_frustrum=True,
                    )

                image_obj = ImageChops.multiply(
                    image_obj.convert("RGB"), self.colors.red_image
                )
                self.screen.paste(image_obj)

                self.last_update = last_solve_time
                # draw_reticle if we have a camera solve
                if self.align_mode:
                    self.draw_marker()
                else:
                    self.draw_reticle()

                # draw the help text
                if not self.align_mode:
                    # Prompt to start align
                    hint_text = _(f"  {self._SQUARE_} START ALIGN")
                elif self.alignment_star is None:
                    hint_text = _(f"{self._ARROWS_} SELECT STAR")
                else:
                    hint_text = _(f"{self._SQUARE_} SAVE / 0 CANCEL")
                self.draw.text(
                    (
                        self._s(15, min_px=4),
                        self.display_class.resY - self.fonts.base.height - self._s(2, min_px=1),
                    ),
                    hint_text,
                    font=self.fonts.base.font,
                    fill=self.colors.get(255),
                )


        else:
            self.draw.rectangle(
                [0, 0, self.display_class.resX, self.display_class.resY],
                fill=self.colors.get(0),
            )
            self.draw.text(
                (self._s(16, min_px=6), self.display_class.titlebar_height + self._s(10, min_px=4)),
                _("Can't plot"),
                font=self.fonts.large.font,
                fill=self.colors.get(255),
            )
            self.draw.text(
                (
                    self._s(26, min_px=10),
                    self.display_class.titlebar_height
                    + self._s(10, min_px=4)
                    + self.fonts.large.height
                    + self._s(4, min_px=2),
                ),
                _("No Solve Yet"),
                font=self.fonts.base.font,
                fill=self.colors.get(255),
            )

        return self.screen_update(title_bar=not self.align_mode)

    def change_fov(self, direction):
        self.fov_index += direction
        if self.fov_index < 0:
            self.fov_index = 0
        if self.fov_index >= len(self.fov_list):
            self.fov_index = len(self.fov_list) - 1
        self.set_fov(self.fov_list[self.fov_index])
        self.update(force=True)

    def switch_align_star(self, direction: str) -> None:
        # iterate through all stars with screen coord on the requested
        # side of the screen, find the closest, set those coordinates
        mag_limit = 5.5

        # This 'pushes' the target in the direction of the
        # arrow press to bias the closeness in that direction
        offset_bias = 1

        # filter with numpy
        # mag /screen limits
        candidate_stars = self.visible_stars
        candidate_stars = candidate_stars[
            (
                (candidate_stars["x_pos"] > 0)
                & (candidate_stars["x_pos"] < self.display_class.resolution[0])
                & (candidate_stars["y_pos"] > 0)
                & (candidate_stars["y_pos"] < self.display_class.resolution[1])
                & (candidate_stars["magnitude"] < mag_limit)
            )
        ]
        if direction == "up":
            candidate_stars = candidate_stars[
                (candidate_stars["y_pos"] < self.marker_position[1] - offset_bias)
            ]
        if direction == "down":
            candidate_stars = candidate_stars[
                (candidate_stars["y_pos"] > self.marker_position[1] + offset_bias)
            ]
        if direction == "left":
            candidate_stars = candidate_stars[
                (candidate_stars["x_pos"] < self.marker_position[0] - offset_bias)
            ]

        if direction == "right":
            candidate_stars = candidate_stars[
                (candidate_stars["x_pos"] > self.marker_position[0] + offset_bias)
            ]

        if len(candidate_stars) == 0:
            return

        # calculate distance in screen space
        candidate_stars = candidate_stars.assign(
            distance=np.hypot(
                candidate_stars["x_pos"] - self.marker_position[0],
                candidate_stars["y_pos"] - self.marker_position[1],
            )
        )

        candidate_stars = candidate_stars.sort_values("distance")

        # look for stars that are within the 'cone' of the
        # direction pressed
        found_star = False
        for i in range(len(candidate_stars)):
            test_star = candidate_stars.iloc[i]
            x_delta = abs(test_star["x_pos"] - self.marker_position[0])
            y_delta = abs(test_star["y_pos"] - self.marker_position[1])

            if direction == "up" or direction == "down":
                if y_delta > x_delta:
                    found_star = True
                    self.alignment_star = test_star
                    break

            if direction == "left" or direction == "right":
                if x_delta > y_delta:
                    found_star = True
                    self.alignment_star = test_star
                    break

        if not found_star:
            self.alignment_star = candidate_stars.iloc[0]

        self.marker_position = (
            self.alignment_star["x_pos"],
            self.alignment_star["y_pos"],
        )

        self.update(force=True)

        return

    def key_plus(self):
        # if not self.align_mode:
        self.change_fov(-1)

    def key_minus(self):
        # if not self.align_mode:
        self.change_fov(1)

    def key_square(self):
        if self.align_mode:
            self.align_mode = False

            if self.alignment_star is not None:
                self.message(_("Aligning..."), 0.1)
                if align_on_radec(
                    self.alignment_star["ra_degrees"],
                    self.alignment_star["dec_degrees"],
                    self.command_queues,
                    self.config_object,
                    self.shared_state,
                ):
                    self.message(_("Aligned!"), 1)
                else:
                    self.message(_("Alignment failed"), 2)
        else:
            self.align_mode = True
        self.update(force=True)

    def key_up(self):
        if self.align_mode:
            self.switch_align_star("up")

    def key_down(self):
        if self.align_mode:
            self.switch_align_star("down")

    def key_right(self):
        if self.align_mode:
            self.switch_align_star("right")

    def key_left(self):
        if self.align_mode:
            self.switch_align_star("left")
            return False
        return True

    def key_number(self, number):
        if self.align_mode:
            if number == 1:
                # reset reticle to center (solve space)
                self.shared_state.set_solve_pixel((256, 256))
                self.config_object.set_option("solve_pixel", (256, 256))

                # reset marker to UI center (any resolution)
                self.marker_position = (self.display_class.resX / 2, self.display_class.resY / 2)

                self.update(force=True)
                self.align_mode = False

            if number == 0:
                # cancel without changing alignment
                self.align_mode = False
                self.update(force=True)
