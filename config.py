"""
config.py — Central configuration for the Celestial Tracker.

All hardware pin assignments, servo limits, camera settings, and
tuning constants live here so operators can adjust them in one place.
"""

import os

# ---------------------------------------------------------------------------
# GPIO Pin Assignments  (BCM numbering)
# ---------------------------------------------------------------------------
# Change these to match YOUR wiring.  The two GPIOs must support hardware
# PWM on the Pi (GPIO 12/13/18/19 on a Pi 4; any GPIO works with pigpio's
# software-timed PWM on older boards).
SERVO_PAN_PIN  = int(os.getenv("CT_PAN_PIN",  17))   # X-axis (horizontal)
SERVO_TILT_PIN = int(os.getenv("CT_TILT_PIN", 18))   # Y-axis (vertical)

# ---------------------------------------------------------------------------
# Servo Pulse-Width Limits  (microseconds)
# ---------------------------------------------------------------------------
# Standard hobby servos: 500 µs → 0°, 2500 µs → 180°.
# Adjust if your servos have a different travel range.
SERVO_MIN_PULSE = 500
SERVO_MAX_PULSE = 2500
SERVO_MIN_ANGLE = 0.0
SERVO_MAX_ANGLE = 180.0

# Starting position (degrees).  90° = centred for most brackets.
SERVO_PAN_START  = 90.0
SERVO_TILT_START = 90.0

# Maximum degrees the servo may move in a single control tick.
# Smaller = smoother but slower tracking.
SERVO_MAX_STEP = 2.0

# ---------------------------------------------------------------------------
# Proportional Controller Gains
# ---------------------------------------------------------------------------
# Kp maps pixel-error → degree adjustment.  Start low and increase until
# tracking is responsive but not oscillatory.
KP_PAN  = 0.05    # degrees per pixel of horizontal error
KP_TILT = 0.05    # degrees per pixel of vertical error

# Deadzone in pixels — ignore errors smaller than this to prevent jitter.
DEADZONE_PX = 5

# ---------------------------------------------------------------------------
# Camera / Frame Settings
# ---------------------------------------------------------------------------
CAMERA_INDEX   = 0          # /dev/video0
FRAME_WIDTH    = 320        # Downsampled capture width
FRAME_HEIGHT   = 240        # Downsampled capture height
FPS_LIMIT      = 10         # Max processing ticks per second

# ---------------------------------------------------------------------------
# Vision Pipeline Tuning
# ---------------------------------------------------------------------------
GAUSSIAN_KERNEL    = (5, 5)  # Must be odd integers
GAUSSIAN_SIGMA     = 0

# Adaptive threshold for bright-object (star/planet) detection
ADAPTIVE_BLOCK_SIZE = 11    # Neighbourhood size (odd)
ADAPTIVE_C          = 2     # Constant subtracted from mean

# Minimum contour area to accept as a real detection (pixels²)
MIN_BRIGHT_AREA = 4
MAX_BRIGHT_AREA = 800

# Frame-differencing for moving-object (satellite/meteor) detection
DIFF_THRESHOLD     = 30     # Binary threshold on abs-diff image
MIN_MOVING_AREA    = 20
MAX_MOVING_AREA    = 2000

# Confidence scoring
CONFIDENCE_BRIGHT_BASE = 0.7   # Base confidence for bright detections
CONFIDENCE_MOVE_BASE   = 0.6   # Base confidence for moving detections

# ---------------------------------------------------------------------------
# Web Server
# ---------------------------------------------------------------------------
WEB_HOST = "0.0.0.0"
WEB_PORT = int(os.getenv("CT_WEB_PORT", 8080))

# Maximum number of detection events kept in the ring buffer
MAX_DETECTION_LOG = 200

# Optional remote endpoint to POST detections to (leave empty to disable)
REMOTE_POST_URL = os.getenv("CT_REMOTE_URL", "")
