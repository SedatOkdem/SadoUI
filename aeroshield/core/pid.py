"""PID controller with anti-windup."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PID:
    kp: float = 0.4
    ki: float = 0.0
    kd: float = 0.05
    i_limit: float = 50.0
    out_limit: float = 500.0
    _integral: float = 0.0
    _prev_error: float = 0.0
    _has_prev: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> "PID":
        return cls(
            kp=float(d.get("kp", 0.4)),
            ki=float(d.get("ki", 0.0)),
            kd=float(d.get("kd", 0.05)),
            i_limit=float(d.get("i_limit", 50.0)),
        )

    def reset(self) -> None:
        self._integral = 0.0
        self._prev_error = 0.0
        self._has_prev = False

    def update(self, error: float, dt: float) -> float:
        if dt <= 0:
            dt = 1e-3
        self._integral += error * dt
        self._integral = max(-self.i_limit, min(self.i_limit, self._integral))
        derivative = 0.0
        if self._has_prev:
            derivative = (error - self._prev_error) / dt
        self._prev_error = error
        self._has_prev = True
        out = self.kp * error + self.ki * self._integral + self.kd * derivative
        return max(-self.out_limit, min(self.out_limit, out))
