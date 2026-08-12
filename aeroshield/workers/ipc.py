"""Shared IPC message helpers and latest-only queue utilities."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional


def put_latest(q, item, drop_old: bool = True) -> None:
    """Put item; optionally drain queue first so consumers always see newest."""
    if drop_old:
        try:
            while True:
                q.get_nowait()
        except Exception:
            pass
    try:
        q.put_nowait(item)
    except Exception:
        try:
            q.get_nowait()
        except Exception:
            pass
        try:
            q.put_nowait(item)
        except Exception:
            pass


@dataclass
class Detection:
    track_id: int
    x: float
    y: float
    w: float
    h: float
    conf: float
    label: str
    hostile: bool = True
    cx: float = 0.0
    cy: float = 0.0
    range_m: Optional[float] = None

    def __post_init__(self) -> None:
        if not self.cx and not self.cy:
            self.cx = self.x + self.w / 2.0
            self.cy = self.y + self.h / 2.0


@dataclass
class TrackSnapshot:
    ts: float
    frame_id: int
    fps: float
    latency_ms: float
    width: int
    height: int
    detections: list[Detection] = field(default_factory=list)
    primary_id: Optional[int] = None
    # Encoded JPEG of the frame for UI (keeps queue light)
    jpeg: Optional[bytes] = None

    def primary(self) -> Optional[Detection]:
        if self.primary_id is None:
            return None
        for d in self.detections:
            if d.track_id == self.primary_id:
                return d
        return self.detections[0] if self.detections else None


@dataclass
class OperatorCommand:
    ts: float = field(default_factory=time.time)
    stage: int = 1
    start_mission: bool = False
    estop: bool = False
    estop_clear: bool = False
    maint: bool = False
    fire: bool = False
    pan_cmd: int = 0
    tilt_cmd: int = 0
    pan_forbidden_min: float = 200.0
    pan_forbidden_max: float = 270.0
    manual_range_m: Optional[float] = None
    bit_ok: bool = True


@dataclass
class Telemetry:
    ts: float = field(default_factory=time.time)
    linked: bool = False
    mock: bool = False
    failsafe: bool = False
    status: int = 0
    limit_pan: int = 0
    limit_tilt: int = 0
    fsm: str = "BIT"
    stage: int = 1
    fps: float = 0.0
    latency_ms: float = 0.0
    pan: int = 0
    tilt: int = 0
    fire: int = 0
    locked: bool = False
    wez_ok: bool = False
    primary_label: str = "-"
    primary_id: Optional[int] = None
    cx: float = 0.0
    cy: float = 0.0
    vx: float = 0.0
    vy: float = 0.0
    range_m: Optional[float] = None
    maint_remaining_s: float = 600.0
    log_line: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
