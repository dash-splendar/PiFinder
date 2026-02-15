from PiFinder.ui.base import UIModule
from PiFinder import utils
from PiFinder.state_utils import sleep_for_framerate
from PiFinder.ui.marking_menus import MarkingMenuOption, MarkingMenu

sys_utils = utils.get_sys_utils()


class UIEquipment(UIModule):
    """
    Displays various status information
    """

    __title__ = "Equipment"
    # TODO __help__ for Equipment!

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.menu_index = 0  # Telescope
        # Marking Menu - Just default help for now
        self.marking_menu = MarkingMenu(
            left=MarkingMenuOption(),
            right=MarkingMenuOption(),
            down=MarkingMenuOption(),
        )

    def update(self, force=False):
        sleep_for_framerate(self.shared_state)
        self.clear_screen()

        x_left = self._s(10, min_px=4)

        # Legacy y=20 was ~3px below a 17px titlebar.
        y_tel = self.display_class.titlebar_height + self._s(3, min_px=2)

        if self.config_object.equipment.active_telescope is None:
            self.draw.text(
                (x_left, y_tel),
                _("No telescope selected"),
                font=self.fonts.small.font,
                fill=self.colors.get(128),
            )
        else:
            self.draw.text(
                (x_left, y_tel),
                self.config_object.equipment.active_telescope.name.strip(),
                font=self.fonts.base.font,
                fill=self.colors.get(128),
            )

        # Legacy y=35 was ~18px below the titlebar.
        y_eye = self.display_class.titlebar_height + self._s(18, min_px=6)

        if self.config_object.equipment.active_eyepiece is None:
            self.draw.text(
                (x_left, y_eye),
                _("No eyepiece selected"),
                font=self.fonts.small.font,
                fill=self.colors.get(128),
            )
        else:
            self.draw.text(
                (x_left, y_eye),
                f"{self.config_object.equipment.active_eyepiece.focal_length_mm}mm {self.config_object.equipment.active_eyepiece.name}",
                font=self.fonts.base.font,
                fill=self.colors.get(128),
            )

        if (
            self.config_object.equipment.active_telescope is not None
            and self.config_object.equipment.active_eyepiece is not None
        ):
            mag = self.config_object.equipment.calc_magnification()
            if mag > 0:
                # Legacy y=50 -> ~33px below titlebar; y=70 -> ~53px below titlebar
                y_mag = self.display_class.titlebar_height + self._s(33, min_px=10)
                y_tfov = self.display_class.titlebar_height + self._s(53, min_px=14)

                self.draw.text(
                    (x_left, y_mag),
                    _("Mag: {mag:.0f}x").format(
                        mag=mag
                    ),  # TRANSLATORS: Magnification e.g. 200x
                    font=self.fonts.base.font,
                    fill=self.colors.get(128),
                )

                tfov = self.config_object.equipment.calc_tfov()
                tfov_degrees = int(tfov)
                tfov_minutes = int((tfov - tfov_degrees) * 60)
                self.draw.text(
                    (x_left, y_tfov),
                    _(
                        "TFOV: {tfov_degrees:.0f}°{tfov_minutes:02.0f}'"
                    ).format(  # TRANSLATORS: True field of View in degree and minutes
                        tfov_degrees=tfov_degrees, tfov_minutes=tfov_minutes
                    ),
                    font=self.fonts.base.font,
                    fill=self.colors.get(128),
                )

        horiz_pos = self.display_class.titlebar_height + 70

        horiz_pos = self.display_class.titlebar_height + self._s(70, min_px=self._s(30, min_px=20))

        self.draw.text(
            (x_left, horiz_pos),
            _("Telescope..."),
            font=self.fonts.large.font,
            fill=self.colors.get(192),
        )
        if self.menu_index == 0:
            self.draw_menu_pointer(horiz_pos)

        horiz_pos += self._s(18, min_px=self.fonts.large.height + self._s(2, min_px=1))

        self.draw.text(
            (x_left, horiz_pos),
            _("Eyepiece..."),
            font=self.fonts.large.font,
            fill=self.colors.get(192),
        )
        if self.menu_index == 1:
            self.draw_menu_pointer(horiz_pos)

        return self.screen_update()

    def key_down(self):
        self.menu_index += 1
        if self.menu_index > 1:
            self.menu_index = 1

    def key_up(self):
        self.menu_index -= 1
        if self.menu_index < 0:
            self.menu_index = 0

    def key_right(self):
        if self.menu_index == 0:
            self.jump_to_label("select_telescope")
        if self.menu_index == 1:
            self.jump_to_label("select_eyepiece")

    def draw_menu_pointer(self, horiz_position: int):
        self.draw.text(
            (self._s(2, min_px=1), horiz_position),
            self._RIGHT_ARROW,
            font=self.fonts.large.font,
            fill=self.colors.get(255),
        )

    def active(self):
        """
        Called when a module becomes active
        i.e. foreground controlling display
        """
