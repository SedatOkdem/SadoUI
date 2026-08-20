"""Paint-color IFF: dominant red = düşman, dominant blue = dost (vehicle type kept)."""

from __future__ import annotations

from typing import Any, Optional

import cv2
import numpy as np

from aeroshield.workers.ipc import Detection

# HSV ranges (OpenCV H: 0–179)
_RED1 = ((0, 70, 50), (10, 255, 255))
_RED2 = ((170, 70, 50), (179, 255, 255))
_BLUE = ((95, 60, 40), (135, 255, 255))

_VEHICLES = {"Helikopter", "F16", "MiniIHA", "BalistikFuze"}


def dominant_red_or_blue(
    frame_bgr: np.ndarray,
    det: Detection,
    *,
    min_frac: float = 0.06,
    ratio: float = 1.2,
) -> Optional[str]:
    """Return 'red', 'blue', or None if neither clearly dominates in the bbox."""
    if frame_bgr is None or frame_bgr.size == 0:
        return None
    h, w = frame_bgr.shape[:2]
    x1 = int(max(0, min(w - 1, det.x)))
    y1 = int(max(0, min(h - 1, det.y)))
    x2 = int(max(0, min(w, det.x + det.w)))
    y2 = int(max(0, min(h, det.y + det.h)))
    if x2 - x1 < 8 or y2 - y1 < 8:
        return None

    # Shrink to body (avoid background edges)
    bw, bh = x2 - x1, y2 - y1
    pad_x = int(bw * 0.15)
    pad_y = int(bh * 0.15)
    x1, y1 = x1 + pad_x, y1 + pad_y
    x2, y2 = x2 - pad_x, y2 - pad_y
    if x2 - x1 < 6 or y2 - y1 < 6:
        return None

    roi = frame_bgr[y1:y2, x1:x2]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    red = cv2.inRange(hsv, np.array(_RED1[0]), np.array(_RED1[1]))
    red |= cv2.inRange(hsv, np.array(_RED2[0]), np.array(_RED2[1]))
    blue = cv2.inRange(hsv, np.array(_BLUE[0]), np.array(_BLUE[1]))

    area = float(roi.shape[0] * roi.shape[1])
    r_frac = float(cv2.countNonZero(red)) / area
    b_frac = float(cv2.countNonZero(blue)) / area
    if r_frac < min_frac and b_frac < min_frac:
        return None
    if r_frac >= b_frac * ratio and r_frac >= min_frac:
        return "red"
    if b_frac >= r_frac * ratio and b_frac >= min_frac:
        return "blue"
    return None


def apply_color_iff(
    frame_bgr: np.ndarray,
    detections: list[Detection],
    config: dict[str, Any] | None = None,
) -> list[Detection]:
    """Set hostile from paint; keep vehicle class (F16/Helikopter/…)."""
    cfg = (config or {}).get("color_iff") or {}
    if not bool(cfg.get("enabled", True)):
        return detections
    classes = set(cfg.get("classes") or list(_VEHICLES))
    min_frac = float(cfg.get("min_frac", 0.06))
    ratio = float(cfg.get("ratio", 1.2))

    for d in detections:
        if d.label not in classes and d.label != "Dost":
            continue
        verdict = dominant_red_or_blue(frame_bgr, d, min_frac=min_frac, ratio=ratio)
        if verdict == "blue":
            d.hostile = False
        elif verdict == "red":
            d.hostile = True
            if d.label == "Dost":
                d.label = "MiniIHA"
    return detections
