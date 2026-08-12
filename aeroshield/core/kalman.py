"""2D Kalman filter for target center prediction."""

from __future__ import annotations

import numpy as np


class Kalman2D:
    """Constant-velocity Kalman filter on (x, y)."""

    def __init__(self, process_noise: float = 8.0, meas_noise: float = 12.0) -> None:
        self.x = np.zeros((4, 1), dtype=np.float64)  # x, y, vx, vy
        self.P = np.eye(4, dtype=np.float64) * 100.0
        self.q = float(process_noise)
        self.r = float(meas_noise)
        self.initialized = False

    def reset(self) -> None:
        self.x[:] = 0
        self.P = np.eye(4, dtype=np.float64) * 100.0
        self.initialized = False

    def predict(self, dt: float) -> tuple[float, float, float, float]:
        if dt <= 0:
            dt = 1e-3
        F = np.array(
            [
                [1, 0, dt, 0],
                [0, 1, 0, dt],
                [0, 0, 1, 0],
                [0, 0, 0, 1],
            ],
            dtype=np.float64,
        )
        Q = np.array(
            [
                [dt**4 / 4, 0, dt**3 / 2, 0],
                [0, dt**4 / 4, 0, dt**3 / 2],
                [dt**3 / 2, 0, dt**2, 0],
                [0, dt**3 / 2, 0, dt**2],
            ],
            dtype=np.float64,
        ) * (self.q**2)
        self.x = F @ self.x
        self.P = F @ self.P @ F.T + Q
        return float(self.x[0, 0]), float(self.x[1, 0]), float(self.x[2, 0]), float(self.x[3, 0])

    def update(self, zx: float, zy: float) -> tuple[float, float, float, float]:
        if not self.initialized:
            self.x[:, 0] = [zx, zy, 0.0, 0.0]
            self.initialized = True
            return zx, zy, 0.0, 0.0
        H = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], dtype=np.float64)
        R = np.eye(2, dtype=np.float64) * (self.r**2)
        z = np.array([[zx], [zy]], dtype=np.float64)
        y = z - H @ self.x
        S = H @ self.P @ H.T + R
        K = self.P @ H.T @ np.linalg.inv(S)
        self.x = self.x + K @ y
        I = np.eye(4)
        self.P = (I - K @ H) @ self.P
        return float(self.x[0, 0]), float(self.x[1, 0]), float(self.x[2, 0]), float(self.x[3, 0])
