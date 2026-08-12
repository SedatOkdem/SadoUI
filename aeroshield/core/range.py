"""Monocular range estimation from bounding-box height."""

from __future__ import annotations

import math
from typing import Any, Optional

from aeroshield.core.modes import normalize_label


def focal_length_px(config: dict[str, Any]) -> float:
    est = config.get("range_estimator", {})
    if est.get("focal_length_px"):
        return float(est["focal_length_px"])
    cam = config.get("camera", {})
    width = float(cam.get("width", 1280))
    fov_deg = float(est.get("fov_deg", 78.0))
    half = math.radians(fov_deg / 2.0)
    if half <= 0:
        return width
    return width / (2.0 * math.tan(half))


def object_height_m(config: dict[str, Any], label: str) -> float:
    est = config.get("range_estimator", {})
    heights = est.get("object_heights_m") or {}
    lab = normalize_label(label)
    if lab in heights:
        return float(heights[lab])
    return float(heights.get("default", 0.55))


def estimate_range_raw(
    bbox_h: float,
    config: dict[str, Any],
    label: str = "default",
) -> Optional[float]:
    est = config.get("range_estimator", {})
    if not est.get("enabled", True):
        return None
    if bbox_h <= 1.0:
        return None

    cal = est.get("calibration") or {}
    ref_range = cal.get("range_m")
    ref_h_px = cal.get("bbox_height_px")
    if ref_range and ref_h_px:
        distance = float(ref_range) * float(ref_h_px) / float(bbox_h)
    else:
        f_px = focal_length_px(config)
        h_obj = object_height_m(config, label)
        distance = (h_obj * f_px) / float(bbox_h)

    lo = float(est.get("min_m", 0.5))
    hi = float(est.get("max_m", 20.0))
    return max(lo, min(hi, distance))


class RangeSmoother:
    """Per-track exponential moving average for stable range readout."""

    def __init__(self, alpha: float = 0.35) -> None:
        self.alpha = max(0.05, min(1.0, alpha))
        self._state: dict[int, float] = {}

    def update(self, track_id: int, raw_m: Optional[float]) -> Optional[float]:
        if raw_m is None:
            self._state.pop(track_id, None)
            return None
        prev = self._state.get(track_id)
        if prev is None:
            smoothed = raw_m
        else:
            smoothed = self.alpha * raw_m + (1.0 - self.alpha) * prev
        self._state[track_id] = smoothed
        return round(smoothed, 1)

    def prune(self, alive_ids: set[int]) -> None:
        for tid in list(self._state.keys()):
            if tid not in alive_ids:
                del self._state[tid]


def estimate_range_m(
    bbox_h: float,
    config: dict[str, Any],
    label: str = "default",
    track_id: Optional[int] = None,
    smoother: Optional[RangeSmoother] = None,
) -> Optional[float]:
    raw = estimate_range_raw(bbox_h, config, label)
    if raw is None:
        if smoother is not None and track_id is not None:
            smoother.update(track_id, None)
        return None
    if smoother is not None and track_id is not None:
        return smoother.update(track_id, raw)
    return round(raw, 1)
