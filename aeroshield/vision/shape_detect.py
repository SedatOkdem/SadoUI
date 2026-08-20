"""Match demo-prop silhouettes by shape only (binary mask), not paint color."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

from aeroshield.core.modes import is_hostile
from aeroshield.workers.ipc import Detection

_TEMPLATE_DIR = Path(__file__).with_name("shape_templates")
_TEMPLATES: Optional[list[tuple[str, np.ndarray]]] = None

_FILES = (
    ("mini_iha", "MiniIHA"),
    ("f16", "F16"),          # Celik Kubbe clay / grey
    ("f16_ck", "F16"),
    ("f16_asd", "F16"),      # asd1dw blue
    ("f16_prop", "F16"),     # turuncu 3D baskı
    ("helikopter", "Helikopter"),
    ("fuze", "BalistikFuze"),
)


def detect_by_shape(
    frame_bgr: np.ndarray,
    config: dict[str, Any] | None = None,
    skip_labels: Optional[set[str]] = None,
) -> list[Detection]:
    cfg = (config or {}).get("shape_detect") or {}
    if not bool(cfg.get("enabled", True)):
        return []
    templates = _load_templates(cfg)
    if not templates or frame_bgr is None or frame_bgr.size == 0:
        return []

    skip = skip_labels or set()
    src_h, src_w = frame_bgr.shape[:2]
    work_w = int(cfg.get("work_width", 480))
    scale = 1.0
    small = frame_bgr
    if src_w > work_w:
        scale = src_w / float(work_w)
        nh = max(1, int(round(src_h / scale)))
        small = cv2.resize(frame_bgr, (work_w, nh), interpolation=cv2.INTER_AREA)

    h, w = small.shape[:2]
    mask = _foreground_mask(small)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=1)

    min_corr = float(cfg.get("min_corr", 0.80))
    fuze_corr = float(cfg.get("fuze_min_corr", 0.80))
    f16_corr = float(cfg.get("f16_min_corr", 0.55))
    min_a = int(h * w * float(cfg.get("min_area_frac", 0.002)))
    max_a = int(h * w * float(cfg.get("max_area_frac", 0.35)))

    hits: list[tuple[float, str, int, int, int, int]] = []
    for label, patch in templates:
        if label in skip:
            continue
        if label == "BalistikFuze":
            need = fuze_corr
        elif label == "F16":
            need = f16_corr
        else:
            need = min_corr
        found = _match_silhouette(mask, patch, need)
        if found is None:
            continue
        score, x, y, bw, bh = found
        area = bw * bh
        if area < min_a or area > max_a:
            continue
        if not _shape_ok(label, bw, bh, w, h):
            continue
        hits.append((score, label, x, y, bw, bh))

    hits.sort(key=lambda t: t[0], reverse=True)
    dets: list[Detection] = []
    used: set[str] = set()
    tid = 400
    for score, label, x, y, bw, bh in hits:
        if label in used:
            continue
        box = Detection(
            track_id=tid,
            x=float(x) * scale,
            y=float(y) * scale,
            w=float(bw) * scale,
            h=float(bh) * scale,
            conf=float(min(0.92, 0.50 + 0.5 * score)),
            label=label,
            hostile=is_hostile(label),
        )
        if any(_iou(box, d) >= 0.35 for d in dets):
            continue
        used.add(label)
        dets.append(box)
        tid += 1
    return dets


def merge_shape(yolo: list[Detection], shapes: list[Detection], iou_min: float = 0.28) -> list[Detection]:
    if not shapes:
        return yolo
    if not yolo:
        return shapes
    used: set[int] = set()
    out: list[Detection] = []
    for y in yolo:
        hit = False
        for i, s in enumerate(shapes):
            if i in used:
                continue
            if _iou(y, s) >= iou_min:
                used.add(i)
                hit = True
                out.append(y)
                break
        if not hit:
            out.append(y)
    for i, s in enumerate(shapes):
        if i not in used:
            out.append(s)
    return out


def _match_silhouette(
    mask: np.ndarray, patch: np.ndarray, min_corr: float
) -> Optional[tuple[float, int, int, int, int]]:
    mh, mw = mask.shape[:2]
    ph, pw = patch.shape[:2]
    if ph < 8 or pw < 8:
        return None
    best: Optional[tuple[float, int, int, int, int]] = None
    # Relative to frame width: cruise missile is a mid-size table prop.
    for frac in (0.10, 0.14, 0.20, 0.28, 0.38, 0.50):
        tw = max(24, int(mw * frac))
        th = max(16, int(round(tw * ph / float(pw))))
        if tw >= mw or th >= mh:
            continue
        tmpl = cv2.resize(patch, (tw, th), interpolation=cv2.INTER_AREA)
        _, tmpl = cv2.threshold(tmpl, 127, 255, cv2.THRESH_BINARY)
        res = cv2.matchTemplate(mask, tmpl, cv2.TM_CCOEFF_NORMED)
        _, maxv, _, maxloc = cv2.minMaxLoc(res)
        if maxv < min_corr:
            continue
        if best is None or maxv > best[0]:
            best = (float(maxv), int(maxloc[0]), int(maxloc[1]), tw, th)
    return best


def _shape_ok(label: str, bw: int, bh: int, fw: int, fh: int) -> bool:
    if bw > fw * 0.90 and bh > fh * 0.90:
        return False
    aspect = max(bw, bh) / float(max(1, min(bw, bh)))
    if label == "BalistikFuze":
        # Long fuselage + wings; reject square clutter and full-frame blobs.
        return 1.55 <= aspect <= 5.5 and bw >= 22 and bh >= 12
    if label == "MiniIHA":
        return aspect <= 2.4
    if label == "F16":
        return 1.15 <= aspect <= 4.5
    if label == "Helikopter":
        return aspect <= 3.2
    return True


# Default active F16 stems (skip asd/ck unless listed in config).
_DEFAULT_F16_STEMS = ("f16", "f16_prop")


def _load_templates(cfg: Optional[dict[str, Any]] = None) -> list[tuple[str, np.ndarray]]:
    global _TEMPLATES
    cfg = cfg or {}
    f16_stems = cfg.get("f16_templates")
    if f16_stems is None:
        f16_stems = list(_DEFAULT_F16_STEMS)
    else:
        f16_stems = [str(s) for s in f16_stems]
    cache_key = tuple(f16_stems)
    if _TEMPLATES is not None and getattr(_load_templates, "_key", None) == cache_key:
        return _TEMPLATES
    allow_f16 = set(f16_stems)
    out: list[tuple[str, np.ndarray]] = []
    for stem, label in _FILES:
        if label == "F16" and stem not in allow_f16:
            continue
        path = _TEMPLATE_DIR / f"{stem}.png"
        if not path.exists():
            continue
        img = cv2.imread(str(path))
        if img is None:
            continue
        m = _foreground_mask(img)
        contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue
        cnt = max(contours, key=cv2.contourArea)
        if cv2.contourArea(cnt) < 80:
            continue
        x, y, bw, bh = cv2.boundingRect(cnt)
        patch = np.zeros((bh, bw), dtype=np.uint8)
        cv2.drawContours(patch, [cnt], -1, 255, thickness=cv2.FILLED, offset=(-x, -y))
        out.append((label, patch))
    _TEMPLATES = out
    _load_templates._key = cache_key  # type: ignore[attr-defined]
    return out


def _foreground_mask(bgr: np.ndarray) -> np.ndarray:
    """Pixels unlike the frame border — silhouette, independent of paint color."""
    h, w = bgr.shape[:2]
    t = max(4, min(h, w) // 40)
    border = np.concatenate(
        [
            bgr[:t, :].reshape(-1, 3),
            bgr[-t:, :].reshape(-1, 3),
            bgr[:, :t].reshape(-1, 3),
            bgr[:, -t:].reshape(-1, 3),
        ],
        axis=0,
    )
    bg = np.median(border.astype(np.float32), axis=0)
    diff = np.linalg.norm(bgr.astype(np.float32) - bg, axis=2)
    return (diff > 26).astype(np.uint8) * 255


def _iou(a: Detection, b: Detection) -> float:
    x1 = max(a.x, b.x)
    y1 = max(a.y, b.y)
    x2 = min(a.x + a.w, b.x + b.w)
    y2 = min(a.y + a.h, b.y + b.h)
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = a.w * a.h + b.w * b.h - inter
    return float(inter / union) if union > 0 else 0.0
