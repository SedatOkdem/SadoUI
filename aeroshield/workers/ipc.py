"""Shared IPC message helpers and latest-only queue utilities."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from multiprocessing import shared_memory
from typing import Any, Optional

import numpy as np


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


class FrameRing:
    """Double-buffered BGR frames in shared memory (camera → inference)."""

    SLOTS = 2

    def __init__(
        self,
        max_w: int = 1280,
        max_h: int = 720,
        *,
        create: bool = True,
        names: Optional[list[str]] = None,
    ) -> None:
        self.max_w = int(max_w)
        self.max_h = int(max_h)
        self.nbytes = self.max_w * self.max_h * 3
        self._shms: list[shared_memory.SharedMemory] = []
        self._names: list[str] = []
        self._write_i = 0
        for i in range(self.SLOTS):
            if create:
                shm = shared_memory.SharedMemory(create=True, size=self.nbytes)
            else:
                if not names or i >= len(names):
                    raise ValueError("FrameRing attach requires two shared-memory names")
                shm = shared_memory.SharedMemory(name=str(names[i]))
            self._shms.append(shm)
            self._names.append(shm.name)

    @property
    def names(self) -> list[str]:
        return list(self._names)

    def write(self, frame: np.ndarray) -> tuple[int, int, int]:
        """Copy BGR frame into next slot. Returns (slot, width, height)."""
        if frame is None or frame.ndim != 3 or frame.shape[2] != 3:
            raise ValueError("FrameRing.write expects HxWx3 BGR uint8")
        h, w = int(frame.shape[0]), int(frame.shape[1])
        if w > self.max_w or h > self.max_h:
            raise ValueError(f"frame {w}x{h} exceeds ring {self.max_w}x{self.max_h}")
        slot = self._write_i % self.SLOTS
        self._write_i += 1
        buf = np.ndarray((self.max_h, self.max_w, 3), dtype=np.uint8, buffer=self._shms[slot].buf)
        # Contiguous copy into shared buffer
        np.copyto(buf[:h, :w], np.ascontiguousarray(frame))
        return slot, w, h

    def read(self, slot: int, width: int, height: int) -> np.ndarray:
        """Return a private copy of the slot crop (safe vs camera overwrite)."""
        slot = int(slot) % self.SLOTS
        w = int(width)
        h = int(height)
        if w <= 0 or h <= 0 or w > self.max_w or h > self.max_h:
            raise ValueError(f"invalid read size {w}x{h}")
        buf = np.ndarray((self.max_h, self.max_w, 3), dtype=np.uint8, buffer=self._shms[slot].buf)
        return buf[:h, :w].copy()

    def close(self) -> None:
        for shm in self._shms:
            try:
                shm.close()
            except Exception:
                pass

    def unlink(self) -> None:
        for shm in self._shms:
            try:
                shm.unlink()
            except Exception:
                pass

    def close_and_unlink(self) -> None:
        self.close()
        self.unlink()

    @staticmethod
    def from_meta(meta: dict[str, Any]) -> "FrameRing":
        return FrameRing(
            max_w=int(meta.get("max_w", 1280)),
            max_h=int(meta.get("max_h", 720)),
            create=False,
            names=list(meta.get("names") or []),
        )


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
