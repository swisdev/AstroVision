#!/usr/bin/env python3
"""
AstroVision (AV) — Native Raspberry Pi 4 Celestial Tracker & Observatory Hub.

Features:
- Camera: Native Picamera2 (CSI ribbon cameras) + OpenCV fallback (USB webcams).
- Servos: Hardware-timed PWM via pigpio when available + gpiozero fallback + active PWM signal-kill (zero jitter).
- Auto Sky Patrol: Automated celestial sweep / patrol mode to autonomously scan the night sky.
- Vision: Dual-mode OpenCV detector (bright stars + moving satellites/meteors) with P-control.
- Web UI: Clean dark-mode dashboard (Vercel/Linear style) with live stream, manual D-pad, and REST API.

Usage:
    sudo python3 app_pi4.py
    python3 app_pi4.py --no-servo
    python3 app_pi4.py --port 8080
"""

import argparse
import logging
import os
import signal
import sys
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
from flask import Flask, Response, jsonify, render_template, request

# ── Logging Configuration ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("AstroVision-Pi4")

# ── Global State & Lifecycle ───────────────────────────────────────────────
_running = True
_autoscan_active = False
_autoscan_thread: Optional[threading.Thread] = None

def stop_autoscan():
    global _autoscan_active, _autoscan_thread
    _autoscan_active = False
    if _autoscan_thread and _autoscan_thread.is_alive():
        _autoscan_thread.join(timeout=1.0)
    _autoscan_thread = None

def _signal_handler(sig, frame):
    global _running
    log.info("Shutdown signal received (%d). Stopping AstroVision…", sig)
    stop_autoscan()
    _running = False

signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)

# ── Configuration Constants ────────────────────────────────────────────────
SERVO_PAN_PIN = int(os.getenv("AV_PAN_PIN", 17))    # BCM GPIO 17
SERVO_TILT_PIN = int(os.getenv("AV_TILT_PIN", 18))  # BCM GPIO 18

FRAME_WIDTH = 320
FRAME_HEIGHT = 240
FPS_TARGET = 15

# Proportional Controller Gains & Anti-Jitter Tuning
KP_PAN = 0.025       # Gentle gain (prevents overshooting)
KP_TILT = 0.025      # Gentle gain
DEADZONE_PX = 16     # Generous deadband: if object is near center, don't twitch
MAX_STEP_DEG = 2.0   # Max degrees per step for smooth glide
MIN_STEP_DEG = 0.5   # Ignore microscopic steps below gear backlash

# ── Global State ───────────────────────────────────────────────────────────
_state_lock = threading.Lock()
_state: Dict[str, Any] = {
    "tracking": False,
    "autotrack": True,
    "autoscan": False,
    "pan_angle": 90.0,
    "tilt_angle": 90.0,
    "target": None,
    "fps": 0.0,
    "uptime_s": 0,
    "camera_type": "Initializing…",
}
_detection_log = deque(maxlen=100)
_latest_jpeg: Optional[bytes] = None


# ── Hardware Servo Controller (Zero-Jitter Pulse & Kill) ────────────────────
class Pi4ServoDriver:
    """
    Controls Pan/Tilt servos with ZERO jitter.
    
    1. Uses pigpio hardware DMA PWM when available (zero-jitter pulse timing).
    2. Falls back to gpiozero native pin factory.
    3. Actively kills the PWM signal (value=None) when idle so motor coils
       turn off completely, eliminating 100% of hum, buzz, and vibration!
    """

    def __init__(self, pan_pin: int = SERVO_PAN_PIN, tilt_pin: int = SERVO_TILT_PIN):
        self.pan_angle = 90.0
        self.tilt_angle = 90.0
        self.pan_servo = None
        self.tilt_servo = None
        self.is_hardware = False
        self._signal_active = False
        self._last_move_time = time.monotonic()

        # Coordinate smoothing filters (exponential moving average)
        self.smooth_x = float(FRAME_WIDTH // 2)
        self.smooth_y = float(FRAME_HEIGHT // 2)

        try:
            from gpiozero import AngularServo

            # Attempt 1: Try pigpio for hardware-timed PWM (rock solid)
            pin_factory = None
            try:
                from gpiozero.pins.pigpio import PiGPIOFactory
                pin_factory = PiGPIOFactory()
                log.info("✔ Connected to pigpio hardware PWM backend (zero-jitter mode).")
            except Exception:
                log.info("ℹ pigpio daemon not running, using standard gpiozero backend.")

            kwargs = {
                "min_angle": 0,
                "max_angle": 180,
                "min_pulse_width": 0.0005,
                "max_pulse_width": 0.0025,
            }
            if pin_factory:
                kwargs["pin_factory"] = pin_factory

            self.pan_servo = AngularServo(pan_pin, **kwargs)
            self.tilt_servo = AngularServo(tilt_pin, **kwargs)
            self.is_hardware = True
            log.info("✔ Servos initialized on GPIO %d (Pan) & %d (Tilt)", pan_pin, tilt_pin)

            # Move to default center, then immediately silence the PWM signal
            self._write_angles(self.pan_angle, self.tilt_angle)
            time.sleep(0.4)
            self._silence_pwm()

        except Exception as exc:
            log.warning("Hardware servos unavailable (%s). Running in simulation mode.", exc)

    def _write_angles(self, pan: float, tilt: float):
        """Send PWM pulse to turn the motors."""
        self.pan_angle = max(0.0, min(180.0, pan))
        self.tilt_angle = max(0.0, min(180.0, tilt))

        if self.is_hardware:
            try:
                if self.pan_servo:
                    self.pan_servo.angle = self.pan_angle
                if self.tilt_servo:
                    self.tilt_servo.angle = self.tilt_angle
                self._signal_active = True
                self._last_move_time = time.monotonic()
            except Exception as e:
                log.debug("Servo write error: %s", e)

    def _silence_pwm(self):
        """
        Kill PWM output on the GPIO pins completely.
        When value=None, gpiozero cuts off the signal pulse.
        Motor coils power down, mechanical gears hold position with friction,
        and all jitter, buzzing, and camera shake drops to ZERO.
        """
        if self.is_hardware and self._signal_active:
            try:
                if self.pan_servo:
                    self.pan_servo.value = None
                if self.tilt_servo:
                    self.tilt_servo.value = None
                self._signal_active = False
            except Exception:
                pass

    def set_angles(self, pan: float, tilt: float):
        """Command a movement to specified angles."""
        self._write_angles(pan, tilt)

    def tick_silence_check(self):
        """Automatically called on every frame. Kills PWM 0.25s after moving."""
        if self._signal_active and (time.monotonic() - self._last_move_time > 0.25):
            self._silence_pwm()

    def update_p_controller(self, raw_x: int, raw_y: int):
        # 1. Smooth out noisy camera detections with Exponential Moving Average
        self.smooth_x = 0.65 * self.smooth_x + 0.35 * float(raw_x)
        self.smooth_y = 0.65 * self.smooth_y + 0.35 * float(raw_y)

        cx, cy = FRAME_WIDTH // 2, FRAME_HEIGHT // 2
        err_x = self.smooth_x - cx
        err_y = self.smooth_y - cy

        # 2. Generous deadband: if target is near center, don't twitch
        if abs(err_x) < DEADZONE_PX:
            err_x = 0
        if abs(err_y) < DEADZONE_PX:
            err_y = 0

        if err_x == 0 and err_y == 0:
            return

        # 3. Smooth clamped proportional step
        delta_pan = max(-MAX_STEP_DEG, min(MAX_STEP_DEG, KP_PAN * err_x))
        delta_tilt = max(-MAX_STEP_DEG, min(MAX_STEP_DEG, KP_TILT * err_y))

        # 4. Only move if delta exceeds minimal gear backlash
        if abs(delta_pan) >= MIN_STEP_DEG or abs(delta_tilt) >= MIN_STEP_DEG:
            self._write_angles(self.pan_angle + delta_pan, self.tilt_angle + delta_tilt)

    def center(self):
        self._write_angles(90.0, 90.0)

    def close(self):
        self._silence_pwm()
        if self.is_hardware:
            try:
                if self.pan_servo:
                    self.pan_servo.close()
                if self.tilt_servo:
                    self.tilt_servo.close()
            except Exception:
                pass


# ── Auto Sky Patrol / Scan Worker ───────────────────────────────────────────
def _autoscan_worker():
    """
    Sweeps the celestial dome in an automated serpentine patrol pattern.
    Pans left to right (25° to 155°), steps up tilt by 8°, sweeps back,
    and resets smoothly when reaching zenith.
    """
    global _autoscan_active

    pan_min, pan_max = 25.0, 155.0
    tilt_min, tilt_max = 25.0, 85.0
    step_pan = 1.5       # degrees per tick
    step_tilt = 8.0      # degrees step between horizontal sweeps
    sweep_interval = 0.12 # seconds between steps

    current_pan = pan_min
    current_tilt = tilt_min
    pan_dir = 1  # 1 = right, -1 = left

    log.info("🔭 Auto Sky Patrol started: sweeping %.0f° to %.0f° pan, %.0f° to %.0f° tilt",
             pan_min, pan_max, tilt_min, tilt_max)

    while _autoscan_active and _running:
        # Move servos to patrol coordinates
        if global_servos:
            global_servos.set_angles(current_pan, current_tilt)

        with _state_lock:
            _state["pan_angle"] = round(current_pan, 2)
            _state["tilt_angle"] = round(current_tilt, 2)

        time.sleep(sweep_interval)

        # Advance pan along the sweep
        current_pan += step_pan * pan_dir

        # Hit edge? Step elevation (tilt) and reverse pan direction
        if current_pan >= pan_max:
            current_pan = pan_max
            pan_dir = -1
            current_tilt += step_tilt
        elif current_pan <= pan_min:
            current_pan = pan_min
            pan_dir = 1
            current_tilt += step_tilt

        # Reached top elevation? Reset to horizon
        if current_tilt > tilt_max:
            current_tilt = tilt_min
            log.info("🔭 Auto Sky Patrol completed a full sky pass. Resetting from horizon.")

    if global_servos:
        global_servos._silence_pwm()
    log.info("🔭 Auto Sky Patrol stopped.")


# ── Dual-Mode Camera Driver (Picamera2 Ribbon + OpenCV USB) ────────────────
class Pi4CameraDriver:
    """Auto-detects either Raspberry Pi Ribbon Camera (Picamera2) or USB Webcam."""

    def __init__(self):
        self.mode = "none"
        self.picam2 = None
        self.cv_cap = None

        # Attempt 1: Try Native Picamera2 (Official Pi 4 ribbon camera stack)
        try:
            from picamera2 import Picamera2
            picam2 = Picamera2()
            cam_config = picam2.create_preview_configuration(
                main={"size": (FRAME_WIDTH, FRAME_HEIGHT), "format": "BGR888"}
            )
            picam2.configure(cam_config)
            picam2.start()
            test_frame = picam2.capture_array()
            if test_frame is not None and test_frame.size > 0:
                self.picam2 = picam2
                self.mode = "picamera2"
                log.info("✔ Successfully initialized native Raspberry Pi Camera Module (Picamera2).")
                return
        except Exception as exc:
            log.debug("Picamera2 not active (%s). Checking USB cameras…", exc)

        # Attempt 2: Try OpenCV USB webcam nodes (/dev/video0 through /dev/video4)
        for idx in [0, 1, 2, 3, 4]:
            for backend in [cv2.CAP_V4L2, cv2.CAP_ANY]:
                try:
                    cap = cv2.VideoCapture(idx, backend)
                    if not cap.isOpened():
                        continue
                    # Grab test frame
                    for _ in range(3):
                        ok, test_frame = cap.read()
                        if ok and test_frame is not None and test_frame.size > 0:
                            self.cv_cap = cap
                            self.mode = f"opencv_usb_{idx}"
                            log.info("✔ Successfully initialized USB camera on /dev/video%d", idx)
                            return
                        time.sleep(0.05)
                    cap.release()
                except Exception:
                    continue

        log.warning("No physical camera detected. Running with built-in celestial simulation generator.")
        self.mode = "synthetic"

    def read_frame(self) -> Tuple[bool, np.ndarray]:
        if self.mode == "picamera2":
            try:
                frame = self.picam2.capture_array()
                return True, frame
            except Exception:
                return False, np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 3), dtype=np.uint8)

        elif self.mode.startswith("opencv"):
            ok, frame = self.cv_cap.read()
            if ok and frame is not None:
                h, w = frame.shape[:2]
                if w != FRAME_WIDTH or h != FRAME_HEIGHT:
                    frame = cv2.resize(frame, (FRAME_WIDTH, FRAME_HEIGHT), interpolation=cv2.INTER_AREA)
                return True, frame
            return False, np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 3), dtype=np.uint8)

        else:
            return True, self._generate_synthetic_sky()

    def _generate_synthetic_sky(self) -> np.ndarray:
        frame = np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 3), dtype=np.uint8)
        frame[:] = (20, 14, 10)  # Matte dark sky

        np.random.seed(99)
        sx = np.random.randint(4, FRAME_WIDTH - 4, 30)
        sy = np.random.randint(4, FRAME_HEIGHT - 4, 30)
        for x, y in zip(sx, sy):
            frame[y, x] = (220, 220, 220)

        t = (time.monotonic() * 14) % FRAME_WIDTH
        sat_x = int(t)
        sat_y = int(50 + np.sin(t / 20) * 20)
        cv2.circle(frame, (sat_x, sat_y), 2, (255, 255, 255), -1)
        cv2.line(frame, (max(0, sat_x - 10), sat_y), (sat_x, sat_y), (140, 160, 200), 1)
        return frame

    def close(self):
        if self.picam2:
            try:
                self.picam2.stop()
            except Exception:
                pass
        if self.cv_cap:
            try:
                self.cv_cap.release()
            except Exception:
                pass


# ── Computer Vision Detector ───────────────────────────────────────────────
class CelestialVision:
    def __init__(self):
        self.prev_gray = None

    def detect(self, frame: np.ndarray) -> Optional[Dict[str, Any]]:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        # 1. Motion Differencing (Satellites / Meteors)
        if self.prev_gray is not None:
            diff = cv2.absdiff(self.prev_gray, gray)
            _, thresh_diff = cv2.threshold(diff, 28, 255, cv2.THRESH_BINARY)
            contours, _ = cv2.findContours(thresh_diff, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for cnt in contours:
                area = cv2.contourArea(cnt)
                if 15 <= area <= 1500:
                    M = cv2.moments(cnt)
                    if M["m00"] > 0:
                        cx = int(M["m10"] / M["m00"])
                        cy = int(M["m01"] / M["m00"])
                        self.prev_gray = gray
                        return {
                            "x": cx, "y": cy, "area": area,
                            "object_type": "moving_celestial",
                            "confidence": round(min(0.99, 0.70 + (area / 1500) * 0.28), 2),
                            "timestamp": timestamp,
                        }

        self.prev_gray = gray

        # 2. Bright Static Objects (Stars & Planets)
        thresh_bright = cv2.adaptiveThreshold(
            blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 11, -2
        )
        contours, _ = cv2.findContours(thresh_bright, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        best_star = None
        best_area = 0

        for cnt in contours:
            area = cv2.contourArea(cnt)
            if 4 <= area <= 600 and area > best_area:
                M = cv2.moments(cnt)
                if M["m00"] > 0:
                    best_area = area
                    best_star = {
                        "x": int(M["m10"] / M["m00"]),
                        "y": int(M["m01"] / M["m00"]),
                        "area": area,
                        "object_type": "bright_star",
                        "confidence": round(min(0.98, 0.75 + (area / 600) * 0.22), 2),
                        "timestamp": timestamp,
                    }

        return best_star


# ── Flask Web Server ───────────────────────────────────────────────────────
app = Flask(__name__)
app.logger.setLevel(logging.WARNING)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/video_feed")
def video_feed():
    def generate():
        while _running:
            with _state_lock:
                frame_data = _latest_jpeg
            if frame_data:
                yield (b"--frame\r\n"
                       b"Content-Type: image/jpeg\r\n\r\n" + frame_data + b"\r\n")
            time.sleep(0.08)
    return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")

@app.route("/api/status")
def api_status():
    with _state_lock:
        data = dict(_state)
    data["autoscan"] = _autoscan_active
    return jsonify(data)

@app.route("/api/detections")
def api_detections():
    with _state_lock:
        return jsonify(list(_detection_log))

@app.route("/api/move", methods=["GET", "POST"])
def api_move():
    # Moving manually pauses auto-scan
    stop_autoscan()

    if request.method == "POST":
        data = request.get_json(silent=True) or request.form or {}
        direction = data.get("direction", request.args.get("direction", "")).lower()
        step = float(data.get("step", request.args.get("step", 5.0)))
    else:
        direction = request.args.get("direction", "").lower()
        step = float(request.args.get("step", 5.0))

    with _state_lock:
        pan = _state["pan_angle"]
        tilt = _state["tilt_angle"]

        if direction == "up":
            tilt = min(180.0, tilt + step)
        elif direction == "down":
            tilt = max(0.0, tilt - step)
        elif direction == "left":
            pan = max(0.0, pan - step)
        elif direction == "right":
            pan = min(180.0, pan + step)
        elif direction in ("center", "home"):
            pan, tilt = 90.0, 90.0

        _state["pan_angle"] = round(pan, 2)
        _state["tilt_angle"] = round(tilt, 2)

    # Move physical hardware servos
    if global_servos:
        global_servos.set_angles(pan, tilt)

    return jsonify({"status": "ok", "direction": direction, "pan": pan, "tilt": tilt})

@app.route("/api/toggle_autotrack", methods=["GET", "POST"])
def api_toggle_autotrack():
    with _state_lock:
        if request.is_json and "enabled" in (request.get_json(silent=True) or {}):
            _state["autotrack"] = bool(request.get_json()["enabled"])
        elif "enabled" in request.args:
            _state["autotrack"] = request.args.get("enabled", "").lower() in ("1", "true", "yes")
        else:
            _state["autotrack"] = not _state.get("autotrack", True)
        current = _state["autotrack"]
    return jsonify({"status": "ok", "autotrack": current})

@app.route("/api/autoscan", methods=["GET", "POST"])
@app.route("/api/toggle_scan", methods=["GET", "POST"])
def api_autoscan():
    global _autoscan_active, _autoscan_thread
    if _autoscan_active:
        stop_autoscan()
        current = False
    else:
        _autoscan_active = True
        _autoscan_thread = threading.Thread(target=_autoscan_worker, daemon=True)
        _autoscan_thread.start()
        current = True

    with _state_lock:
        _state["autoscan"] = current
    return jsonify({"status": "ok", "autoscan": current})

@app.route("/api/capture", methods=["GET", "POST"])
def api_capture():
    with _state_lock:
        pan = _state["pan_angle"]
        tilt = _state["tilt_angle"]
        tgt = _state["target"]
    snap_id = f"snap_{int(time.time())}"
    return jsonify({
        "status": "success",
        "snapshot_id": snap_id,
        "pan": pan, "tilt": tilt, "target": tgt,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })


# ── Main Controller Loop ───────────────────────────────────────────────────
global_servos: Optional[Pi4ServoDriver] = None

def main():
    global global_servos
    parser = argparse.ArgumentParser(description="AstroVision Pi 4 Autonomous Celestial Tracker")
    parser.add_argument("--no-servo", action="store_true", help="Run without physical servo hardware")
    parser.add_argument("--port", type=int, default=8080, help="Web dashboard port (default: 8080)")
    args = parser.parse_args()

    log.info("══════════════════════════════════════════════════════════")
    log.info("  AstroVision (AV) — Raspberry Pi 4 Celestial Observatory")
    log.info("══════════════════════════════════════════════════════════")

    # 1. Initialize Servos
    if not args.no_servo:
        global_servos = Pi4ServoDriver()
    else:
        log.info("Physical servos disabled (--no-servo mode).")

    # 2. Initialize Camera
    camera = Pi4CameraDriver()
    vision = CelestialVision()

    with _state_lock:
        _state["camera_type"] = camera.mode

    # 3. Launch Flask Daemon
    web_thread = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=args.port, threaded=True, use_reloader=False),
        daemon=True
    )
    web_thread.start()
    log.info("✔ Web Telemetry Dashboard active at http://0.0.0.0:%d", args.port)

    # 4. Main Control Loop
    tick_delay = 1.0 / FPS_TARGET
    frame_count = 0
    start_time = time.monotonic()

    log.info("Tracking active. Point your scope/camera toward the night sky.")

    try:
        while _running:
            t0 = time.monotonic()
            ok, frame = camera.read_frame()

            if not ok or frame is None:
                time.sleep(0.05)
                continue

            # Detect Celestial Bodies
            target = vision.detect(frame)
            tracking = target is not None

            # Retrieve Servo Angles & Auto-Track
            pan_val = global_servos.pan_angle if global_servos else _state["pan_angle"]
            tilt_val = global_servos.tilt_angle if global_servos else _state["tilt_angle"]

            with _state_lock:
                autotrack_on = _state["autotrack"]

            # Only run tracking corrections if not in auto sky patrol mode
            if tracking and autotrack_on and global_servos and not _autoscan_active:
                global_servos.update_p_controller(target["x"], target["y"])
                pan_val = global_servos.pan_angle
                tilt_val = global_servos.tilt_angle

            # Silence PWM pulse when servos finish moving (stops vibration!)
            if global_servos:
                global_servos.tick_silence_check()

            # Annotate Frame for Web Stream
            annotated = frame.copy()
            if target:
                tx, ty = target["x"], target["y"]
                col = (0, 60, 255) if target["object_type"] == "moving_celestial" else (255, 200, 0)
                cv2.circle(annotated, (tx, ty), 8, col, 2)
                cv2.drawMarker(annotated, (tx, ty), col, cv2.MARKER_CROSS, 16, 1)

            # Encode JPEG for Web
            ret, jpeg = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if ret:
                global _latest_jpeg
                with _state_lock:
                    _latest_jpeg = jpeg.tobytes()

            # Record Detection Event
            if tracking:
                event = {
                    "timestamp": target["timestamp"],
                    "object_type": target["object_type"],
                    "current_pan_angle": round(pan_val, 2),
                    "current_tilt_angle": round(tilt_val, 2),
                    "confidence_score": target["confidence"],
                }
                with _state_lock:
                    _detection_log.appendleft(event)

            # Update Telemetry State
            frame_count += 1
            uptime = int(time.monotonic() - start_time)
            fps = round(frame_count / max(1, uptime), 1)

            with _state_lock:
                _state["tracking"] = tracking
                _state["pan_angle"] = round(pan_val, 2)
                _state["tilt_angle"] = round(tilt_val, 2)
                _state["target"] = target
                _state["fps"] = fps
                _state["uptime_s"] = uptime

            dt = time.monotonic() - t0
            sleep_t = tick_delay - dt
            if sleep_t > 0:
                time.sleep(sleep_t)

    finally:
        stop_autoscan()
        camera.close()
        if global_servos:
            global_servos.close()
        log.info("AstroVision stopped cleanly.")

if __name__ == "__main__":
    main()
