"""Rate PID with hard reverse brake and slow same-direction accel."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PID:
    kp: float = 0.4
    ki: float = 0.0
    kd: float = 0.05
    i_limit: float = 50.0
    out_limit: float = 500.0
    accel_dps2: float = 40.0
    _integral: float = 0.0
    _prev_error: float = 0.0
    _has_prev: bool = False
    _last_out: float = 0.0

    @classmethod
    def from_dict(cls, d: dict) -> "PID":
        return cls(
            kp=float(d.get("kp", 0.4)),
            ki=float(d.get("ki", 0.0)),
            kd=float(d.get("kd", 0.05)),
            i_limit=float(d.get("i_limit", 50.0)),
            out_limit=float(d.get("out_limit", 500.0)),
            accel_dps2=float(d.get("accel_dps2", 40.0)),
        )

    def reset(self) -> None:
        self._integral = 0.0
        self._prev_error = 0.0
        self._has_prev = False
        self._last_out = 0.0

    def update(self, error: float, dt: float) -> float:
        if dt <= 0:
            dt = 1e-3
        reversing = self._has_prev and error * self._prev_error < 0.0
        if reversing:
            self._integral = 0.0
            self._last_out = 0.0

        self._integral += error * dt
        self._integral = max(-self.i_limit, min(self.i_limit, self._integral))
        derivative = 0.0
        if self._has_prev and not reversing:
            derivative = (error - self._prev_error) / dt
        self._prev_error = error
        self._has_prev = True
        raw = self.kp * error + self.ki * self._integral + self.kd * derivative
        raw = max(-self.out_limit, min(self.out_limit, raw))

        if reversing:
            # Hard brake already zeroed last_out; small step in the new direction.
            mag = min(abs(raw), 2.0)
            out = mag if raw >= 0.0 else -mag
        elif self._last_out == 0.0 or raw * self._last_out >= 0.0:
            if abs(raw) < abs(self._last_out):
                out = raw
            else:
                step = self.accel_dps2 * dt
                if raw > self._last_out:
                    out = min(raw, self._last_out + step)
                else:
                    out = max(raw, self._last_out - step)
        else:
            out = 0.0

        self._last_out = out
        return out
