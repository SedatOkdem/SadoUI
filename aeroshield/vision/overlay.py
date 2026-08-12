"""Draw crosshair, bboxes and status HUD on frames."""

from __future__ import annotations

from typing import Any, Optional

import cv2
import numpy as np

from aeroshield.core.modes import in_wez
from aeroshield.workers.ipc import Detection


def draw_crosshair(frame: np.ndarray, color=(90, 220, 190)) -> None:
    h, w = frame.shape[:2]
    cx, cy = w // 2, h // 2
    gap, arm = 10, 34
    thickness = 2
    cv2.line(frame, (cx - arm, cy), (cx - gap, cy), color, thickness, cv2.LINE_AA)
    cv2.line(frame, (cx + gap, cy), (cx + arm, cy), color, thickness, cv2.LINE_AA)
    cv2.line(frame, (cx, cy - arm), (cx, cy - gap), color, thickness, cv2.LINE_AA)
    cv2.line(frame, (cx, cy + gap), (cx, cy + arm), color, thickness, cv2.LINE_AA)
    cv2.circle(frame, (cx, cy), 3, color, -1, cv2.LINE_AA)
    # corner brackets
    br = 28
    for x0, y0, dx, dy in (
        (24, 24, 1, 1),
        (w - 24, 24, -1, 1),
        (24, h - 24, 1, -1),
        (w - 24, h - 24, -1, -1),
    ):
        cv2.line(frame, (x0, y0), (x0 + dx * br, y0), (70, 110, 130), 1, cv2.LINE_AA)
        cv2.line(frame, (x0, y0), (x0, y0 + dy * br), (70, 110, 130), 1, cv2.LINE_AA)


def draw_detections(
    frame: np.ndarray,
    detections: list[Detection],
    primary_id: Optional[int] = None,
    locked: bool = False,
    stage: int = 1,
    config: Optional[dict[str, Any]] = None,
) -> None:
    for d in detections:
        x1, y1 = int(d.x), int(d.y)
        x2, y2 = int(d.x + d.w), int(d.y + d.h)
        is_primary = primary_id is not None and d.track_id == primary_id
        if not d.hostile:
            color = (220, 170, 70)
        elif is_primary:
            color = (40, 70, 255) if locked else (40, 190, 255)
        else:
            color = (60, 150, 230)
        thickness = 3 if is_primary else 2
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness, cv2.LINE_AA)
        # corner ticks
        tick = 12
        for ax, ay, bx, by in (
            (x1, y1, x1 + tick, y1),
            (x1, y1, x1, y1 + tick),
            (x2, y1, x2 - tick, y1),
            (x2, y1, x2, y1 + tick),
            (x1, y2, x1 + tick, y2),
            (x1, y2, x1, y2 - tick),
            (x2, y2, x2 - tick, y2),
            (x2, y2, x2, y2 - tick),
        ):
            cv2.line(frame, (ax, ay), (bx, by), color, 2, cv2.LINE_AA)

        range_txt = ""
        if d.range_m is not None:
            if stage == 3 and config and d.hostile:
                ok = in_wez(config, d.label, d.range_m)
                range_txt = f"  {d.range_m:.1f}m{' ✓' if ok else ' !'}"
            else:
                range_txt = f"  {d.range_m:.1f}m"
        tag = f"ID{d.track_id}  {d.label}  {d.conf:.0%}{range_txt}"
        (tw, th), _ = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        ty = max(22, y1 - 8)
        cv2.rectangle(frame, (x1, ty - th - 6), (x1 + tw + 10, ty + 4), (10, 14, 18), -1)
        cv2.putText(frame, tag, (x1 + 5, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
        cv2.circle(frame, (int(d.cx), int(d.cy)), 3, color, -1, cv2.LINE_AA)


def _hud_panel(frame: np.ndarray, x: int, y: int, lines: list[str], accent=(90, 220, 190)) -> None:
    pad_x, pad_y, line_h = 12, 10, 22
    widths = [cv2.getTextSize(s, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)[0][0] for s in lines]
    w = max(widths) + pad_x * 2
    h = line_h * len(lines) + pad_y * 2 - 4
    overlay = frame.copy()
    cv2.rectangle(overlay, (x, y), (x + w, y + h), (8, 12, 18), -1)
    cv2.addWeighted(overlay, 0.72, frame, 0.28, 0, frame)
    cv2.rectangle(frame, (x, y), (x + w, y + h), (40, 70, 85), 1, cv2.LINE_AA)
    cv2.line(frame, (x, y), (x + 28, y), accent, 2, cv2.LINE_AA)
    cy = y + pad_y + 14
    for i, line in enumerate(lines):
        color = accent if i == 0 else (210, 225, 235)
        cv2.putText(frame, line, (x + pad_x, cy), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1, cv2.LINE_AA)
        cy += line_h


def draw_hud(
    frame: np.ndarray,
    fsm: str,
    stage: int,
    fps: float,
    linked: bool,
    wez_ok: bool,
    locked: bool,
    range_text: str = "",
    estop: bool = False,
    mock: bool = False,
) -> None:
    link = "MOCK" if mock else ("OK" if linked else "NO")
    lines = [
        "AEROSHIELD GCS",
        f"A{stage}  ·  {fsm}",
        f"FPS {fps:.0f}  LINK {link}",
        f"LOCK {'ON' if locked else 'OFF'}  WEZ {'OK' if wez_ok else '--'}",
    ]
    if range_text and range_text != "-":
        lines.append(f"RNG {range_text}")
    if estop:
        lines.append("! E-STOP ACTIVE")
    _hud_panel(frame, 16, 16, lines)

    # bottom-right system strip
    h, w = frame.shape[:2]
    strip = f"TEKNOFEST 2026  ·  CELIKKUBBE"
    (tw, th), _ = cv2.getTextSize(strip, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
    cv2.putText(
        frame,
        strip,
        (w - tw - 18, h - 16),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (90, 120, 140),
        1,
        cv2.LINE_AA,
    )
