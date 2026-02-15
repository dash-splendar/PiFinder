#!/usr/bin/python
# -*- coding:utf-8 -*-
"""
USB IMU backend.

Reads fused orientation from a Feather RP2040 over USB CDC serial.
Feather should output JSON lines like:

  {"quat":[x,y,z,w], "cal":3}

Where quat is in scipy's expected order: [x, y, z, w]
"""

import json
import logging
import time
from typing import Optional, Tuple

from PiFinder.multiproclogging import MultiprocLogging
from PiFinder import config

from scipy.spatial.transform import Rotation

logger = logging.getLogger("IMU.usb")

QUEUE_LEN = 10
MOVE_CHECK_LEN = 2


class Imu:
    """
    Matches the interface used by imu_pi.Imu:
      - update()
      - moving()
      - get_euler()
      - avg_quat
      - calibration
    """

    def __init__(self, device: str = "/dev/ttyACM0", baud: int = 115200, timeout: float = 0.2):
        self.device = device
        self.baud = baud
        self.timeout = timeout

        self._ser = None  # lazily opened

        self.quat_history = [(0, 0, 0, 0)] * QUEUE_LEN
        self._flip_count = 0
        self.calibration = 0
        self.avg_quat: Tuple[float, float, float, float] = (0, 0, 0, 0)
        self.__moving = False
        self.__reading_diff = 0.0

        self.last_sample_time = time.time()
        self.imu_sample_frequency = 1 / 30

        cfg = config.Config()
        imu_threshold_scale = cfg.get_option("imu_threshold_scale", 1)
        self.__moving_threshold = (
            0.0005 * imu_threshold_scale,
            0.0003 * imu_threshold_scale,
        )

        # If Feather doesn't provide "cal", we can treat "valid data received" as calibrated.
        self._have_seen_data = False

    def _open(self):
        if self._ser is not None:
            return
        try:
            import serial  # pyserial
        except Exception as e:
            raise RuntimeError("pyserial is required for USB IMU (pip install pyserial)") from e

        self._ser = serial.Serial(self.device, self.baud, timeout=self.timeout)

        # Clear any buffered junk
        try:
            self._ser.reset_input_buffer()
        except Exception:
            pass

    def _read_line(self) -> Optional[str]:
        self._open()
        if self._ser is None:
            return None
        try:
            line = self._ser.readline()
        except Exception as e:
            logger.error(f"IMU USB read error: {e}")
            return None
        if not line:
            return None
        try:
            return line.decode("utf-8", errors="ignore").strip()
        except Exception:
            return None

    def quat_to_euler(self, quat):
        if quat[0] + quat[1] + quat[2] + quat[3] == 0:
            return 0, 0, 0
        rot = Rotation.from_quat(quat)
        rot_euler = rot.as_euler("xyz", degrees=True)
        rot_euler[0] += 180
        rot_euler[1] += 180
        rot_euler[2] += 180
        return rot_euler

    def moving(self):
        return self.__moving

    def update(self):
        # rate limit
        if time.time() - self.last_sample_time < self.imu_sample_frequency:
            return
        self.last_sample_time = time.time()

        # Pull the newest valid message (drain a few lines so we use latest)
        newest = None
        for _ in range(5):
            line = self._read_line()
            if not line:
                break
            newest = line

        if newest is None:
            return

        # Parse JSON
        try:
            msg = json.loads(newest)
        except Exception:
            # allow Feather to print debug lines without killing us
            return

        quat = msg.get("quat")
        if not isinstance(quat, (list, tuple)) or len(quat) != 4:
            return

        # Convert to floats and validate
        try:
            quat = tuple(float(q) for q in quat)
        except Exception:
            return

        if quat[0] is None:
            return

        self._have_seen_data = True

        # Calibration: accept Feather's cal if present; otherwise 3 once we have data
        cal = msg.get("cal", None)
        if isinstance(cal, int):
            self.calibration = max(0, min(3, cal))
        else:
            self.calibration = 3 if self._have_seen_data else 0

        # If not calibrated, behave like imu_pi: discard
        if self.calibration == 0:
            logger.warning("NOIMU CAL (usb)")
            return True

        # Movement / diff logic (copied from imu_pi behavior)
        _quat_diff = [abs(quat[i] - self.quat_history[-1][i]) for i in range(4)]
        self.__reading_diff = sum(_quat_diff)

        if self.__reading_diff == 0.0078125:
            self.__reading_diff = 0
            return

        if self.__reading_diff > 1.5:
            self._flip_count += 1
            if self._flip_count > 10:
                self.quat_history = [quat] * QUEUE_LEN
                self.__reading_diff = 0
            else:
                self.__reading_diff = 0
                return
        else:
            self._flip_count = 0

        self.avg_quat = quat
        if len(self.quat_history) == QUEUE_LEN:
            self.quat_history = self.quat_history[1:]
        self.quat_history.append(quat)

        if self.__moving:
            if self.__reading_diff < self.__moving_threshold[1]:
                self.__moving = False
        else:
            if self.__reading_diff > self.__moving_threshold[0]:
                self.__moving = True

    def get_euler(self):
        return list(self.quat_to_euler(self.avg_quat))

    def __str__(self):
        return (
            f"IMU USB Information:\n"
            f"Device: {self.device}\n"
            f"Calibration Status: {self.calibration}\n"
            f"Quaternion History: {self.quat_history}\n"
            f"Average Quaternion: {self.avg_quat}\n"
            f"Moving: {self.moving()}\n"
            f"Reading Difference: {self.__reading_diff}\n"
            f"Flip Count: {self._flip_count}\n"
            f"Last Sample Time: {self.last_sample_time}\n"
            f"IMU Sample Frequency: {self.imu_sample_frequency}\n"
            f"Moving Threshold: {self.__moving_threshold}\n"
        )


def imu_monitor(shared_state, console_queue, log_queue, imu_device: Optional[str] = None):
    MultiprocLogging.configurer(log_queue)
    logger.debug("Starting USB IMU")

    dev = imu_device or "/dev/ttyACM0"

    imu = None
    try:
        imu = Imu(device=dev)
    except Exception as e:
        logger.error(f"Error starting USB IMU: {e}")
        console_queue.put(f"IMU: USB error, using fake IMU ({e})")
        console_queue.put("DEGRADED_OPS IMU")
        from PiFinder.imu_fake import Imu as ImuFake

        imu = ImuFake()

    imu_calibrated = False
    imu_data = {
        "moving": False,
        "move_start": None,
        "move_end": None,
        "pos": [0, 0, 0],
        "quat": [0, 0, 0, 0],
        "start_pos": [0, 0, 0],
        "status": 0,
    }

    while True:
        imu.update()
        imu_data["status"] = getattr(imu, "calibration", 0)

        if imu.moving():
            if not imu_data["moving"]:
                logger.debug("IMU USB: move start")
                imu_data["moving"] = True
                imu_data["start_pos"] = imu_data["pos"]
                imu_data["move_start"] = time.time()
            imu_data["pos"] = imu.get_euler()
            imu_data["quat"] = list(getattr(imu, "avg_quat", (0, 0, 0, 0)))
        else:
            if imu_data["moving"]:
                logger.debug("IMU USB: move end")
                imu_data["moving"] = False
                imu_data["pos"] = imu.get_euler()
                imu_data["quat"] = list(getattr(imu, "avg_quat", (0, 0, 0, 0)))
                imu_data["move_end"] = time.time()

        if not imu_calibrated:
            if imu_data["status"] == 3:
                imu_calibrated = True
                console_queue.put("IMU: USB Calibrated!")

        if shared_state is not None and imu_calibrated:
            shared_state.set_imu(imu_data)
