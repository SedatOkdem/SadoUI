"""Multi-object Kalman helpers (used by inference process)."""

from __future__ import annotations

from typing import Dict

from aeroshield.core.kalman import Kalman2D
from aeroshield.workers.ipc import Detection


class MultiKalmanTracker:
    def __init__(self) -> None:
        self.filters: Dict[int, Kalman2D] = {}

    def update(self, detections: list[Detection], dt: float = 1.0 / 30.0) -> list[Detection]:
        alive = set()
        for d in detections:
            alive.add(d.track_id)
            kf = self.filters.get(d.track_id)
            if kf is None:
                kf = Kalman2D()
                self.filters[d.track_id] = kf
            kf.predict(dt)
            cx, cy, vx, vy = kf.update(d.cx, d.cy)
            d.cx, d.cy = cx, cy
            # stash velocity on object dynamically for IPC
            d.vx = vx  # type: ignore[attr-defined]
            d.vy = vy  # type: ignore[attr-defined]
        for tid in list(self.filters.keys()):
            if tid not in alive:
                del self.filters[tid]
        return detections

    def velocity(self, track_id: int) -> tuple[float, float]:
        kf = self.filters.get(track_id)
        if kf is None:
            return 0.0, 0.0
        return float(kf.x[2, 0]), float(kf.x[3, 0])
