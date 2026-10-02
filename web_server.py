"""
web_server.py — Lightweight Flask web server for the Celestial Tracker.

Endpoints
---------
GET  /              → Live HTML dashboard (auto-refreshes every 2 s).
GET  /api/status    → Current tracker state as JSON.
GET  /api/detections → Recent detection log as JSON array.
POST /api/forward   → Manually trigger a POST of the latest detection
                       to the configured REMOTE_POST_URL.

The server is started in a daemon thread so it never blocks the
main vision/servo loop.
"""

import json
import logging
import threading
import time
from collections import deque
from typing import Any, Dict, Optional

import requests
from flask import Flask, Response, jsonify, render_template_string, request

from config import WEB_HOST, WEB_PORT, MAX_DETECTION_LOG, REMOTE_POST_URL

log = logging.getLogger("web")

# ── Shared state (written by tracker loop, read by Flask handlers) ────────
_lock = threading.Lock()
_state: Dict[str, Any] = {
    "tracking": False,
    "pan_angle": 90.0,
    "tilt_angle": 90.0,
    "target": None,
    "fps": 0.0,
    "uptime_s": 0,
}
_detections: deque = deque(maxlen=MAX_DETECTION_LOG)


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
    # Fire-and-forget remote forwarding
    if REMOTE_POST_URL:
        _forward(det)


def _forward(payload: Dict) -> None:
    try:
        r = requests.post(REMOTE_POST_URL, json=payload, timeout=3)
        log.info("Forwarded detection → %s  [%d]", REMOTE_POST_URL, r.status_code)
    except Exception as exc:
        log.warning("Remote forward failed: %s", exc)


# ── Flask application ─────────────────────────────────────────────────────
app = Flask(__name__)
app.logger.setLevel(logging.WARNING)  # quieten Flask's default chatter

_DASHBOARD_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="2">
<title>Celestial Tracker</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body {
    font-family: 'Courier New', monospace;
    background: #0a0e17;
    color: #c8d6e5;
    padding: 1.5rem;
  }
  h1 { color: #f5cd79; margin-bottom: .6rem; font-size: 1.4rem; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
  .card {
    background: #141a2a;
    border: 1px solid #2e3a50;
    border-radius: 8px;
    padding: 1rem;
  }
  .card h2 { color: #78e08f; font-size: 1rem; margin-bottom: .5rem; }
  table { width: 100%; border-collapse: collapse; font-size: .85rem; }
  td, th {
    text-align: left;
    padding: 4px 8px;
    border-bottom: 1px solid #1e2a3a;
  }
  th { color: #82ccdd; }
  .badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: .75rem;
    font-weight: bold;
  }
  .badge-star   { background:#f5cd79; color:#0a0e17; }
  .badge-moving { background:#e55039; color:#fff; }
  .badge-track  { background:#78e08f; color:#0a0e17; }
  .badge-idle   { background:#576574; color:#fff; }
  .footer { margin-top: 1rem; font-size: .7rem; color: #576574; }
</style>
</head>
<body>
<h1>&#9733; Celestial Tracker Dashboard</h1>

<div class="grid">
  <div class="card">
    <h2>System Status</h2>
    <table>
      <tr><th>Tracking</th><td>
        {% if state.tracking %}
          <span class="badge badge-track">LOCKED</span>
        {% else %}
          <span class="badge badge-idle">SCANNING</span>
        {% endif %}
      </td></tr>
      <tr><th>Pan Angle</th><td>{{ state.pan_angle }}&deg;</td></tr>
      <tr><th>Tilt Angle</th><td>{{ state.tilt_angle }}&deg;</td></tr>
      <tr><th>FPS</th><td>{{ state.fps }}</td></tr>
      <tr><th>Uptime</th><td>{{ state.uptime_s }} s</td></tr>
    </table>
  </div>

  <div class="card">
    <h2>Current Target</h2>
    {% if state.target %}
    <table>
      <tr><th>Type</th><td>
        {% if state.target.object_type == 'bright_star' %}
          <span class="badge badge-star">{{ state.target.object_type }}</span>
        {% else %}
          <span class="badge badge-moving">{{ state.target.object_type }}</span>
        {% endif %}
      </td></tr>
      <tr><th>Position</th><td>({{ state.target.x }}, {{ state.target.y }})</td></tr>
      <tr><th>Confidence</th><td>{{ state.target.confidence }}</td></tr>
    </table>
    {% else %}
    <p style="color:#576574">No target acquired.</p>
    {% endif %}
  </div>
</div>

<div class="card" style="margin-top:1rem">
  <h2>Recent Detections (last {{ detections|length }})</h2>
  <table>
    <tr><th>Time</th><th>Type</th><th>Pan</th><th>Tilt</th><th>Conf</th></tr>
    {% for d in detections[:20] %}
    <tr>
      <td>{{ d.timestamp }}</td>
      <td>
        {% if d.object_type == 'bright_star' %}
          <span class="badge badge-star">{{ d.object_type }}</span>
        {% else %}
          <span class="badge badge-moving">{{ d.object_type }}</span>
        {% endif %}
      </td>
      <td>{{ d.current_pan_angle }}&deg;</td>
      <td>{{ d.current_tilt_angle }}&deg;</td>
      <td>{{ d.confidence_score }}</td>
    </tr>
    {% endfor %}
  </table>
</div>

<p class="footer">Auto-refreshes every 2 s &middot; GET <code>/api/status</code> or <code>/api/detections</code> for JSON</p>
</body>
</html>
"""


@app.route("/")
def dashboard():
    with _lock:
        state = dict(_state)
        dets  = list(_detections)
    return render_template_string(_DASHBOARD_HTML, state=state, detections=dets)


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
    """Manually POST the latest detection to REMOTE_POST_URL."""
    if not REMOTE_POST_URL:
        return jsonify({"error": "REMOTE_POST_URL not configured"}), 400
    with _lock:
        if not _detections:
            return jsonify({"error": "No detections yet"}), 404
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
    log.info("Web server listening on http://%s:%d", WEB_HOST, WEB_PORT)
    return t
