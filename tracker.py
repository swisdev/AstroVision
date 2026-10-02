#!/usr/bin/env python3
"""
tracker.py — Main entry point for the Celestial Tracker.

Orchestrates the vision pipeline, servo controller, and web server.

Usage
-----
    sudo python3 tracker.py            # needs root for pigpio
    sudo python3 tracker.py --no-servo  # vision-only mode (for dev)

Environment overrides (see config.py for full list):
    CT_PAN_PIN=17  CT_TILT_PIN=18  CT_WEB_PORT=8080  python3 tracker.py
"""

import argparse
import logging
import signal
import sys
import time

import cv2

from config import (
    CAMERA_INDEX, FRAME_WIDTH, FRAME_HEIGHT, FPS_LIMIT,
)
from vision import VisionPipeline, Detection
from servo_controller import ServoController
from web_server import (
    start_server, update_state, log_detection, update_frame,
    register_servo_controller, is_autotrack_enabled,
)

# ── Logging ───────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-8s  %(levelname)-5s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("tracker")

# ── Graceful shutdown ─────────────────────────────────────────────────────
_running = True

def _sig_handler(sig, frame):
    global _running
    log.info("Caught signal %d — shutting down.", sig)
    _running = False

signal.signal(signal.SIGINT,  _sig_handler)
signal.signal(signal.SIGTERM, _sig_handler)


def _pick_target(detections: list) -> Detection | None:
    """
    Select the single best target from this frame's detections.

    Priority:  moving objects first (they are transient), then brightest
    star.  Within each category, highest confidence wins.
    """
    moving = [d for d in detections if d.object_type == "moving_celestial"]
    if moving:
        return moving[0]  # already sorted by confidence
    bright = [d for d in detections if d.object_type == "bright_star"]
    if bright:
        return bright[0]
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Celestial Tracker")
    parser.add_argument(
        "--no-servo", action="store_true",
        help="Disable servo output (for desktop development)",
    )
    args = parser.parse_args()

    # ── Initialise components ─────────────────────────────────────────
    log.info("Starting Celestial Tracker …")

    vision = VisionPipeline()
    servo: ServoController | None = None
    if not args.no_servo:
        servo = ServoController()
        register_servo_controller(servo)
    else:
        log.info("Servo output disabled (--no-servo).")

    web_thread = start_server()

    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        log.error("Cannot open camera %d. Exiting.", CAMERA_INDEX)
        sys.exit(1)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
    cap.set(cv2.CAP_PROP_BUFFERSIZE,   1)  # minimise latency
    log.info("Camera opened: %dx%d", FRAME_WIDTH, FRAME_HEIGHT)

    tick_interval = 1.0 / FPS_LIMIT
    start_time    = time.monotonic()
    frame_count   = 0
    fps           = 0.0

    # ── Main loop ─────────────────────────────────────────────────────
    try:
        while _running:
            t0 = time.monotonic()

            ok, frame = cap.read()
            if not ok:
                log.warning("Frame grab failed — retrying.")
                time.sleep(0.1)
                continue

            # Down-sample if the camera ignores our requested resolution
            h, w = frame.shape[:2]
            if w != FRAME_WIDTH or h != FRAME_HEIGHT:
                frame = cv2.resize(
                    frame, (FRAME_WIDTH, FRAME_HEIGHT),
                    interpolation=cv2.INTER_AREA,
                )

            # ── Vision ────────────────────────────────────────────────
            detections = vision.process(frame)
            target = _pick_target(detections)

            tracking = target is not None

            # ── Servo update ──────────────────────────────────────────
            pan_angle  = servo.pan_angle  if servo else 90.0
            tilt_angle = servo.tilt_angle if servo else 90.0

            if tracking and servo and is_autotrack_enabled():
                servo.update(target.x, target.y)
                pan_angle  = servo.pan_angle
                tilt_angle = servo.tilt_angle

            # ── Build detection payload & log ─────────────────────────
            if tracking:
                payload = {
                    "timestamp":          target.timestamp,
                    "object_type":        target.object_type,
                    "current_pan_angle":  round(pan_angle, 2),
                    "current_tilt_angle": round(tilt_angle, 2),
                    "confidence_score":   target.confidence,
                }
                log_detection(payload)

            # ── Publish state for the web dashboard ───────────────────
            frame_count += 1
            elapsed = time.monotonic() - start_time
            if frame_count % FPS_LIMIT == 0:
                fps = frame_count / elapsed if elapsed > 0 else 0

            target_dict = None
            if target:
                target_dict = {
                    "x": target.x,
                    "y": target.y,
                    "object_type": target.object_type,
                    "confidence":  target.confidence,
                }

            update_state(
                tracking=tracking,
                pan=pan_angle,
                tilt=tilt_angle,
                target=target_dict,
                fps=fps,
                uptime=int(elapsed),
            )

            # ── Stream frame to web server ────────────────────────────
            ret, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
            if ret:
                update_frame(jpeg.tobytes())

            # ── Throttle to FPS_LIMIT ─────────────────────────────────
            dt = time.monotonic() - t0
            sleep_time = tick_interval - dt
            if sleep_time > 0:
                time.sleep(sleep_time)

    finally:
        cap.release()
        if servo:
            servo.stop()
        log.info("Tracker stopped.  Processed %d frames.", frame_count)


if __name__ == "__main__":
    main()
