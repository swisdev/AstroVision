"""
servo_controller.py — GPIO PWM servo driver with P-control loop.

Uses the *pigpio* library for jitter-free PWM on any GPIO pin.
Falls back to a software stub when pigpio is unavailable (desktop dev).
"""

import time
import logging

try:
    import pigpio
    _HAS_PIGPIO = True
except ImportError:
    _HAS_PIGPIO = False

from config import (
    SERVO_PAN_PIN, SERVO_TILT_PIN,
    SERVO_MIN_PULSE, SERVO_MAX_PULSE,
    SERVO_MIN_ANGLE, SERVO_MAX_ANGLE,
    SERVO_PAN_START, SERVO_TILT_START,
    SERVO_MAX_STEP,
    KP_PAN, KP_TILT,
    DEADZONE_PX,
    FRAME_WIDTH, FRAME_HEIGHT,
)

log = logging.getLogger("servo")


def _angle_to_pulse(angle: float) -> int:
    """Convert an angle in degrees to a pulse width in microseconds."""
    ratio = (angle - SERVO_MIN_ANGLE) / (SERVO_MAX_ANGLE - SERVO_MIN_ANGLE)
    return int(SERVO_MIN_PULSE + ratio * (SERVO_MAX_PULSE - SERVO_MIN_PULSE))


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


class ServoController:
    """
    Drives pan (X) and tilt (Y) servos and exposes a simple
    proportional-control update method.
    """

    def __init__(self):
        self.pan_angle: float  = SERVO_PAN_START
        self.tilt_angle: float = SERVO_TILT_START

        # Frame centre — the target pixel position
        self._cx = FRAME_WIDTH  // 2
        self._cy = FRAME_HEIGHT // 2

        if _HAS_PIGPIO:
            self._pi = pigpio.pi()
            if not self._pi.connected:
                log.warning("pigpio daemon not running — servos disabled")
                self._pi = None
        else:
            log.warning("pigpio not installed — running in stub mode")
            self._pi = None

        # Move servos to their start positions
        self._write_pan(self.pan_angle)
        self._write_tilt(self.tilt_angle)

    # ------------------------------------------------------------------
    # Low-level PWM writes
    # ------------------------------------------------------------------
    def _write_pan(self, angle: float) -> None:
        self.pan_angle = _clamp(angle, SERVO_MIN_ANGLE, SERVO_MAX_ANGLE)
        pw = _angle_to_pulse(self.pan_angle)
        if self._pi:
            self._pi.set_servo_pulsewidth(SERVO_PAN_PIN, pw)
        log.debug("PAN  → %.1f°  (%d µs)", self.pan_angle, pw)

    def _write_tilt(self, angle: float) -> None:
        self.tilt_angle = _clamp(angle, SERVO_MIN_ANGLE, SERVO_MAX_ANGLE)
        pw = _angle_to_pulse(self.tilt_angle)
        if self._pi:
            self._pi.set_servo_pulsewidth(SERVO_TILT_PIN, pw)
        log.debug("TILT → %.1f°  (%d µs)", self.tilt_angle, pw)

    # ------------------------------------------------------------------
    # Proportional controller
    # ------------------------------------------------------------------
    def update(self, target_x: int, target_y: int) -> None:
        """
        Given the pixel coordinates of the tracked object, compute the
        error from frame-centre and nudge the servos proportionally.

        The step size is clamped to SERVO_MAX_STEP so the bracket moves
        smoothly rather than lurching.
        """
        err_x = target_x - self._cx
        err_y = target_y - self._cy

        # Deadzone — ignore sub-pixel jitter
        if abs(err_x) < DEADZONE_PX:
            err_x = 0
        if abs(err_y) < DEADZONE_PX:
            err_y = 0

        # P-control: Δangle = Kp × error
        delta_pan  = _clamp(KP_PAN  * err_x, -SERVO_MAX_STEP, SERVO_MAX_STEP)
        delta_tilt = _clamp(KP_TILT * err_y, -SERVO_MAX_STEP, SERVO_MAX_STEP)

        self._write_pan(self.pan_angle   + delta_pan)
        self._write_tilt(self.tilt_angle + delta_tilt)

    # ------------------------------------------------------------------
    # Manual slew (for the web UI or keyboard)
    # ------------------------------------------------------------------
    def set_angles(self, pan: float, tilt: float) -> None:
        self._write_pan(pan)
        self._write_tilt(tilt)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    def stop(self) -> None:
        """Turn off PWM signals and release pigpio resources."""
        if self._pi:
            self._pi.set_servo_pulsewidth(SERVO_PAN_PIN, 0)
            self._pi.set_servo_pulsewidth(SERVO_TILT_PIN, 0)
            self._pi.stop()
            self._pi = None
        log.info("Servos released.")
