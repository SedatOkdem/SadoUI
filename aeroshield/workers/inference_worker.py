"""YOLO inference + tracking process (Ultralytics track = ByteTrack-like)."""

from __future__ import annotations

import time
from multiprocessing import Process, Queue
from typing import Any, Optional

import cv2
import numpy as np

from aeroshield.core.modes import is_hostile, normalize_label
from aeroshield.core.range import RangeSmoother, estimate_range_m
from aeroshield.workers.ipc import Detection, put_latest
from aeroshield.workers.tracking_worker import MultiKalmanTracker


def inference_process_main(frame_q: Queue, track_q: Queue, stop_event, config: dict[str, Any]) -> None:
    model_cfg = config.get("model", {})
    model = None
    use_stub = False
    try:
        from ultralytics import YOLO

        model = YOLO(str(model_cfg.get("path", "yolov8n.pt")))
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
    custom_names = model_cfg.get("class_names") or []
    conf = float(model_cfg.get("conf", 0.35))
    iou = float(model_cfg.get("iou", 0.45))
    device = model_cfg.get("device") or None
    if device == "":
        device = None

    tracker = MultiKalmanTracker()
    range_alpha = float(config.get("range_estimator", {}).get("smooth_alpha", 0.35))
    range_smoother = RangeSmoother(alpha=range_alpha)
    t_fps = time.time()
    frames = 0
    fps = 0.0

    while not stop_event.is_set():
        try:
            item = frame_q.get(timeout=0.1)
        except Exception:
            continue

        t0 = time.time()
        jpeg = item.get("jpeg")
        if not jpeg:
            continue
        arr = np.frombuffer(jpeg, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            continue
        h, w = frame.shape[:2]

        detections: list[Detection] = []
        if use_stub or model is None:
            detections = _stub_detections(frame, item.get("frame_id", 0), config)
        else:
            try:
                results = model.track(
                    frame,
                    persist=True,
                    conf=conf,
                    iou=iou,
                    device=device,
                    verbose=False,
                )
                detections = _parse_results(results, coco_map, custom_names, config)
            except Exception:
                try:
                    results = model.predict(frame, conf=conf, iou=iou, device=device, verbose=False)
                    detections = _parse_results(results, coco_map, custom_names, config, assign_ids=True)
                except Exception:
                    detections = _stub_detections(frame, item.get("frame_id", 0), config)

        detections = tracker.update(detections, dt=1.0 / 30.0)
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

        primary_id = _select_primary(detections, w)

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


def _parse_results(results, coco_map, custom_names, config, assign_ids: bool = False) -> list[Detection]:
    out: list[Detection] = []
    if not results:
        return out
    r0 = results[0]
    boxes = getattr(r0, "boxes", None)
    if boxes is None:
        return out
    names = r0.names if hasattr(r0, "names") else {}
    for i in range(len(boxes)):
        xyxy = boxes.xyxy[i].tolist()
        conf = float(boxes.conf[i].item()) if boxes.conf is not None else 0.0
        cls_id = int(boxes.cls[i].item()) if boxes.cls is not None else 0
        tid = None
        if boxes.id is not None:
            tid = int(boxes.id[i].item())
        elif assign_ids:
            tid = i + 1
        else:
            tid = i + 1
        raw_name = custom_names[cls_id] if custom_names and cls_id < len(custom_names) else names.get(cls_id, str(cls_id))
        mapped = coco_map.get(str(raw_name).lower(), raw_name)
        label = normalize_label(str(mapped))
        x1, y1, x2, y2 = xyxy
        det = Detection(
            track_id=tid,
            x=float(x1),
            y=float(y1),
            w=float(x2 - x1),
            h=float(y2 - y1),
            conf=conf,
            label=label,
            hostile=is_hostile(label),
        )
        out.append(det)
    return out


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
    """Primary threat: prefer hostiles nearer vertical center and lower in frame (approaching)."""
    if not detections:
        return None
    hostiles = [d for d in detections if d.hostile]
    pool = hostiles or detections
    best = min(pool, key=lambda d: abs(d.cx - width / 2.0) - d.cy)
    return best.track_id


def start_inference_process(frame_q: Queue, track_q: Queue, stop_event, config: dict) -> Process:
    p = Process(
        target=inference_process_main,
        args=(frame_q, track_q, stop_event, config),
        name="InferenceProcess",
        daemon=True,
    )
    p.start()
    return p
