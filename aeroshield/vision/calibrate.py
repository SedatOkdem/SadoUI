"""Optional camera undistort helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np


def load_calibration(path: Optional[str]) -> tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    if not path:
        return None, None
    p = Path(path)
    if not p.exists():
        return None, None
    if p.suffix.lower() == ".npz":
        data = np.load(str(p))
        return data["camera_matrix"], data["dist_coeffs"]
    # OpenCV FileStorage YAML/XML
    fs = cv2.FileStorage(str(p), cv2.FILE_STORAGE_READ)
    if not fs.isOpened():
        return None, None
    camera_matrix = fs.getNode("camera_matrix").mat()
    dist_coeffs = fs.getNode("dist_coeffs").mat()
    fs.release()
    return camera_matrix, dist_coeffs


def undistort(frame: np.ndarray, camera_matrix, dist_coeffs) -> np.ndarray:
    if camera_matrix is None or dist_coeffs is None:
        return frame
    h, w = frame.shape[:2]
    new_k, _ = cv2.getOptimalNewCameraMatrix(camera_matrix, dist_coeffs, (w, h), 1)
    return cv2.undistort(frame, camera_matrix, dist_coeffs, None, new_k)
