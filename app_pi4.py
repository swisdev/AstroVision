#!/usr/bin/env python3
"""
AstroVision (AV) — Native Raspberry Pi 4 Celestial Tracker & Observatory Hub.

Operating Modes:
1. MANUAL MODE (Default):
   - The servos are completely powered down (0 PWM signal) while waiting.
   - It ONLY moves when a command signal is received from the web dashboard
     (e.g. Left, Right, Up, Down, Center).
   - Once received, it turns the servo on, moves to the new angle, holds
     for 0.22s to settle, and IMMEDIATELY turns the signal off (value=None).
   - Absolute zero hum, zero vibration, zero jitter.

2. AUTO-TRACKING MODE (Enabled via website toggle):
   - Automatically and SLOWLY scans across the sky in gentle 0.5° increments.
   - Sweeps azimuth smoothly, steps elevation, and continues slow celestial patrol.
   - Shuts down the moment manual control is touched or the toggle is turned off.
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

# ── Global Lifecycle State ─────────────────────────────────────────────────
_running = True
_autotrack_thread: Optional[threading.Thread] = None

def _signal_handler(sig, frame):
    global _running
    log.info("Shutdown signal received (%d). Stopping AstroVision…", sig)
    _running = False

signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)

# ── Pin & Resolution Settings ──────────────────────────────────────────────
SERVO_PAN_PIN = int(os.getenv("AV_PAN_PIN", 17))    # BCM GPIO 17
SERVO_TILT_PIN = int(os.getenv("AV_TILT_PIN", 18))  # BCM GPIO 18

FRAME_WIDTH = 320
FRAME_HEIGHT = 240
FPS_TARGET = 15

# ── Shared Telemetry State ─────────────────────────────────────────────────
_state_lock = threading.Lock()
_state: Dict[str, Any] = {
    "tracking": False,
    "autotrack": False,         # Starts in quiet MANUAL MODE by default
    "pan_angle": 90.0,
    "tilt_angle": 90.0,
    "target": None,
    "fps": 0.0,
    "uptime_s": 0,
    "camera_type": "Initializing…",
}
_detection_log = deque(maxlen=100)
_latest_jpeg: Optional[bytes] = None


# ── Hardware Servo Driver (On-Demand Pulse & Immediate Kill) ───────────────
class Pi4ServoDriver:
    """
    Zero-Jitter On-Demand Servo Driver.
    
    In Manual Mode:
      - Does NOT emit continuous pulses.
      - When a move is requested:
          1. Turn on PWM output
          2. Move to target angle
          3. Sleep briefly (0.22s) so the physical horn reaches the angle
          4. Turn OFF PWM output completely (value=None)
      - The motor coils de-energize and mechanical friction holds position.
      - Jitter is physically impossible because there is zero signal on the wire.
    """

    def __init__(self, pan_pin: int = SERVO_PAN_PIN, tilt_pin: int = SERVO_TILT_PIN):
        self.pan_angle = 90.0
        self.tilt_angle = 90.0
        self.pan_servo = None
        self.tilt_servo = None
        self.is_hardware = False
        self._move_lock = threading.Lock()

        try:
            from gpiozero import AngularServo

            # Attempt to use pigpio hardware DMA factory if available
            pin_factory = None
            try:
                from gpiozero.pins.pigpio import PiGPIOFactory
                pin_factory = PiGPIOFactory()
                log.info("✔ Using pigpio hardware PWM backend.")
            except Exception:
                log.info("ℹ Using standard gpiozero backend.")

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

            # Center once at boot, then immediately cut power to the pins
            self.pulse_move(self.pan_angle, self.tilt_angle, hold_s=0.30)

        except Exception as exc:
            log.warning("Hardware servos unavailable (%s). Running in simulation mode.", exc)

    def pulse_move(self, pan: float, tilt: float, hold_s: float = 0.22):
        """
        Turns the servos on, sets angles, waits for physical travel,
        then immediately shuts off PWM output (value = None).
        """
        with self._move_lock:
            self.pan_angle = max(0.0, min(180.0, float(pan)))
            self.tilt_angle = max(0.0, min(180.0, float(tilt)))

            if self.is_hardware:
                try:
                    # 1. Turn on and send angle pulse
                    if self.pan_servo:
                        self.pan_servo.angle = self.pan_angle
                    if self.tilt_servo:
                        self.tilt_servo.angle = self.tilt_angle

                    # 2. Hold pulse long enough for physical movement
                    time.sleep(hold_s)

                    # 3. Kill signal completely
                    if self.pan_servo:
                        self.pan_servo.value = None
                    if self.tilt_servo:
                        self.tilt_servo.value = None
                except Exception as e:
                    log.debug("Servo pulse error: %s", e)

    def power_off(self):
        """Ensure PWM is silenced."""
        with self._move_lock:
            if self.is_hardware:
                try:
                    if self.pan_servo:
                        self.pan_servo.value = None
                    if self.tilt_servo:
                        self.tilt_servo.value = None
                except Exception:
                    pass

    def close(self):
        self.power_off()
        if self.is_hardware:
            try:
                if self.pan_servo:
                    self.pan_servo.close()
                if self.tilt_servo:
                    self.tilt_servo.close()
            except Exception:
                pass


# ── Auto-Tracking Slow Sky Scan Worker ─────────────────────────────────────
def _slow_scan_worker():
    """
    Runs ONLY when auto-tracking is enabled on the website.
    Slowly sweeps the sky:
    Pan: 25° -> 155° in gentle 0.5° increments every 0.35 seconds.
    Then steps Tilt by 5° and sweeps back.
    Between every step, PWM is killed to eliminate jitter.
    """
    global _running

    pan_min, pan_max = 25.0, 155.0
    tilt_min, tilt_max = 25.0, 80.0
    pan_step = 0.5         # Gentle 0.5° nudge
    tilt_step = 5.0        # Small tilt step
    step_delay = 0.35      # Calm 0.35s interval

    pan_dir = 1            # 1 = right, -1 = left

    log.info("🔭 Auto-Tracking Slow Sky Scan started.")

    while _running:
        with _state_lock:
            autotrack_active = _state["autotrack"]
            current_pan = _state["pan_angle"]
            current_tilt = _state["tilt_angle"]

        if not autotrack_active:
            break

        # Calculate next slow step
        next_pan = current_pan + (pan_step * pan_dir)
        next_tilt = current_tilt

        if next_pan >= pan_max:
            next_pan = pan_max
            pan_dir = -1
            next_tilt = current_tilt + tilt_step
        elif next_pan <= pan_min:
            next_pan = pan_min
            pan_dir = 1
            next_tilt = current_tilt + tilt_step

        if next_tilt > tilt_max:
            next_tilt = tilt_min

        # Move the servos gently with short hold, then silence
        if global_servos:
            global_servos.pulse_move(next_pan, next_tilt, hold_s=0.10)

        with _state_lock:
            _state["pan_angle"] = round(next_pan, 2)
            _state["tilt_angle"] = round(next_tilt, 2)

        time.sleep(step_delay)

    # When auto-tracking is stopped, ensure servos are completely powered off
    if global_servos:
        global_servos.power_off()
    log.info("🔭 Auto-Tracking Slow Sky Scan stopped.")


def set_autotrack(enabled: bool):
    """Starts or stops the slow auto-tracking background scanner."""
    global _autotrack_thread
    with _state_lock:
        _state["autotrack"] = enabled

    if enabled:
        if _autotrack_thread is None or not _autotrack_thread.is_alive():
            _autotrack_thread = threading.Thread(target=_slow_scan_worker, daemon=True)
            _autotrack_thread.start()
    else:
        if global_servos:
            global_servos.power_off()


# ── Dual-Mode Camera Driver (Picamera2 Ribbon + OpenCV USB) ────────────────
class Pi4CameraDriver:
    """Auto-detects either Raspberry Pi Ribbon Camera (Picamera2) or USB Webcam."""

    def __init__(self):
        self.mode = "none"
        self.picam2 = None
        self.cv_cap = None

        # Attempt 1: Native Picamera2 (Official Pi 4 ribbon camera stack)
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

        # Attempt 2: OpenCV USB webcam nodes (/dev/video0 through /dev/video4)
        for idx in [0, 1, 2, 3, 4]:
            for backend in [cv2.CAP_V4L2, cv2.CAP_ANY]:
                try:
                    cap = cv2.VideoCapture(idx, backend)
                    if not cap.isOpened():
                        continue
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
        frame[:] = (20, 14, 10)

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
            _, thresh_diff = cv2.threshold(diff, 35, 255, cv2.THRESH_BINARY)
            contours, _ = cv2.findContours(thresh_diff, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for cnt in contours:
                area = cv2.contourArea(cnt)
                if 20 <= area <= 1200:
                    M = cv2.moments(cnt)
                    if M["m00"] > 0:
                        cx = int(M["m10"] / M["m00"])
                        cy = int(M["m01"] / M["m00"])
                        self.prev_gray = gray
                        return {
                            "x": cx, "y": cy, "area": area,
                            "object_type": "moving_celestial",
                            "confidence": round(min(0.99, 0.70 + (area / 1200) * 0.28), 2),
                            "timestamp": timestamp,
                        }

        self.prev_gray = gray

        # 2. Bright Static Objects (Stars & Planets)
        # Use a high-value threshold to filter out dark webcam noise
        _, thresh_bright = cv2.threshold(blurred, 210, 255, cv2.THRESH_BINARY)
        contours, _ = cv2.findContours(thresh_bright, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        best_star = None
        best_area = 0

        for cnt in contours:
            area = cv2.contourArea(cnt)
            if 6 <= area <= 500 and area > best_area:
                M = cv2.moments(cnt)
                if M["m00"] > 0:
                    best_area = area
                    best_star = {
                        "x": int(M["m10"] / M["m00"]),
                        "y": int(M["m01"] / M["m00"]),
                        "area": area,
                        "object_type": "bright_star",
                        "confidence": round(min(0.98, 0.75 + (area / 500) * 0.22), 2),
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
    data["autoscan"] = data["autotrack"]
    return jsonify(data)

@app.route("/api/detections")
def api_detections():
    with _state_lock:
        return jsonify(list(_detection_log))

@app.route("/api/move", methods=["GET", "POST"])
def api_move():
    """
    Triggered ONLY when a user presses a direction button on the website.
    1. Pauses auto-tracking so user has manual control.
    2. Powers on the servos and moves to the requested angle.
    3. Holds for 0.22s to settle.
    4. Powers OFF the PWM signal completely (value = None).
    5. Waits silently for the next button press. Zero jitter.
    """
    # Manual press disables auto-tracking
    set_autotrack(False)

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

    # Move physically: turn on -> move -> hold 0.22s -> turn off completely!
    if global_servos:
        global_servos.pulse_move(pan, tilt, hold_s=0.22)

    return jsonify({"status": "ok", "direction": direction, "pan": pan, "tilt": tilt})

@app.route("/api/toggle_autotrack", methods=["GET", "POST"])
@app.route("/api/autoscan", methods=["GET", "POST"])
def api_toggle_autotrack():
    with _state_lock:
        current = _state["autotrack"]

    if request.is_json and "enabled" in (request.get_json(silent=True) or {}):
        new_val = bool(request.get_json()["enabled"])
    elif "enabled" in request.args:
        new_val = request.args.get("enabled", "").lower() in ("1", "true", "yes")
    else:
        new_val = not current

    set_autotrack(new_val)
    return jsonify({"status": "ok", "autotrack": new_val, "autoscan": new_val})

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

    # 2. Initialize Camera & Vision
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

    # 4. Main Vision & Telemetry Loop (Does NOT touch servos in manual mode)
    tick_delay = 1.0 / FPS_TARGET
    frame_count = 0
    start_time = time.monotonic()

    log.info("System Ready. In Manual Mode by default (servos quiet).")

    try:
        while _running:
            t0 = time.monotonic()
            ok, frame = camera.read_frame()

            if not ok or frame is None:
                time.sleep(0.05)
                continue

            # Detect Celestial Bodies (for UI overlay only)
            target = vision.detect(frame)
            tracking = target is not None

            # Current Angles
            with _state_lock:
                pan_val = _state["pan_angle"]
                tilt_val = _state["tilt_angle"]

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
                _state["target"] = target
                _state["fps"] = fps
                _state["uptime_s"] = uptime

            dt = time.monotonic() - t0
            sleep_t = tick_delay - dt
            if sleep_t > 0:
                time.sleep(sleep_t)

    finally:
        set_autotrack(False)
        camera.close()
        if global_servos:
            global_servos.close()
        log.info("AstroVision stopped cleanly.")

if __name__ == "__main__":
    main()
