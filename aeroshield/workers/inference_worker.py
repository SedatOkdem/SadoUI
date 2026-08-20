"""YOLO inference + tracking — optimized for FPS without killing recall."""

from __future__ import annotations

import time
from dataclasses import replace
from multiprocessing import Process, Queue
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

from aeroshield.core.modes import is_hostile, normalize_label
from aeroshield.core.range import RangeSmoother, estimate_range_m
from aeroshield.vision.color_iff import apply_color_iff
from aeroshield.vision.shape_detect import detect_by_shape, merge_shape
from aeroshield.workers.ipc import Detection, FrameRing, put_latest
from aeroshield.workers.tracking_worker import MultiKalmanTracker

# COCO class ids used by default stub map (airplane, bird, kite)
_DEFAULT_COCO_FILTER = [4, 14, 33]
_STOCK_WEIGHTS = {"yolov8n.pt", "yolov8s.pt", "yolov8m.pt", "yolo11n.pt", "yolo11s.pt", "yolo11m.pt"}


def _resolve_weights(model_cfg: dict[str, Any]) -> tuple[str, bool]:
    """Return (path, is_custom). Prefer trained Celik Kubbe weights when present."""
    requested = Path(str(model_cfg.get("path", "yolov8n.pt")))
    fallback = str(model_cfg.get("fallback_path", "yolov8n.pt"))
    candidates = [requested]
    if not requested.is_absolute():
        candidates.append(Path.cwd() / requested)
    for cand in candidates:
        if cand.exists() and cand.is_file():
            return str(cand), cand.name not in _STOCK_WEIGHTS
    return fallback, False


def inference_process_main(
    frame_q: Queue,
    track_q: Queue,
    stop_event,
    config: dict[str, Any],
    ring_meta: Optional[dict[str, Any]] = None,
) -> None:
    model_cfg = config.get("model", {})
    perf = config.get("performance", {})
    ui_cfg = config.get("ui", {})
    model = None
    use_stub = False
    use_half = False
    device = model_cfg.get("device") or None
    if device == "":
        device = None

    imgsz = int(model_cfg.get("imgsz", 640))
    max_det = int(model_cfg.get("max_det", 20))
    conf = float(model_cfg.get("conf", 0.35))
    conf_by_class = {
        str(k): float(v) for k, v in (model_cfg.get("conf_by_class") or {}).items()
    }
    iou = float(model_cfg.get("iou", 0.45))
    infer_every_n = max(1, int(perf.get("infer_every_n", 1)))
    want_half = bool(model_cfg.get("half", True))
    weights_path, is_custom = _resolve_weights(model_cfg)
    jpeg_quality = int(perf.get("jpeg_quality", 55))
    display_max_w = int(ui_cfg.get("display_max_width", 960))

    ring_meta = ring_meta or config.get("_frame_ring")
    if not ring_meta:
        raise RuntimeError("inference_process_main requires FrameRing metadata")
    ring = FrameRing.from_meta(ring_meta)

    classes = model_cfg.get("classes")
    custom_names = list(model_cfg.get("class_names") or []) if is_custom else []
    if classes is None and not is_custom:
        # Only run relevant COCO classes → big speedup, same detection for mapped targets
        classes = list(model_cfg.get("filter_classes") or _DEFAULT_COCO_FILTER)

    try:
        from ultralytics import YOLO
        import torch

        model = YOLO(weights_path)
        try:
            model.fuse()
        except Exception:
            pass

        if device is None:
            device = "0" if torch.cuda.is_available() else "cpu"
        use_half = bool(want_half and str(device) != "cpu" and torch.cuda.is_available())

        dummy = np.zeros((imgsz, imgsz, 3), dtype=np.uint8)
        model.predict(dummy, imgsz=imgsz, device=device, half=use_half, verbose=False, max_det=max_det)
        put_latest(
            track_q,
            {
                "ts": time.time(),
                "frame_id": 0,
                "fps": 0.0,
                "latency_ms": 0.0,
                "width": 0,
                "height": 0,
                "detections": [],
                "primary_id": None,
                "jpeg": None,
                "log_line": f"YOLO {Path(weights_path).name}  custom={is_custom}  device={device}",
            },
        )
    except Exception as exc:
        use_stub = True
        put_latest(
            track_q,
            {
                "ts": time.time(),
                "frame_id": 0,
                "fps": 0.0,
                "latency_ms": 0.0,
                "width": 0,
                "height": 0,
                "detections": [],
                "primary_id": None,
                "jpeg": None,
                "log_line": f"YOLO unavailable, stub tracker: {exc}",
            },
        )

    coco_map = {str(k).lower(): v for k, v in (model_cfg.get("coco_stub_map") or {}).items()}
    if not custom_names:
        custom_names = list(model_cfg.get("class_names") or []) if is_custom else []

    tracker = MultiKalmanTracker()
    range_alpha = float(config.get("range_estimator", {}).get("smooth_alpha", 0.35))
    range_smoother = RangeSmoother(alpha=range_alpha)
    t_fps = time.time()
    frames = 0
    fps = 0.0
    frame_i = 0
    last_detections: list[Detection] = []
    last_shapes: list[Detection] = []
    shape_every = max(1, int((config.get("shape_detect") or {}).get("every_n", 3)))
    sticky = _StickyPrimary(
        lost_max=max(8, int((config.get("control") or {}).get("sticky_lost_frames", 10))),
        min_conf=float(model_cfg.get("conf", 0.45)),
        center_frac=float((config.get("control") or {}).get("sticky_center_frac", 0.55)),
    )
    next_tid = 1

    try:
        while not stop_event.is_set():
            try:
                item = frame_q.get(timeout=0.05)
            except Exception:
                continue

            t0 = time.time()
            try:
                frame = ring.read(int(item["slot"]), int(item["width"]), int(item["height"]))
            except Exception:
                continue
            h, w = frame.shape[:2]
            frame_i += 1
            run_infer = (frame_i % infer_every_n == 0) or not last_detections

            detections: list[Detection] = []
            yolo_dets: list[Detection] = []
            if use_stub or model is None:
                yolo_dets = _stub_detections(frame, item.get("frame_id", 0), config)
            elif not run_infer:
                yolo_dets = [replace(d) for d in last_detections if d.track_id < 400]
            else:
                # Pass the native frame. Pre-resizing + imgsz letterbox double-scaled
                # boxes to full-screen (looked like a phantom target in the center).
                kwargs = dict(
                    conf=min(conf, min(conf_by_class.values()) if conf_by_class else conf),
                    iou=iou,
                    imgsz=imgsz,
                    device=device,
                    verbose=False,
                    max_det=max_det,
                )
                if use_half:
                    kwargs["half"] = True
                if classes:
                    kwargs["classes"] = classes
                try:
                    results = model.predict(frame, **kwargs)
                    yolo_dets = _parse_results(
                        results,
                        coco_map,
                        custom_names,
                        w,
                        h,
                        min_conf=conf,
                        conf_by_class=conf_by_class,
                    )
                except Exception:
                    yolo_dets = []
                yolo_dets, next_tid = _associate_ids(yolo_dets, last_detections, next_tid)

            if frame_i % shape_every == 0:
                skip_labels: set[str] = set()
                # Skip shape templates when YOLO already has a strong hit for that class.
                for label in ("F16", "BalistikFuze", "MiniIHA", "Helikopter"):
                    if any(d.label == label and d.conf >= conf for d in yolo_dets):
                        skip_labels.add(label)
                last_shapes = detect_by_shape(frame, config, skip_labels=skip_labels)
            shapes = last_shapes
            detections = merge_shape(yolo_dets, shapes)
            detections = apply_color_iff(frame, detections, config)
            if run_infer or use_stub or model is None:
                last_detections = detections

            dt_track = 1.0 / max(fps, 15.0)
            detections = tracker.update(detections, dt=dt_track)
            alive = {d.track_id for d in detections}
            range_smoother.prune(alive)
            for d in detections:
                d.range_m = estimate_range_m(
                    d.h,
                    config,
                    label=d.label,
                    track_id=d.track_id,
                    smoother=range_smoother,
                )

            primary_id = sticky.select(detections, w)

            frames += 1
            now = time.time()
            if now - t_fps >= 1.0:
                fps = frames / (now - t_fps)
                frames = 0
                t_fps = now
            latency_ms = (time.time() - item.get("ts", t0)) * 1000.0

            det_dicts = []
            for d in detections:
                vx, vy = tracker.velocity(d.track_id)
                det_dicts.append(
                    {
                        "track_id": d.track_id,
                        "x": d.x,
                        "y": d.y,
                        "w": d.w,
                        "h": d.h,
                        "conf": d.conf,
                        "label": d.label,
                        "hostile": d.hostile,
                        "cx": d.cx,
                        "cy": d.cy,
                        "range_m": d.range_m,
                        "vx": vx,
                        "vy": vy,
                    }
                )

            jpeg = _encode_display_jpeg(frame, display_max_w, jpeg_quality)

            put_latest(
                track_q,
                {
                    "ts": now,
                    "frame_id": item.get("frame_id", 0),
                    "fps": fps,
                    "latency_ms": latency_ms,
                    "width": w,
                    "height": h,
                    "detections": det_dicts,
                    "primary_id": primary_id,
                    "jpeg": jpeg,
                },
            )
    finally:
        ring.close()


def _encode_display_jpeg(frame: np.ndarray, max_w: int, quality: int) -> Optional[bytes]:
    """Single UI JPEG at display width — control keeps camera-space detections."""
    if frame is None or frame.size == 0:
        return None
    out = frame
    h, w = frame.shape[:2]
    if max_w > 0 and w > max_w:
        scale = max_w / float(w)
        out = cv2.resize(
            frame,
            (int(round(w * scale)), int(round(h * scale))),
            interpolation=cv2.INTER_AREA,
        )
    ok, buf = cv2.imencode(".jpg", out, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    return buf.tobytes() if ok else None


def _parse_results(
    results,
    coco_map,
    custom_names,
    frame_w: int,
    frame_h: int,
    min_conf: float = 0.45,
    conf_by_class: dict[str, float] | None = None,
) -> list[Detection]:
    out: list[Detection] = []
    if not results:
        return out
    r0 = results[0]
    boxes = getattr(r0, "boxes", None)
    if boxes is None:
        return out
    names = r0.names if hasattr(r0, "names") else {}
    frame_area = float(max(1, frame_w * frame_h))
    class_floor = conf_by_class or {}
    gate = min(min_conf, min(class_floor.values())) if class_floor else min_conf
    for i in range(len(boxes)):
        xyxy = boxes.xyxy[i].tolist()
        conf = float(boxes.conf[i].item()) if boxes.conf is not None else 0.0
        if conf < gate:
            continue
        cls_id = int(boxes.cls[i].item()) if boxes.cls is not None else 0
        raw_name = str(names.get(cls_id, cls_id))
        # Prefer dataset class_names by id when model name is opaque; else keep model name.
        if custom_names and cls_id < len(custom_names):
            if str(raw_name).isdigit() or raw_name in ("None", "", "NoneType"):
                raw_name = custom_names[cls_id]
            elif normalize_label(raw_name) not in (
                "F16",
                "BalistikFuze",
                "Helikopter",
                "MiniIHA",
                "Dost",
            ):
                raw_name = custom_names[cls_id]
        mapped = coco_map.get(str(raw_name).lower(), raw_name)
        label = normalize_label(str(mapped))
        need = float(class_floor.get(label, min_conf))
        if conf < need:
            continue
        x1, y1, x2, y2 = xyxy
        x1 = float(max(0.0, min(frame_w, x1)))
        y1 = float(max(0.0, min(frame_h, y1)))
        x2 = float(max(0.0, min(frame_w, x2)))
        y2 = float(max(0.0, min(frame_h, y2)))
        bw, bh = x2 - x1, y2 - y1
        if bw < 12 or bh < 12:
            continue
        frac = (bw * bh) / frame_area
        # Full-frame boxes are letterbox artifacts; allow closer large F16 props.
        if frac < 0.0002 or frac > 0.55:
            continue
        det = Detection(
            track_id=0,  # assigned stably in _associate_ids
            x=x1,
            y=y1,
            w=bw,
            h=bh,
            conf=conf,
            label=label,
            hostile=is_hostile(label),
        )
        out.append(det)
    return out


def _associate_ids(
    detections: list[Detection],
    previous: list[Detection],
    next_tid: int,
    iou_min: float = 0.25,
) -> tuple[list[Detection], int]:
    """Keep track_id stable across frames so sticky primary / Kalman work."""
    prev_yolo = [d for d in previous if d.track_id < 400]
    used_prev: set[int] = set()
    for d in detections:
        if d.track_id >= 400:
            continue
        best_i = -1
        best_iou = iou_min
        for i, p in enumerate(prev_yolo):
            if i in used_prev:
                continue
            if not _labels_track_compat(p.label, d.label):
                continue
            score = _det_iou(d, p)
            if score > best_iou:
                best_iou = score
                best_i = i
        if best_i >= 0:
            d.track_id = prev_yolo[best_i].track_id
            used_prev.add(best_i)
        else:
            d.track_id = next_tid
            next_tid += 1
    return detections, next_tid


_VEHICLE_LABELS = {"F16", "Helikopter", "MiniIHA", "BalistikFuze"}


def _labels_track_compat(a: str, b: str) -> bool:
    """Dost ↔ vehicle remaps from color-IFF must keep the same track_id."""
    if a == b:
        return True
    if a == "Dost" and b in _VEHICLE_LABELS:
        return True
    if b == "Dost" and a in _VEHICLE_LABELS:
        return True
    return False


def _stub_detections(frame: np.ndarray, frame_id: int, config: dict) -> list[Detection]:
    h, w = frame.shape[:2]
    t = frame_id / 30.0
    cx = int(w * 0.5 + w * 0.28 * np.sin(t * 0.9))
    cy = int(h * 0.35 + h * 0.12 * np.cos(t * 1.1))
    bw, bh = 80, 36
    label = "MiniIHA"
    det = Detection(
        track_id=1,
        x=float(cx - bw / 2),
        y=float(cy - bh / 2),
        w=float(bw),
        h=float(bh),
        conf=0.9,
        label=label,
        hostile=True,
        cx=float(cx),
        cy=float(cy),
    )
    det.range_m = estimate_range_m(det.h, config, label=label)
    return [det]


def _select_primary(detections: list[Detection], width: int) -> Optional[int]:
    return _StickyPrimary().select(detections, width)


class _StickyPrimary:
    """Keep the same track until it is gone for several frames; never snap to a random box."""

    def __init__(self, lost_max: int = 10, min_conf: float = 0.45, center_frac: float = 0.55) -> None:
        self.tid: Optional[int] = None
        self.lost = 0
        self.lost_max = lost_max
        self.min_conf = min_conf
        self.center_frac = center_frac
        self._last: Optional[Detection] = None

    def select(self, detections: list[Detection], width: int) -> Optional[int]:
        by_id = {d.track_id: d for d in detections}
        if self.tid is not None and self.tid in by_id:
            self.lost = 0
            self._last = by_id[self.tid]
            return self.tid
        if self._last is not None and detections:
            hit = max(detections, key=lambda d: _det_iou(self._last, d))
            if _det_iou(self._last, hit) >= 0.28:
                self.tid = hit.track_id
                self.lost = 0
                self._last = hit
                return self.tid
        if self.tid is not None:
            self.lost += 1
            if self.lost < self.lost_max:
                return None
            self.tid = None
            self.lost = 0
            self._last = None
        cx0 = width / 2.0
        pool = [d for d in detections if d.hostile and d.conf >= self.min_conf]
        if not pool:
            pool = [d for d in detections if d.conf >= self.min_conf]
        near = [d for d in pool if abs(d.cx - cx0) <= width * self.center_frac]
        if not near:
            return None
        pick = min(near, key=lambda d: abs(d.cx - cx0) - d.cy)
        self.tid = pick.track_id
        self._last = pick
        return self.tid


def _det_iou(a: Detection, b: Detection) -> float:
    x1 = max(a.x, b.x)
    y1 = max(a.y, b.y)
    x2 = min(a.x + a.w, b.x + b.w)
    y2 = min(a.y + a.h, b.y + b.h)
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = a.w * a.h + b.w * b.h - inter
    return float(inter / union) if union > 0 else 0.0


def start_inference_process(
    frame_q: Queue,
    track_q: Queue,
    stop_event,
    config: dict,
    ring_meta: Optional[dict[str, Any]] = None,
) -> Process:
    p = Process(
        target=inference_process_main,
        args=(frame_q, track_q, stop_event, config, ring_meta),
        name="InferenceProcess",
        daemon=True,
    )
    p.start()
    return p
