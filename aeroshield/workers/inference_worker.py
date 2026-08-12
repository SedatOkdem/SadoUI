"""YOLO inference + tracking — optimized for FPS without killing recall."""

from __future__ import annotations

import time
from dataclasses import replace
from multiprocessing import Process, Queue
from typing import Any, Optional

import cv2
import numpy as np

from aeroshield.core.modes import is_hostile, normalize_label
from aeroshield.core.range import RangeSmoother, estimate_range_m
from aeroshield.workers.ipc import Detection, put_latest
from aeroshield.workers.tracking_worker import MultiKalmanTracker

# COCO class ids used by default stub map (airplane, bird, kite)
_DEFAULT_COCO_FILTER = [4, 14, 33]


def inference_process_main(frame_q: Queue, track_q: Queue, stop_event, config: dict[str, Any]) -> None:
    model_cfg = config.get("model", {})
    perf = config.get("performance", {})
    model = None
    use_stub = False
    use_half = False
    device = model_cfg.get("device") or None
    if device == "":
        device = None

    imgsz = int(model_cfg.get("imgsz", 640))
    max_det = int(model_cfg.get("max_det", 20))
    conf = float(model_cfg.get("conf", 0.35))
    iou = float(model_cfg.get("iou", 0.45))
    infer_every_n = max(1, int(perf.get("infer_every_n", 1)))
    want_half = bool(model_cfg.get("half", True))

    classes = model_cfg.get("classes")
    if classes is None and not (model_cfg.get("class_names") or []):
        # Only run relevant COCO classes → big speedup, same detection for mapped targets
        classes = list(model_cfg.get("filter_classes") or _DEFAULT_COCO_FILTER)

    try:
        from ultralytics import YOLO
        import torch

        model = YOLO(str(model_cfg.get("path", "yolov8n.pt")))
        try:
            model.fuse()
        except Exception:
            pass

        if device is None:
            device = "0" if torch.cuda.is_available() else "cpu"
        use_half = bool(want_half and str(device) != "cpu" and torch.cuda.is_available())

        # Warmup
        dummy = np.zeros((imgsz, imgsz, 3), dtype=np.uint8)
        model.predict(dummy, imgsz=imgsz, device=device, half=use_half, verbose=False, max_det=max_det)
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

    tracker = MultiKalmanTracker()
    range_alpha = float(config.get("range_estimator", {}).get("smooth_alpha", 0.35))
    range_smoother = RangeSmoother(alpha=range_alpha)
    t_fps = time.time()
    frames = 0
    fps = 0.0
    frame_i = 0
    last_detections: list[Detection] = []

    while not stop_event.is_set():
        try:
            item = frame_q.get(timeout=0.05)
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
        frame_i += 1
        run_infer = (frame_i % infer_every_n == 0) or not last_detections

        detections: list[Detection] = []
        if use_stub or model is None:
            detections = _stub_detections(frame, item.get("frame_id", 0), config)
        elif not run_infer:
            # Hold last boxes; Kalman coasts between full inference frames
            detections = [replace(d) for d in last_detections]
        else:
            infer_frame, scale_x, scale_y = _prepare_infer_frame(frame, imgsz)
            try:
                kwargs = dict(
                    persist=True,
                    conf=conf,
                    iou=iou,
                    imgsz=imgsz,
                    device=device,
                    half=use_half,
                    verbose=False,
                    max_det=max_det,
                )
                if classes:
                    kwargs["classes"] = classes
                results = model.track(infer_frame, **kwargs)
                detections = _parse_results(
                    results, coco_map, custom_names, config, scale_x=scale_x, scale_y=scale_y
                )
            except Exception:
                try:
                    kwargs = dict(
                        conf=conf,
                        iou=iou,
                        imgsz=imgsz,
                        device=device,
                        half=use_half,
                        verbose=False,
                        max_det=max_det,
                    )
                    if classes:
                        kwargs["classes"] = classes
                    results = model.predict(infer_frame, **kwargs)
                    detections = _parse_results(
                        results,
                        coco_map,
                        custom_names,
                        config,
                        assign_ids=True,
                        scale_x=scale_x,
                        scale_y=scale_y,
                    )
                except Exception:
                    detections = _stub_detections(frame, item.get("frame_id", 0), config)

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


def _prepare_infer_frame(frame: np.ndarray, imgsz: int) -> tuple[np.ndarray, float, float]:
    """Downscale for YOLO; return scale factors to map boxes back to full frame."""
    h, w = frame.shape[:2]
    scale = float(imgsz) / float(max(h, w))
    if scale >= 0.999:
        return frame, 1.0, 1.0
    nw = max(1, int(round(w * scale)))
    nh = max(1, int(round(h * scale)))
    small = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
    # boxes on small → multiply by 1/scale to full
    inv = 1.0 / scale
    return small, inv, inv


def _parse_results(
    results,
    coco_map,
    custom_names,
    config,
    assign_ids: bool = False,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
) -> list[Detection]:
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
        if boxes.id is not None:
            tid = int(boxes.id[i].item())
        else:
            tid = i + 1
        raw_name = (
            custom_names[cls_id]
            if custom_names and cls_id < len(custom_names)
            else names.get(cls_id, str(cls_id))
        )
        mapped = coco_map.get(str(raw_name).lower(), raw_name)
        label = normalize_label(str(mapped))
        x1, y1, x2, y2 = xyxy
        x1 *= scale_x
        x2 *= scale_x
        y1 *= scale_y
        y2 *= scale_y
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
    if not detections:
        return None
    hostiles = [d for d in detections if d.hostile]
    pool = hostiles or detections
    return min(pool, key=lambda d: abs(d.cx - width / 2.0) - d.cy).track_id


def start_inference_process(frame_q: Queue, track_q: Queue, stop_event, config: dict) -> Process:
    p = Process(
        target=inference_process_main,
        args=(frame_q, track_q, stop_event, config),
        name="InferenceProcess",
        daemon=True,
    )
    p.start()
    return p
