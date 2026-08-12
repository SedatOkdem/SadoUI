"""Camera capture process — low-latency grab."""

from __future__ import annotations

import time
from multiprocessing import Process, Queue
from typing import Any

import cv2
import numpy as np

from aeroshield.vision.calibrate import load_calibration, undistort
from aeroshield.workers.ipc import put_latest


def camera_process_main(frame_q: Queue, stop_event, config: dict[str, Any]) -> None:
    cam = config.get("camera", {})
    perf = config.get("performance", {})
    index = int(cam.get("index", 0))
    width = int(cam.get("width", 1280))
    height = int(cam.get("height", 720))
    target_fps = float(cam.get("fps", 60))
    period = 1.0 / max(1.0, target_fps)
    jpeg_q = int(perf.get("jpeg_quality", 70))
    buffer_size = int(perf.get("camera_buffer", 1))

    camera_matrix, dist = load_calibration(cam.get("calibration_file"))
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap = cv2.VideoCapture(index)

    if cap.isOpened():
        # MJPG drastically reduces USB bandwidth / CPU on webcams
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        cap.set(cv2.CAP_PROP_FPS, target_fps)
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, buffer_size)
        except Exception:
            pass

    frame_id = 0
    use_synthetic = not cap.isOpened()
    t_last = time.time()

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
        if frame.shape[1] != width or frame.shape[0] != height:
            frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_LINEAR)

        now = time.time()
        dt = now - t_last
        t_last = now
        fps = 1.0 / dt if dt > 1e-6 else 0.0
        frame_id += 1

        ok_j, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_q])
        if ok_j:
            put_latest(
                frame_q,
                {
                    "ts": now,
                    "frame_id": frame_id,
                    "fps": fps,
                    "width": frame.shape[1],
                    "height": frame.shape[0],
                    "jpeg": buf.tobytes(),
                    "synthetic": use_synthetic,
                },
            )

        elapsed = time.time() - t0
        # Don't oversleep — if capture already slow, skip wait
        wait = period - elapsed
        if wait > 0.001:
            time.sleep(wait)

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


def start_camera_process(frame_q: Queue, stop_event, config: dict) -> Process:
    p = Process(
        target=camera_process_main,
        args=(frame_q, stop_event, config),
        name="CameraProcess",
        daemon=True,
    )
    p.start()
    return p
