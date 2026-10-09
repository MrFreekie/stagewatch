"""Everything Stagewatch may ever send to Smaart: exactly four messages, built here and checked here.

Owner decision 09/10/2026. The client can only send through ``allowed()``; a message that is not one of
these four shapes is refused before it reaches the socket, and a test pins the list.

1. ``{"action":"get"}``                                              ask whether a password is needed
2. ``{"action":"get","target":"activeCalibratedInputs"}``            list the inputs and metric names
3. ``{"action":"set","properties":[{"password":"<API password>"}]}`` log in (nothing else)
4. ``{"action":"set","properties":[{"targetFPS":1}]}``               this one meter connection's update rate

None of them changes Smaart's measurement, gain, calibration, logging, alarms or mix. Nothing else is
ever sent: no other ``set``, no control, no history stream request. (The shapes are from Smaart's own
web page script; not yet tested against a live Smaart.)
"""

from __future__ import annotations

import json

TARGET_FPS = 1   # we record at most one value a second; Smaart's page allows 1 to 8

PROBE = "get"
INPUTS = "inputs"
LOGIN = "login"
FPS = "fps"


def _dump(obj: object) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def probe() -> str:
    return _dump({"action": "get"})


def inputs() -> str:
    return _dump({"action": "get", "target": "activeCalibratedInputs"})


def login(password: str) -> str:
    return _dump({"action": "set", "properties": [{"password": password}]})


def fps() -> str:
    return _dump({"action": "set", "properties": [{"targetFPS": TARGET_FPS}]})


def kind_of(text: object) -> str | None:
    """Which of the four messages ``text`` is, or None. The comparison is on the decoded shape, so it
    is exact: no extra keys, no other action, no other target, the frame rate is the fixed one."""
    if not isinstance(text, str):
        return None
    try:
        doc = json.loads(text)
    except ValueError:
        return None
    if doc == {"action": "get"}:
        return PROBE
    if doc == {"action": "get", "target": "activeCalibratedInputs"}:
        return INPUTS
    if isinstance(doc, dict) and set(doc) == {"action", "properties"} and doc["action"] == "set":
        props = doc["properties"]
        if isinstance(props, list) and len(props) == 1 and isinstance(props[0], dict) and len(props[0]) == 1:
            (key, value), = props[0].items()
            if key == "password" and isinstance(value, str):
                return LOGIN
            if key == "targetFPS" and value == TARGET_FPS and type(value) is int:
                return FPS
    return None


def allowed(text: object) -> bool:
    return kind_of(text) is not None
