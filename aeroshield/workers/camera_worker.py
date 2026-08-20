"""Camera capture process — low-latency grab into shared-memory FrameRing."""

from __future__ import annotations

import time
from multiprocessing import Process, Queue
from typing import Any, Optional

import cv2
import numpy as np

from aeroshield.vision.calibrate import load_calibration, undistort
from aeroshield.workers.ipc import FrameRing, put_latest


def _fourcc_to_str(value: float) -> str:
    v = int(value)
    return "".join(chr((v >> (8 * i)) & 0xFF) for i in range(4))


def _open_webcam(
    index: int, width: int, height: int, fps: float, buffer_size: int, backend: str = "dshow"
):
    """C920 1080p is ~5 FPS in YUY2; MJPEG is required for 30 FPS.

    DirectShow only by default. Trying MSMF after DSHOW doubles open time
    (each backend enumerates devices and negotiates 1080p MJPEG).
    """
    mjpg = cv2.VideoWriter_fourcc(*"MJPG")
    backends: list[int] = []
    want = (backend or "dshow").strip().lower()
    if want in ("dshow", "auto", ""):
        backends.append(cv2.CAP_DSHOW)
    if want in ("msmf", "auto") and hasattr(cv2, "CAP_MSMF"):
        backends.append(cv2.CAP_MSMF)
    if not backends:
        backends.append(cv2.CAP_DSHOW)

    for api in backends:
        cap = cv2.VideoCapture(index, api)
        if not cap.isOpened():
            cap.release()
            continue
        cap.set(cv2.CAP_PROP_FOURCC, mjpg)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(width))
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(height))
        cap.set(cv2.CAP_PROP_FPS, float(fps))
        cap.set(cv2.CAP_PROP_FOURCC, mjpg)
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, buffer_size)
        except Exception:
            pass
        fcc = _fourcc_to_str(cap.get(cv2.CAP_PROP_FOURCC))
        # Do not block on a 1080p probe read here — first grab happens in the loop.
        return cap, fcc

    return None, ""


def camera_process_main(
    frame_q: Queue,
    stop_event,
    config: dict[str, Any],
    ring_meta: Optional[dict[str, Any]] = None,
) -> None:
    cam = config.get("camera", {})
    perf = config.get("performance", {})
    index = int(cam.get("index", 0))
    width = int(cam.get("width", 1280))
    height = int(cam.get("height", 720))
    target_fps = float(cam.get("fps", 30))
    period = 1.0 / max(1.0, target_fps)
    buffer_size = int(perf.get("camera_buffer", 1))

    ring_meta = ring_meta or config.get("_frame_ring")
    if not ring_meta:
        raise RuntimeError("camera_process_main requires FrameRing metadata")
    ring = FrameRing.from_meta(ring_meta)

    camera_matrix, dist = load_calibration(cam.get("calibration_file"))
    cap, fcc = _open_webcam(
        index,
        width,
        height,
        target_fps,
        buffer_size,
        str(cam.get("backend", "dshow")),
    )
    use_synthetic = cap is None or not cap.isOpened()

    frame_id = 0
    t_last = time.time()

    try:
        while not stop_event.is_set():
            t0 = time.time()
            if use_synthetic:
                frame = _synthetic_frame(width, height, frame_id)
            else:
                ok, frame = cap.read()
                if not ok or frame is None:
                    frame = _synthetic_frame(width, height, frame_id)

            if camera_matrix is not None:
                frame = undistort(frame, camera_matrix, dist)
            # Don't upsample 720p to 1080 — extra CPU, still looks like 5 FPS.
            if frame.shape[1] > width or frame.shape[0] > height:
                frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)

            now = time.time()
            dt = now - t_last
            t_last = now
            fps = 1.0 / dt if dt > 1e-6 else 0.0
            frame_id += 1

            try:
                slot, fw, fh = ring.write(frame)
            except Exception:
                continue

            put_latest(
                frame_q,
                {
                    "ts": now,
                    "frame_id": frame_id,
                    "fps": fps,
                    "slot": slot,
                    "width": fw,
                    "height": fh,
                    "synthetic": use_synthetic,
                    "log_line": (
                        f"Camera {index} {fw}x{fh} fourcc={fcc or '?'} {fps:.0f} fps shm"
                        if frame_id == 1
                        else None
                    ),
                },
            )

            elapsed = time.time() - t0
            wait = period - elapsed
            if wait > 0.001:
                time.sleep(wait)
    finally:
        ring.close()
        if cap is not None:
            cap.release()


def _synthetic_frame(width: int, height: int, frame_id: int) -> np.ndarray:
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:, :] = (60, 40, 20)
    cv2.rectangle(frame, (0, int(height * 0.65)), (width, height), (40, 70, 40), -1)
    t = frame_id / 30.0
    cx = int(width * 0.5 + width * 0.28 * np.sin(t * 0.9))
    cy = int(height * 0.35 + height * 0.12 * np.cos(t * 1.1))
    cv2.ellipse(frame, (cx, cy), (40, 18), 0, 0, 360, (200, 200, 220), -1)
    cv2.putText(
        frame,
        "SYNTHETIC CAMERA",
        (20, height - 20),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (180, 180, 180),
        2,
    )
    return frame


def start_camera_process(
    frame_q: Queue,
    stop_event,
    config: dict,
    ring_meta: Optional[dict[str, Any]] = None,
) -> Process:
    p = Process(
        target=camera_process_main,
        args=(frame_q, stop_event, config, ring_meta),
        name="CameraProcess",
        daemon=True,
    )
    p.start()
    return p
