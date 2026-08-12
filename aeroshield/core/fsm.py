"""Atış Kontrol Yazılımı (AKY) finite state machine."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


class SystemState(Enum):
    BIT = auto()
    READY = auto()
    SEARCH = auto()
    TRACK = auto()
    LOCK = auto()
    ENGAGE = auto()
    BDA = auto()
    MAINT = auto()
    ESTOP = auto()
    FAILSAFE = auto()


class Stage(Enum):
    STAGE1 = 1
    STAGE2 = 2
    STAGE3 = 3


@dataclass
class FsmContext:
    has_target: bool = False
    locked: bool = False
    wez_ok: bool = False
    is_hostile: bool = True
    fire_requested: bool = False
    fire_done: bool = False
    bda_lost: bool = False
    bit_ok: bool = False
    estop: bool = False
    hw_estop: bool = False
    failsafe: bool = False
    maint: bool = False
    operator_start: bool = False
    estop_cleared: bool = False


@dataclass
class FireControlFSM:
    state: SystemState = SystemState.BIT
    stage: Stage = Stage.STAGE1
    _prev: SystemState = SystemState.BIT
    log_events: list[str] = field(default_factory=list)

    def reset_to_ready(self) -> None:
        self.state = SystemState.READY
        self._emit("FSM -> READY")

    def force_estop(self) -> None:
        if self.state != SystemState.ESTOP:
            self.state = SystemState.ESTOP
            self._emit("FSM -> ESTOP")

    def force_failsafe(self) -> None:
        if self.state not in (SystemState.ESTOP, SystemState.FAILSAFE):
            self.state = SystemState.FAILSAFE
            self._emit("FSM -> FAILSAFE")

    def enter_maint(self) -> None:
        if self.state != SystemState.ESTOP:
            self._prev = self.state
            self.state = SystemState.MAINT
            self._emit("FSM -> MAINT")

    def leave_maint(self) -> None:
        if self.state == SystemState.MAINT:
            self.state = SystemState.READY
            self._emit("FSM -> READY (from MAINT)")

    def update(self, ctx: FsmContext) -> SystemState:
        # Highest priority: enter / stay in ESTOP while any stop source is active
        # and operator has not requested clear.
        if (ctx.estop or ctx.hw_estop or ctx.failsafe) and not ctx.estop_cleared:
            self.force_estop()
            return self.state

        if self.state == SystemState.ESTOP:
            # Reset only when clear is requested AND hardware/failsafe are released.
            if ctx.estop_cleared and not ctx.hw_estop and not ctx.failsafe:
                self.state = SystemState.READY
                self._emit("FSM ESTOP cleared -> READY")
            return self.state

        if ctx.failsafe:
            self.force_failsafe()
            return self.state

        if self.state == SystemState.FAILSAFE:
            if not ctx.failsafe and ctx.bit_ok:
                self.state = SystemState.READY
                self._emit("FSM FAILSAFE cleared -> READY")
            return self.state

        if ctx.maint and self.state not in (SystemState.ESTOP, SystemState.MAINT):
            self.enter_maint()
            return self.state

        if self.state == SystemState.MAINT:
            if not ctx.maint:
                self.leave_maint()
            return self.state

        if self.state == SystemState.BIT:
            if ctx.bit_ok:
                self.state = SystemState.READY
                self._emit("FSM BIT ok -> READY")
            return self.state

        if self.state == SystemState.READY:
            if ctx.operator_start:
                self.state = SystemState.SEARCH
                self._emit("FSM READY -> SEARCH")
            return self.state

        # Stage 1 stays operator-driven; SEARCH just means armed for manual
        if self.stage == Stage.STAGE1:
            if self.state in (SystemState.SEARCH, SystemState.TRACK, SystemState.LOCK, SystemState.ENGAGE, SystemState.BDA):
                if ctx.fire_requested:
                    self.state = SystemState.ENGAGE
                elif ctx.has_target:
                    self.state = SystemState.TRACK
                else:
                    self.state = SystemState.SEARCH
            return self.state

        # Stage 2 / 3 autonomous path
        if self.state == SystemState.SEARCH:
            if ctx.has_target:
                self.state = SystemState.TRACK
                self._emit("FSM SEARCH -> TRACK")
            return self.state

        if self.state == SystemState.TRACK:
            if not ctx.has_target:
                self.state = SystemState.SEARCH
                self._emit("FSM TRACK -> SEARCH (lost)")
            elif ctx.locked and ctx.wez_ok and (self.stage != Stage.STAGE3 or ctx.is_hostile):
                self.state = SystemState.LOCK
                self._emit("FSM TRACK -> LOCK")
            return self.state

        if self.state == SystemState.LOCK:
            if not ctx.has_target:
                self.state = SystemState.SEARCH
            elif not ctx.locked or not ctx.wez_ok:
                self.state = SystemState.TRACK
            elif self.stage in (Stage.STAGE2, Stage.STAGE3) and ctx.is_hostile and ctx.wez_ok:
                self.state = SystemState.ENGAGE
                self._emit("FSM LOCK -> ENGAGE")
            return self.state

        if self.state == SystemState.ENGAGE:
            if ctx.fire_done:
                self.state = SystemState.BDA
                self._emit("FSM ENGAGE -> BDA")
            elif not ctx.has_target:
                self.state = SystemState.SEARCH
            return self.state

        if self.state == SystemState.BDA:
            if ctx.bda_lost or not ctx.has_target:
                self.state = SystemState.SEARCH
                self._emit("FSM BDA -> SEARCH (destroyed/lost)")
            else:
                self.state = SystemState.TRACK
                self._emit("FSM BDA -> TRACK (reacquire)")
            return self.state

        return self.state

    def allow_auto_fire(self) -> bool:
        return self.stage in (Stage.STAGE2, Stage.STAGE3) and self.state == SystemState.ENGAGE

    def allow_manual_fire(self) -> bool:
        return self.stage == Stage.STAGE1 and self.state not in (
            SystemState.ESTOP,
            SystemState.FAILSAFE,
            SystemState.BIT,
            SystemState.MAINT,
        )

    def _emit(self, msg: str) -> None:
        self.log_events.append(msg)

    def pop_events(self) -> list[str]:
        events = list(self.log_events)
        self.log_events.clear()
        return events
