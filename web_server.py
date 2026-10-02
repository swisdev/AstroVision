"""
web_server.py — Lightweight Flask web server for the AstroVision Celestial Tracker.

Endpoints
---------
GET  /                   → Clean, modern Vercel/Linear-inspired dark dashboard.
GET  /video_feed         → MJPEG video stream (live camera feed or sleek observatory fallback).
GET  /api/status         → Current tracker state as JSON (angles, autotrack, target, fps).
GET  /api/detections     → Recent detection log as JSON array.
POST/GET /api/move       → Tactile D-Pad control: direction (up, down, left, right, center), step.
POST /api/servo/jog      → Alternative jog payload ({ "direction": "...", "step": ... }).
POST /api/servo/center   → Re-center servos to 90°, 90°.
POST/GET /api/toggle_autotrack → Toggle or set auto-tracking mode.
POST/GET /api/capture    → Capture a high-resolution snapshot frame.
POST /api/forward        → Manually forward latest detection to REMOTE_POST_URL.

The server is started in a daemon thread so it never blocks the main vision/servo loop.
"""

import json
import logging
import os
import threading
import time
from collections import deque
from typing import Any, Dict, Optional

import cv2
import numpy as np
import requests as http_requests
from flask import Flask, Response, jsonify, render_template, request, send_file

from config import WEB_HOST, WEB_PORT, MAX_DETECTION_LOG, REMOTE_POST_URL

log = logging.getLogger("web")

# ── Shared state (written by tracker loop, read by Flask handlers) ────────
_lock = threading.Lock()
_state: Dict[str, Any] = {
    "tracking": False,
    "autotrack": True,
    "pan_angle": 90.0,
    "tilt_angle": 90.0,
    "target": None,
    "fps": 10.0,
    "uptime_s": 0,
    "last_snapshot": None,
}
_detections: deque = deque(maxlen=MAX_DETECTION_LOG)
_latest_frame_jpeg: Optional[bytes] = None
_servo_ref: Any = None


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


def is_autotrack_enabled() -> bool:
    with _lock:
        return _state.get("autotrack", True)


def _forward(payload: Dict) -> None:
    try:
        r = http_requests.post(REMOTE_POST_URL, json=payload, timeout=3)
        log.info("Forwarded detection → %s [%d]", REMOTE_POST_URL, r.status_code)
    except Exception as exc:
        log.warning("Remote forward failed: %s", exc)


# ── Synthetic Video Generator (Fallback when camera isn't active) ─────────
def _generate_synthetic_frame(tick: int) -> bytes:
    """Create a clean, minimalist astronomical frame."""
    w, h = 320, 240
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    
    # Deep matte background (#0B0F17)
    frame[:] = (23, 15, 11)

    # Subtle celestial field
    np.random.seed(42)
    star_x = np.random.randint(4, w - 4, 35)
    star_y = np.random.randint(4, h - 4, 35)
    brightness = np.random.randint(90, 230, 35)
    for sx, sy, b in zip(star_x, star_y, brightness):
        frame[sy, sx] = (b, b, b)

    # Slowly drifting target object
    t = tick % 120
    sat_x = int(35 + t * 2.1)
    sat_y = int(50 + t * 1.1)
    if 0 <= sat_x < w and 0 <= sat_y < h:
        cv2.circle(frame, (sat_x, sat_y), 2, (255, 255, 255), -1)
        # Subtle clean tail
        cv2.line(frame, (max(0, sat_x - 8), max(0, sat_y - 4)), (sat_x, sat_y), (140, 140, 160), 1)

    # Clean bottom telemetry stamp
    ts = time.strftime("%H:%M:%S")
    cv2.putText(frame, f"ASTROVISION // {ts}", (10, 226),
                cv2.FONT_HERSHEY_SIMPLEX, 0.32, (180, 190, 205), 1, cv2.LINE_AA)

    _, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
    return jpeg.tobytes()


# ── Flask Application ─────────────────────────────────────────────────────
app = Flask(__name__)
app.logger.setLevel(logging.WARNING)


@app.route("/")
def index():
    """Render the clean dark-mode dashboard."""
    return render_template("index.html")


@app.route("/video_feed")
def video_feed():
    """Stream live MJPEG frames from the camera or fallback generator."""
    def frame_stream():
        tick = 0
        while True:
            with _lock:
                frame_bytes = _latest_frame_jpeg

            if frame_bytes is None:
                frame_bytes = _generate_synthetic_frame(tick)

            yield (b"--frame\r\n"
                   b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n")
            tick += 1
            time.sleep(0.1)

    return Response(frame_stream(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/api/status")
def api_status():
    with _lock:
        return jsonify(_state)


@app.route("/api/detections")
def api_detections():
    with _lock:
        return jsonify(list(_detections))


@app.route("/api/move", methods=["GET", "POST"])
def api_move():
    """
    Tactile D-Pad endpoint.
    Accepts query params or JSON body: ?direction=up|down|left|right|center&step=5
    """
    if request.method == "POST":
        data = request.get_json(silent=True) or request.form or {}
        direction = data.get("direction", request.args.get("direction", "")).lower()
        step = float(data.get("step", request.args.get("step", 5.0)))
    else:
        direction = request.args.get("direction", "").lower()
        step = float(request.args.get("step", 5.0))

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
        elif direction in ("center", "home"):
            pan = 90.0
            tilt = 90.0

        _state["pan_angle"] = round(pan, 2)
        _state["tilt_angle"] = round(tilt, 2)

        if _servo_ref and hasattr(_servo_ref, "set_angles"):
            _servo_ref.set_angles(_state["pan_angle"], _state["tilt_angle"])

    return jsonify({
        "status": "ok",
        "direction": direction,
        "step": step,
        "pan": _state["pan_angle"],
        "tilt": _state["tilt_angle"]
    })


@app.route("/api/servo/jog", methods=["POST"])
def api_servo_jog():
    """Backwards-compatible alias for /api/move."""
    return api_move()


@app.route("/api/servo/center", methods=["POST", "GET"])
def api_servo_center():
    """Recalibrate servos to default center (90°, 90°)."""
    with _lock:
        _state["pan_angle"] = 90.0
        _state["tilt_angle"] = 90.0
        if _servo_ref and hasattr(_servo_ref, "set_angles"):
            _servo_ref.set_angles(90.0, 90.0)

    return jsonify({"status": "centered", "pan": 90.0, "tilt": 90.0})


@app.route("/api/toggle_autotrack", methods=["GET", "POST"])
def api_toggle_autotrack():
    """Toggle or explicitly set the Auto-Tracking mode."""
    with _lock:
        if request.is_json and "enabled" in (request.get_json(silent=True) or {}):
            _state["autotrack"] = bool(request.get_json()["enabled"])
        elif "enabled" in request.args:
            _state["autotrack"] = request.args.get("enabled", "").lower() in ("1", "true", "yes")
        else:
            _state["autotrack"] = not _state.get("autotrack", True)
        current = _state["autotrack"]

    return jsonify({"status": "ok", "autotrack": current})


@app.route("/api/capture", methods=["GET", "POST"])
def api_capture():
    """Snapshot the current camera frame and return metadata / payload."""
    with _lock:
        frame_bytes = _latest_frame_jpeg
        pan = _state["pan_angle"]
        tilt = _state["tilt_angle"]
        target = _state["target"]

    if frame_bytes is None:
        frame_bytes = _generate_synthetic_frame(int(time.time() * 10))

    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    snap_id = f"snap_{int(time.time())}"

    # Log as capture event
    with _lock:
        _state["last_snapshot"] = {
            "id": snap_id,
            "timestamp": timestamp,
            "pan": pan,
            "tilt": tilt,
            "target": target,
        }

    return jsonify({
        "status": "success",
        "snapshot_id": snap_id,
        "timestamp": timestamp,
        "pan": pan,
        "tilt": tilt,
        "target": target,
        "message": "Snapshot successfully captured"
    })


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
