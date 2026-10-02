"""
vision.py — Computer-vision pipeline for celestial-body detection.

Two complementary detectors run on every frame:

1. **Bright-object detector** (stars / planets)
   Gaussian blur → grayscale → adaptive threshold → contour filter.

2. **Moving-object detector** (satellites / meteors)
   Absolute frame-difference → binary threshold → contour filter.

Both return lightweight Detection dataclass instances.
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import cv2
import numpy as np

from config import (
    GAUSSIAN_KERNEL, GAUSSIAN_SIGMA,
    ADAPTIVE_BLOCK_SIZE, ADAPTIVE_C,
    MIN_BRIGHT_AREA, MAX_BRIGHT_AREA,
    DIFF_THRESHOLD,
    MIN_MOVING_AREA, MAX_MOVING_AREA,
    CONFIDENCE_BRIGHT_BASE, CONFIDENCE_MOVE_BASE,
)

log = logging.getLogger("vision")


@dataclass
class Detection:
    """A single detected object in a frame."""
    x: int                          # centroid X (pixels)
    y: int                          # centroid Y (pixels)
    area: float                     # contour area (pixels²)
    object_type: str                # "bright_star" | "moving_celestial"
    confidence: float = 0.0         # 0.0 – 1.0
    timestamp: str    = ""          # ISO-8601


class VisionPipeline:
    """
    Stateful pipeline that keeps the previous grey frame for
    frame-differencing and exposes a single `process()` call.
    """

    def __init__(self):
        self._prev_grey: Optional[np.ndarray] = None

    # ------------------------------------------------------------------
    def process(self, frame: np.ndarray) -> List[Detection]:
        """
        Run both detectors on *frame* (BGR uint8) and return a merged
        list of Detection objects sorted by descending confidence.
        """
        grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(grey, GAUSSIAN_KERNEL, GAUSSIAN_SIGMA)
        now = time.strftime("%Y-%m-%dT%H:%M:%S%z")

        detections: List[Detection] = []
        detections.extend(self._detect_bright(blurred, now))
        detections.extend(self._detect_moving(grey, now))

        # Keep previous frame for next diff
        self._prev_grey = grey

        detections.sort(key=lambda d: d.confidence, reverse=True)
        return detections

    # ------------------------------------------------------------------
    # Bright-object detector
    # ------------------------------------------------------------------
    def _detect_bright(
        self, blurred: np.ndarray, ts: str
    ) -> List[Detection]:
        thresh = cv2.adaptiveThreshold(
            blurred, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            ADAPTIVE_BLOCK_SIZE,
            -ADAPTIVE_C,            # negative C → keep bright peaks
        )
        contours, _ = cv2.findContours(
            thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        results: List[Detection] = []
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < MIN_BRIGHT_AREA or area > MAX_BRIGHT_AREA:
                continue
            M = cv2.moments(cnt)
            if M["m00"] == 0:
                continue
            cx = int(M["m10"] / M["m00"])
            cy = int(M["m01"] / M["m00"])

            # Confidence rises with brightness-area, capped at 1.0
            conf = min(1.0, CONFIDENCE_BRIGHT_BASE + area / MAX_BRIGHT_AREA * 0.3)
            results.append(Detection(
                x=cx, y=cy, area=area,
                object_type="bright_star",
                confidence=round(conf, 3),
                timestamp=ts,
            ))
        return results

    # ------------------------------------------------------------------
    # Moving-object detector (frame differencing)
    # ------------------------------------------------------------------
    def _detect_moving(
        self, grey: np.ndarray, ts: str
    ) -> List[Detection]:
        if self._prev_grey is None:
            return []

        diff = cv2.absdiff(self._prev_grey, grey)
        _, mask = cv2.threshold(diff, DIFF_THRESHOLD, 255, cv2.THRESH_BINARY)

        # Light morphological cleanup
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        mask = cv2.dilate(mask, kernel, iterations=1)

        contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        results: List[Detection] = []
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < MIN_MOVING_AREA or area > MAX_MOVING_AREA:
                continue
            M = cv2.moments(cnt)
            if M["m00"] == 0:
                continue
            cx = int(M["m10"] / M["m00"])
            cy = int(M["m01"] / M["m00"])

            conf = min(1.0, CONFIDENCE_MOVE_BASE + area / MAX_MOVING_AREA * 0.4)
            results.append(Detection(
                x=cx, y=cy, area=area,
                object_type="moving_celestial",
                confidence=round(conf, 3),
                timestamp=ts,
            ))
        return results
