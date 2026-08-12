"""Stage rules and WEZ (weapon engagement zone) helpers."""

from __future__ import annotations

from typing import Any

from aeroshield.core.fsm import Stage


# Friendly / hostile names used in overlay and FSM
HOSTILE_TYPES = {"F16", "Helikopter", "BalistikFuze", "MiniIHA"}
FRIENDLY_TYPES = {"Dost", "Friendly"}


def normalize_label(name: str) -> str:
    n = (name or "").strip()
    aliases = {
        "F-16": "F16",
        "f16": "F16",
        "airplane": "F16",
        "heli": "Helikopter",
        "helicopter": "Helikopter",
        "missile": "BalistikFuze",
        "balistik": "BalistikFuze",
        "Balistik Füze": "BalistikFuze",
        "uav": "MiniIHA",
        "drone": "MiniIHA",
        "Mini/Micro İHA": "MiniIHA",
        "MiniIHA": "MiniIHA",
        "bird": "MiniIHA",
        "kite": "BalistikFuze",
        "dost": "Dost",
        "friend": "Dost",
        "friendly": "Dost",
    }
    return aliases.get(n, aliases.get(n.lower(), n))


def is_hostile(label: str) -> bool:
    lab = normalize_label(label)
    if lab in FRIENDLY_TYPES or lab == "Dost":
        return False
    return lab in HOSTILE_TYPES or lab not in FRIENDLY_TYPES


def wez_limits(config: dict[str, Any], label: str) -> tuple[float, float]:
    wez = config.get("wez", {})
    lab = normalize_label(label)
    pair = wez.get(lab, [0.0, 15.0])
    return float(pair[0]), float(pair[1])


def in_wez(config: dict[str, Any], label: str, range_m: float | None) -> bool:
    if range_m is None:
        return False
    lo, hi = wez_limits(config, label)
    return lo <= float(range_m) <= hi


def wez_status_text(config: dict[str, Any], label: str, range_m: float | None) -> str:
    lo, hi = wez_limits(config, label)
    if range_m is None:
        return f"— (WEZ {lo:.0f}–{hi:.0f} m)"
    ok = in_wez(config, label, range_m)
    flag = "OK" if ok else "DIŞI"
    return f"{float(range_m):.1f} m ({flag}, WEZ {lo:.0f}–{hi:.0f} m)"


def auto_fire_allowed(
    stage: Stage,
    label: str,
    range_m: float | None,
    config: dict[str, Any],
    locked: bool,
) -> bool:
    if not locked:
        return False
    if stage == Stage.STAGE1:
        return False
    if stage == Stage.STAGE2:
        return True
    # Stage 3
    if not is_hostile(label):
        return False
    return in_wez(config, label, range_m)
