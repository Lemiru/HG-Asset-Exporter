"""What each room's zones and cameras are for.

The game keeps this in its room scripts, compiled into the executable.
``data/room_logic.json`` holds the result, derived by
``tools/derive_room_logic.py``.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_ROOM_LOGIC = Path(__file__).resolve().parent.parent / "data" / "room_logic.json"
SHARED_ACTIONS = 0x80          # action numbers from here on index the game's shared table
MAX_ACTIONS_IN_NAME = 3
NAME_PART_SEPARATOR = "__"     # between the parts of a zone name; words inside a part use "_"


@lru_cache(maxsize=1)
def room_logic() -> dict:
    try:
        return json.loads(_ROOM_LOGIC.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def room_entry(room: str) -> dict:
    return room_logic().get(room, {})


def rail_set(room: str, rail: int) -> int | None:
    """The camera set a room's scripts use with this rail."""
    return room_entry(room).get("camera_rails", {}).get(str(rail))


def fixed_sets(room: str) -> list[int]:
    """The camera sets a room's scripts use without a rail."""
    return room_entry(room).get("camera_fixed_sets", [])


def camera_node(room: str, set_index: int | None, rail: int | None) -> str:
    """Name of a gameplay camera node in the room file."""
    if rail is None:
        return f"{room}_cam_set{set_index}_fixed"
    if set_index is None:
        return f"{room}_cam_rail{rail}"
    return f"{room}_cam_set{set_index}_rail{rail}"


def _action_token(action: int) -> str:
    return f"shared_action{action - SHARED_ACTIONS}" if action >= SHARED_ACTIONS else f"action{action}"


def _camera_token(camera: dict) -> str:
    if camera["rail"] is None:
        return f"set{camera['set']}_fixed"
    return f"set{camera['set']}_rail{camera['rail']}"


def zone_purposes(uses: dict | None) -> list[str]:
    """Name parts for a zone, most specific first; ["unknown"] if nothing the
    game does was found to use it."""
    if not uses:
        return ["unknown"]
    parts = [f"exit{e['exit']}" for e in uses.get("exits", [])]
    parts += dict.fromkeys(f"camera_{_camera_token(c)}" for c in uses.get("cameras", []))
    parts += [f"prefetch_exit{p['exit']}" if "exit" in p else f"prefetch_{p['room']}" for p in uses.get("prefetch", [])]
    actions = [_action_token(a) for a in sorted(uses.get("actions", []))]
    if len(actions) > MAX_ACTIONS_IN_NAME:
        actions[MAX_ACTIONS_IN_NAME:] = [f"plus{len(actions) - MAX_ACTIONS_IN_NAME}"]
    parts += actions
    if uses.get("facing"):
        parts.append("facing")
    if parts:
        return parts
    return ["unused_here"] if "used_in" in uses else ["trigger"]


def zone_name(index: int, kind: str, uses: dict | None) -> str:
    return NAME_PART_SEPARATOR.join([f"zone{index:02d}", kind, *zone_purposes(uses)])


def _has_camera(room: str, camera: dict) -> bool:
    """False for a rail the room's file does not have (the game ignores those)."""
    return camera["rail"] is None or rail_set(room, camera["rail"]) is not None


def zone_extras(room: str, uses: dict | None) -> dict:
    """The zone's uses for its node's extras, cameras with their node names."""
    if not uses:
        return {}
    out = {k: v for k, v in uses.items() if k != "cameras"}
    if "cameras" in uses:
        out["cameras"] = [{**c, "node": camera_node(room, c["set"], c["rail"]) if _has_camera(room, c) else None}
                          for c in uses["cameras"]]
    return out
