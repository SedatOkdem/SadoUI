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
        "F16": "F16",
        "fighter": "F16",
        "jet": "F16",
        "airplane": "F16",
        "aeroplane": "F16",
        "heli": "Helikopter",
        "helicopter": "Helikopter",
        "helikopter": "Helikopter",
        "missile": "BalistikFuze",
        "balistik": "BalistikFuze",
        "Balistik Füze": "BalistikFuze",
        "fuze": "BalistikFuze",
        "füze": "BalistikFuze",
        "uav": "MiniIHA",
        "drone": "MiniIHA",
        "Drone": "MiniIHA",
        "Mini/Micro İHA": "MiniIHA",
        "MiniIHA": "MiniIHA",
        "mini_iha": "MiniIHA",
        "mini-iha": "MiniIHA",
        "miniiha": "MiniIHA",
        "iha": "MiniIHA",
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


def display_target_name(label: str, hostile: bool | None = None) -> str:
    """UI tag: 'Dost Helikopter' / 'Düşman F16' (type always visible when known)."""
    lab = normalize_label(label)
    side_hostile = is_hostile(lab) if hostile is None else bool(hostile)
    side = "Düşman" if side_hostile else "Dost"
    if lab in HOSTILE_TYPES:
        return f"{side} {lab}"
    if lab == "Dost":
        return "Dost"
    return f"{side} {lab}" if lab else side


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
    hostile: bool | None = None,
) -> bool:
    if not locked:
        return False
    if stage == Stage.STAGE1:
        return False
    if stage == Stage.STAGE2:
        return True
    # Stage 3
    is_h = is_hostile(label) if hostile is None else bool(hostile)
    if not is_h:
        return False
    return in_wez(config, label, range_m)
