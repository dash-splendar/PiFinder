#!/usr/bin/python
# -*- coding:utf-8 -*-
"""
This module contains all the UI Module classes

"""

import time
import requests

from PiFinder import utils
from PiFinder.ui.base import UIModule

sys_utils = utils.get_sys_utils()


def update_needed(current_version: str, repo_version: str) -> bool:
    """
    Returns true if an update is available

    Update is available if semvar of repo_version is > current_version
    Also returns True on error to allow be biased towards allowing
    updates if issues
    """
    try:
        _tmp_split = current_version.split(".")
        current_version_compare = (
            int(_tmp_split[0]),
            int(_tmp_split[1]),
            int(_tmp_split[2]),
        )

        _tmp_split = repo_version.split(".")
        repo_version_compare = (
            int(_tmp_split[0]),
            int(_tmp_split[1]),
            int(_tmp_split[2]),
        )

        # tuples compare in significance from first to last element
        return repo_version_compare > current_version_compare

    except Exception:
        return True


class UISoftware(UIModule):
    """
    UI for updating software versions
    """

    __title__ = "SOFTWARE"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.version_txt = f"{utils.pifinder_dir}/version.txt"
        self.wifi_txt = f"{utils.pifinder_dir}/wifi_status.txt"
        with open(self.wifi_txt, "r") as wfs:
            self._wifi_mode = wfs.read()
        with open(self.version_txt, "r") as ver:
            self._software_version = ver.read()

        self._release_version = "-.-.-"
        self._elipsis_count = 0
        self._go_for_update = False
        self._option_select = "Update"

    def get_release_version(self):
        """
        Fetches current release version from
        github, sets class variable if found
        """
        try:
            res = requests.get(
                "https://raw.githubusercontent.com/brickbots/PiFinder/release/version.txt"
            )
        except requests.exceptions.ConnectionError:
            print("Could not connect to github")
            self._release_version = "Unknown"
            return

        if res.status_code == 200:
            self._release_version = res.text[:-1]
        else:
            self._release_version = "Unknown"

    def update_software(self):
        self.message(_("Updating..."), 10)
        if sys_utils.update_software():
            self.message(_("Ok! Restarting"), 10)
            sys_utils.restart_system()
        else:
            self.message(_("Error on Upd"), 3)

    def update(self, force=False):
        time.sleep(1 / 30)
        self.clear_screen()

        x0 = 0
        x_left = self._s(10, min_px=4)
        gap = self._s(2, min_px=1)
        block_gap = self._s(6, min_px=2)

        draw_pos = self.display_class.titlebar_height + self._s(2, min_px=1)

        self.draw.text(
            (x0, draw_pos),
            _("Wifi Mode: {}").format(self._wifi_mode),
            font=self.fonts.base.font,
            fill=self.colors.get(128),
        )
        draw_pos += self.fonts.base.height + block_gap

        self.draw.text(
            (x0, draw_pos),
            _("Current Version"),
            font=self.fonts.bold.font,
            fill=self.colors.get(128),
        )
        draw_pos += self.fonts.bold.height + gap

        self.draw.text(
            (x_left, draw_pos),
            f"{self._software_version}",
            font=self.fonts.bold.font,
            fill=self.colors.get(192),
        )
        draw_pos += self.fonts.bold.height + block_gap

        self.draw.text(
            (x0, draw_pos),
            _("Release Version"),
            font=self.fonts.bold.font,
            fill=self.colors.get(128),
        )
        draw_pos += self.fonts.bold.height + gap

        self.draw.text(
            (x_left, draw_pos),
            f"{self._release_version}",
            font=self.fonts.bold.font,
            fill=self.colors.get(192),
        )

        # Lower status area (legacy y=90/105 on 128px -> lower half of screen)
        usable_h = self.display_class.resY - self.display_class.titlebar_height
        y1 = self.display_class.titlebar_height + int(usable_h * 0.62)
        y2 = y1 + self.fonts.large.height + self._s(6, min_px=2)

        if self._wifi_mode != "Client":
            self.draw.text(
                (x_left, y1),
                _("WiFi must be"),
                font=self.fonts.large.font,
                fill=self.colors.get(255),
            )
            self.draw.text(
                (x_left, y2),
                _("client mode"),
                font=self.fonts.large.font,
                fill=self.colors.get(255),
            )
            return self.screen_update()

        if self._release_version == "-.-.-":
            # check elipsis count here... if we are at >30 check for
            # release versions
            if self._elipsis_count > 30:
                self.get_release_version()
            self.draw.text(
                (x_left, y1),
                _("Checking for"),
                font=self.fonts.large.font,
                fill=self.colors.get(255),
            )
            self.draw.text(
                (x_left, y2),
                _("updates{elipsis}").format(
                    elipsis="." * int(self._elipsis_count / 10)
                ),
                font=self.fonts.large.font,
                fill=self.colors.get(255),
            )

            self._elipsis_count += 1
            if self._elipsis_count > 39:
                self._elipsis_count = 0
            return self.screen_update()

        if not update_needed(
            self._software_version.strip(), self._release_version.strip()
        ):
            self.draw.text(
                (x_left, y1),
                _("No Update"),
                font=self.fonts.large.font,
                fill=self.colors.get(255),
            )
            self.draw.text(
                (x_left, y2),
                _("needed"),
                font=self.fonts.large.font,
                fill=self.colors.get(255),
            )

            return self.screen_update()

        # If we are here, go for update!
        self._go_for_update = True
        self.draw.text(
            (x_left, y1),
            _("Update Now"),
            font=self.fonts.large.font,
            fill=self.colors.get(255),
        )
        self.draw.text(
            (x_left, y2),
            _("Cancel"),
            font=self.fonts.large.font,
            fill=self.colors.get(255),
        )

        ind_pos = y1 if self._option_select == "Update" else y2
        self.draw.text(
            (x0, ind_pos),
            self._RIGHT_ARROW,
            font=self.fonts.large.font,
            fill=self.colors.get(255),
        )

        return self.screen_update()

    def toggle_option(self):
        if not self._go_for_update:
            return
        if self._option_select == "Update":
            self._option_select = "Cancel"
        else:
            self._option_select = "Update"

    def key_up(self):
        self.toggle_option()

    def key_down(self):
        self.toggle_option()

    def key_right(self):
        if self._option_select == "Cancel":
            self.remove_from_stack()
        else:
            self.update_software()
