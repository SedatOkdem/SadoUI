"""UART binary protocol between host PC and ESP32 (KTR 4.3.2)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Optional


SOF = 0xAA
EOF = 0xFF
# [AA][MOD][PAN_H][PAN_L][TILT_H][TILT_L][FIRE]
# [IDLE_H][IDLE_L][FIRE_H][FIRE_L][SPIN_CS][CHK][FF]
CMD_LEN = 14
STATUS_LEN = 6
ESC_MIN_US = 1000
ESC_MAX_US = 2000  # full ESC range; panel can set freely for now


class ModCode(IntEnum):
    MANUAL = 0
    SEMI = 1
    AUTO = 2
    ESTOP = 3
    HOME = 4


class StatusCode(IntEnum):
    OK = 0
    LIMIT = 1
    FAILSAFE = 2
    ESTOP = 3
    HOME = 4
    BUSY = 5


def xor_checksum(data: bytes) -> int:
    c = 0
    for b in data:
        c ^= b
    return c & 0xFF


def encode_i16(value: int) -> tuple[int, int]:
    value = max(-32768, min(32767, int(value)))
    if value < 0:
        value += 65536
    return (value >> 8) & 0xFF, value & 0xFF


def decode_i16(hi: int, lo: int) -> int:
    value = ((hi & 0xFF) << 8) | (lo & 0xFF)
    if value >= 32768:
        value -= 65536
    return value


def clamp_esc_us(us: int) -> int:
    return max(ESC_MIN_US, min(ESC_MAX_US, int(us)))


@dataclass
class CommandPacket:
    mod: int = ModCode.MANUAL
    pan: int = 0
    tilt: int = 0
    fire: int = 0
    esc_idle_us: int = 1060
    esc_fire_us: int = 1200
    fire_spin_ms: int = 450

    def to_bytes(self) -> bytes:
        pan_h, pan_l = encode_i16(self.pan)
        tilt_h, tilt_l = encode_i16(self.tilt)
        idle = clamp_esc_us(self.esc_idle_us)
        fire_us = clamp_esc_us(self.esc_fire_us)
        if fire_us < idle:
            fire_us = idle
        idle_h, idle_l = encode_i16(idle)
        fire_h, fire_l = encode_i16(fire_us)
        spin_cs = max(1, min(255, int(round(self.fire_spin_ms / 10.0))))
        body = bytes(
            [
                SOF,
                int(self.mod) & 0xFF,
                pan_h,
                pan_l,
                tilt_h,
                tilt_l,
                1 if self.fire else 0,
                idle_h,
                idle_l,
                fire_h,
                fire_l,
                spin_cs & 0xFF,
            ]
        )
        checksum = xor_checksum(body[1:])  # MOD..SPIN
        return body + bytes([checksum, EOF])


@dataclass
class StatusPacket:
    status: int = StatusCode.OK
    limit_pan: int = 0
    limit_tilt: int = 0

    @classmethod
    def from_bytes(cls, raw: bytes) -> Optional["StatusPacket"]:
        if len(raw) != STATUS_LEN:
            return None
        if raw[0] != SOF or raw[-1] != EOF:
            return None
        payload = raw[1:4]
        checksum = raw[4]
        if xor_checksum(payload) != checksum:
            return None
        return cls(status=raw[1], limit_pan=raw[2], limit_tilt=raw[3])

    def to_bytes(self) -> bytes:
        payload = bytes(
            [int(self.status) & 0xFF, int(self.limit_pan) & 0xFF, int(self.limit_tilt) & 0xFF]
        )
        return bytes([SOF]) + payload + bytes([xor_checksum(payload), EOF])


class PacketParser:
    """Incremental parser for ESP32 status frames."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[StatusPacket]:
        self._buf.extend(data)
        packets: list[StatusPacket] = []
        while True:
            try:
                start = self._buf.index(SOF)
            except ValueError:
                self._buf.clear()
                break
            if start > 0:
                del self._buf[:start]
            if len(self._buf) < STATUS_LEN:
                break
            chunk = bytes(self._buf[:STATUS_LEN])
            pkt = StatusPacket.from_bytes(chunk)
            if pkt is None:
                del self._buf[0]
                continue
            del self._buf[:STATUS_LEN]
            packets.append(pkt)
        return packets
