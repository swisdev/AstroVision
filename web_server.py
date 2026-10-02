"""
web_server.py — Lightweight Flask web server for the AstroVision Celestial Tracker.

Endpoints
---------
GET  /                   → Futuristic observatory dashboard (renders templates/index.html).
GET  /video_feed         → MJPEG video stream (live camera feed or observatory generator).
GET  /api/status         → Current tracker state as JSON.
GET  /api/detections     → Recent detection log as JSON array.
POST /api/forward        → Manually trigger a POST of the latest detection to REMOTE_POST_URL.
POST /api/servo/jog      → Interactive manual 4-axis jog control (direction, step).
POST /api/servo/center   → Re-center servos to 90°, 90°.
GET/POST /api/vision/settings → Real-time CV toggles & slider adjustments.

The server is started in a daemon thread so it never blocks the main vision/servo loop.
"""

import json
import logging
import threading
import time
from collections import deque
from typing import Any, Dict, Optional

import cv2
import numpy as np
import requests as http_requests
from flask import Flask, Response, jsonify, render_template, request

from config import WEB_HOST, WEB_PORT, MAX_DETECTION_LOG, REMOTE_POST_URL

log = logging.getLogger("web")

# ── Shared state (written by tracker loop, read by Flask handlers) ────────
_lock = threading.Lock()
_state: Dict[str, Any] = {
    "tracking": False,
    "pan_angle": 90.0,
    "tilt_angle": 90.0,
    "target": None,
    "fps": 10.0,
    "uptime_s": 0,
}
_detections: deque = deque(maxlen=MAX_DETECTION_LOG)
_latest_frame_jpeg: Optional[bytes] = None
_servo_ref: Any = None

# Real-time vision settings shared with the pipeline
_vision_settings: Dict[str, Any] = {
    "motion_filter": True,
    "bounding_boxes": True,
    "trajectories": True,
    "coords_display": True,
    "threshold": 30,
    "min_contour_area": 20,
    "gain": 50,
}


def register_servo_controller(servo_inst: Any) -> None:
    """Register the servo controller instance so the web jog pad can drive it."""
    global _servo_ref
    with _lock:
        _servo_ref = servo_inst


def update_frame(jpeg_bytes: bytes) -> None:
    """Publish a newly encoded JPEG frame to the MJPEG stream."""
    global _latest_frame_jpeg
    with _lock:
        _latest_frame_jpeg = jpeg_bytes


def update_state(*, tracking: bool, pan: float, tilt: float,
                 target: Optional[Dict], fps: float, uptime: int) -> None:
    """Called by the main loop each tick to publish state."""
    with _lock:
        _state["tracking"]   = tracking
        _state["pan_angle"]  = round(pan, 2)
        _state["tilt_angle"] = round(tilt, 2)
        _state["target"]     = target
        _state["fps"]        = round(fps, 1)
        _state["uptime_s"]   = uptime


def log_detection(det: Dict) -> None:
    """Append a detection event and optionally POST it remotely."""
    with _lock:
        _detections.appendleft(det)
    if REMOTE_POST_URL:
        _forward(det)


def get_vision_settings() -> Dict[str, Any]:
    with _lock:
        return dict(_vision_settings)


def _forward(payload: Dict) -> None:
    try:
        r = http_requests.post(REMOTE_POST_URL, json=payload, timeout=3)
        log.info("Forwarded detection → %s [%d]", REMOTE_POST_URL, r.status_code)
    except Exception as exc:
        log.warning("Remote forward failed: %s", exc)


# ── Synthetic Video Generator (Fallback when camera isn't active) ─────────
def _generate_synthetic_frame(tick: int) -> bytes:
    """Create a high-tech synthetic dark-sky frame with stars and targets."""
    w, h = 320, 240
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    
    # Deep space background with subtle gradient
    frame[:] = (12, 8, 5)

    # Random pseudo-stars seeded by position
    np.random.seed(42)
    star_x = np.random.randint(0, w, 40)
    star_y = np.random.randint(0, h, 40)
    brightness = np.random.randint(80, 240, 40)
    for sx, sy, b in zip(star_x, star_y, brightness):
        frame[sy, sx] = (b, b, b)

    # Moving satellite streak
    t = tick % 100
    sat_x = int(30 + t * 2.6)
    sat_y = int(40 + t * 1.5)
    if 0 <= sat_x < w and 0 <= sat_y < h:
        cv2.circle(frame, (sat_x, sat_y), 2, (255, 255, 255), -1)
        cv2.line(frame, (max(0, sat_x - 12), max(0, sat_y - 7)), (sat_x, sat_y), (180, 180, 220), 1)

    # Timestamp stamp
    ts = time.strftime("%H:%M:%S")
    cv2.putText(frame, f"ASTROVISION OBS // {ts}", (8, 230),
                cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 240, 255), 1, cv2.LINE_AA)

    _, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
    return jpeg.tobytes()


# ── Flask Application ─────────────────────────────────────────────────────
app = Flask(__name__)
app.logger.setLevel(logging.WARNING)


@app.route("/")
def index():
    """Render the futuristic dark-mode observatory dashboard."""
    return render_template("index.html")


@app.route("/video_feed")
def video_feed():
    """Stream live MJPEG frames from the camera or the fallback generator."""
    def frame_stream():
        tick = 0
        while True:
            with _lock:
                frame_bytes = _latest_frame_jpeg

            if frame_bytes is None:
                # Generate synthetic observatory video frame
                frame_bytes = _generate_synthetic_frame(tick)

            yield (b"--frame\r\n"
                   b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n")
            tick += 1
            time.sleep(0.1)  # 10 FPS stream

    return Response(frame_stream(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/api/status")
def api_status():
    with _lock:
        return jsonify(_state)


@app.route("/api/detections")
def api_detections():
    with _lock:
        return jsonify(list(_detections))


@app.route("/api/forward", methods=["POST"])
def api_forward():
    if not REMOTE_POST_URL:
        return jsonify({"error": "REMOTE_POST_URL not configured in config.py"}), 400
    with _lock:
        if not _detections:
            return jsonify({"error": "No detections logged yet"}), 404
        payload = _detections[0]
    _forward(payload)
    return jsonify({"status": "forwarded", "payload": payload})


@app.route("/api/servo/jog", methods=["POST"])
def api_servo_jog():
    """Handle 4-axis jog pad inputs from the web UI."""
    data = request.get_json(silent=True) or {}
    direction = data.get("direction", "").lower()
    step = float(data.get("step", 2.0))

    with _lock:
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

        _state["pan_angle"] = round(pan, 2)
        _state["tilt_angle"] = round(tilt, 2)

        if _servo_ref and hasattr(_servo_ref, "set_angles"):
            _servo_ref.set_angles(_state["pan_angle"], _state["tilt_angle"])

    return jsonify({"status": "ok", "pan": _state["pan_angle"], "tilt": _state["tilt_angle"]})


@app.route("/api/servo/center", methods=["POST"])
def api_servo_center():
    """Recalibrate servos to default center (90°, 90°)."""
    with _lock:
        _state["pan_angle"] = 90.0
        _state["tilt_angle"] = 90.0
        if _servo_ref and hasattr(_servo_ref, "set_angles"):
            _servo_ref.set_angles(90.0, 90.0)

    return jsonify({"status": "centered", "pan": 90.0, "tilt": 90.0})


@app.route("/api/vision/settings", methods=["GET", "POST"])
def api_vision_settings():
    """Read or update dynamic computer vision options."""
    global _vision_settings
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        with _lock:
            for k in _vision_settings.keys():
                if k in data:
                    _vision_settings[k] = data[k]
        return jsonify({"status": "updated", "settings": _vision_settings})

    with _lock:
        return jsonify(_vision_settings)


def start_server() -> threading.Thread:
    """Launch Flask in a daemon thread and return the thread handle."""
    t = threading.Thread(
        target=lambda: app.run(
            host=WEB_HOST, port=WEB_PORT,
            threaded=True, use_reloader=False,
        ),
        daemon=True,
    )
    t.start()
    log.info("AstroVision Web Hub listening on http://%s:%d", WEB_HOST, WEB_PORT)
    return t
