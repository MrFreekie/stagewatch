"""Set the clock from this device: validation, jump confirmation, rate limit, can_set states, the API, the marker,
no shell, fixed non-echoing errors, and the root helper script run under bash with fake commands.

Nothing here runs sudo or changes a clock: the runner, the clock and the platform are all injected."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stagewatch.core import clockset as cs
from stagewatch.core.clockset import ClockError, ClockSetter
from stagewatch.core.hub import Hub
from stagewatch.web.server import create_app

REPO = Path(__file__).resolve().parents[1]
T0 = 1_761_000_000                      # 2025-10-20, inside the allowed range


class World:
    """A fake clock and a fake sudo/timedatectl."""

    def __init__(self, t=T0, sudo_ok=True, ntp="no", helper_reply=None, helper_rc=0):
        self.t = float(t)
        self.m = 1000.0
        self.sudo_ok = sudo_ok
        self.ntp = ntp
        self.helper_reply = helper_reply
        self.helper_rc = helper_rc
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []

    def wall(self):
        return self.t

    def mono(self):
        return self.m

    def advance(self, s):
        self.t += s
        self.m += s

    def runner(self, argv, timeout=None, **kw):
        self.calls.append(list(argv))
        self.kwargs.append(kw)
        assert isinstance(argv, list) and all(isinstance(a, str) for a in argv)
        if argv[:3] == ["sudo", "-n", "-l"]:
            return subprocess.CompletedProcess(argv, 0 if self.sudo_ok else 1, stdout="", stderr="")
        if argv[0] == "timedatectl":
            return subprocess.CompletedProcess(argv, 0, stdout=f"NTPSynchronized={self.ntp}\nTimezone=Europe/London\n", stderr="")
        if argv[:2] == ["sudo", "-n"]:
            target = int(argv[3])
            if self.helper_rc == 0:
                self.t = float(target)
            return subprocess.CompletedProcess(argv, self.helper_rc,
                                               stdout=self.helper_reply or f"OK {target} rtc=no\n", stderr="")
        raise AssertionError(argv)


@pytest.fixture
def world():
    return World()


@pytest.fixture
def hub(tmp_path):
    h = Hub(tmp_path, emulate=False)
    yield h
    h.recorder.close()


def setter(hub, world, platform="linux", helper_exists=True):
    return ClockSetter(hub, runner=world.runner, platform=platform, wall=world.wall, mono=world.mono,
                       exists=lambda _p: helper_exists)


def ms(world, offset_s):
    return int((world.t + offset_s) * 1000)


# ------------------------------------------------------------------ the maths and validation
def test_elapsed_time_is_added_then_rounded(hub, world):
    """The browser says the request reaches us at T0 + 100 s.  2.6 s pass before we apply it, so the clock is set
    to T0 + 102.6, which rounds to T0 + 103 (hand sum)."""
    s = setter(hub, world)
    received = world.m
    world.m += 2.6                                      # monotonic time passes; the fake wall clock stays put
    res = s.set((T0 + 100) * 1000, 10, True, received)
    assert world.calls[-1][-1] == str(T0 + 103) and res["changed_by_s"] == 103


def test_a_short_wait_rounds_down(hub, world):
    s = setter(hub, world)
    received = world.m
    world.m += 0.4                                      # T0 + 100.4 -> T0 + 100
    s.set((T0 + 100) * 1000, 10, True, received)
    assert world.calls[-1][-1] == str(T0 + 100)


def test_small_correction_applies_without_confirmation(hub, world):
    s = setter(hub, world)
    res = s.set((T0 + 3) * 1000, 10, False, world.m)    # 3 s ahead: no confirmation needed
    assert res["ok"] and not res["emulated"] and res["changed_by_s"] == 3
    assert world.calls[-1] == ["sudo", "-n", cs.HELPER, str(T0 + 3)]
    assert world.t == T0 + 3


def test_exactly_five_seconds_needs_no_confirmation_six_does(hub, world):
    s = setter(hub, world)
    with pytest.raises(ClockError) as e:
        s.set((T0 + 6) * 1000, 10, False, world.m)
    assert e.value.code == "needs_confirm" and e.value.status == 409
    assert world.t == T0 and not any(c[:2] == ["sudo", "-n"] and len(c) == 4 and c[2] != "-l" for c in world.calls)
    world.m += 20                                       # past the rate limit window of nothing; first call set no stamp
    assert s.set((T0 + 5) * 1000, 10, False, world.m)["changed_by_s"] == 5


def test_big_jump_with_confirmation_forward_and_back(hub, world):
    s = setter(hub, world)
    assert s.set((T0 + 11520) * 1000, 30, True, world.m)["changed_by_s"] == 11520      # 3 h 12 min
    world.advance(30)
    back = s.set((T0 - 500) * 1000, 30, True, world.m)
    assert back["changed_by_s"] == -(11520 + 30 + 500)
    assert world.t == T0 - 500


def test_under_one_second_is_nothing_to_do(hub, world):
    s = setter(hub, world)
    with pytest.raises(ClockError) as e:
        s.set(int((T0 + 0.6) * 1000), 10, False, world.m)
    assert e.value.code == "nothing_to_do"
    assert not any(c[-1].isdigit() for c in world.calls)


@pytest.mark.parametrize("epoch_ms", [cs.EPOCH_MIN_MS - 1, cs.EPOCH_MAX_MS, 0, -5, 10**30])
def test_epoch_outside_2025_to_2100_refused(hub, world, epoch_ms):
    with pytest.raises(ClockError) as e:
        setter(hub, world).set(epoch_ms, 10, True, world.m)
    assert e.value.code == "out_of_range" and e.value.status == 422


@pytest.mark.parametrize("rtt", [-1, 5001, 10**9])
def test_rtt_outside_0_to_5000_refused(hub, world, rtt):
    with pytest.raises(ClockError) as e:
        setter(hub, world).set((T0 + 60) * 1000, rtt, True, world.m)
    assert e.value.code == "out_of_range"


def test_rtt_edges_accepted(hub, world):
    s = setter(hub, world)
    s.set((T0 + 60) * 1000, 0, True, world.m)
    world.advance(11)
    s.set((T0 + 200) * 1000, 5000, True, world.m)


def test_rate_limit_one_per_ten_seconds(hub, world):
    s = setter(hub, world)
    s.set((T0 + 60) * 1000, 10, True, world.m)
    world.advance(9.9)
    with pytest.raises(ClockError) as e:
        s.set(ms(world, 60), 10, True, world.m)
    assert e.value.code == "rate_limited" and e.value.status == 429
    world.advance(0.2)
    s.set(ms(world, 60), 10, True, world.m)


# ------------------------------------------------------------------ can_set states
def test_can_set_true_when_helper_and_sudo_rule_present(hub, world):
    ok, why = setter(hub, world).can_set()
    assert ok and why == ""
    assert world.calls == [["sudo", "-n", "-l", cs.HELPER]]


def test_can_set_false_without_helper_file(hub, world):
    s = setter(hub, world, helper_exists=False)
    assert s.can_set() == (False, cs.NOT_SET_UP)
    assert world.calls == []
    with pytest.raises(ClockError) as e:
        s.set((T0 + 60) * 1000, 10, True, world.m)
    assert e.value.code == "not_set_up"


def test_can_set_false_without_sudo_rule(hub):
    w = World(sudo_ok=False)
    s = setter(hub, w)
    assert s.can_set() == (False, cs.NOT_SET_UP)
    with pytest.raises(ClockError):
        s.set((T0 + 60) * 1000, 10, True, w.m)
    assert w.t == T0


def test_can_set_false_off_linux_with_the_plain_reason(hub, world):
    s = setter(hub, world, platform="win32")
    assert s.can_set() == (False, "Windows and macOS set their own clock.")
    assert s.status()["setup_command"] is None
    with pytest.raises(ClockError) as e:
        s.set((T0 + 60) * 1000, 10, True, world.m)
    assert e.value.code == "not_supported"
    assert world.calls == []                           # nothing run at all on Windows or macOS
    assert setter(hub, world, platform="darwin").can_set()[0] is False


def test_can_set_is_cached_briefly(hub, world):
    s = setter(hub, world)
    s.can_set()
    s.can_set()
    assert len([c for c in world.calls if c[:3] == ["sudo", "-n", "-l"]]) == 1
    world.m += cs.CAN_SET_CACHE_S + 1
    s.can_set()
    assert len([c for c in world.calls if c[:3] == ["sudo", "-n", "-l"]]) == 2


def test_status_fields(hub, world):
    st = setter(hub, world, helper_exists=False).status()
    assert set(st) == {"hub_epoch", "site_timezone", "system_timezone", "ntp_synchronized", "can_set",
                       "reason_if_not", "is_emulate", "setup_command"}
    assert st["can_set"] is False and st["reason_if_not"] == cs.NOT_SET_UP
    assert re.fullmatch(r"sudo bash [A-Za-z0-9_./-]+install-clock-helper\.sh", st["setup_command"])
    assert st["ntp_synchronized"] is False and st["system_timezone"] == "Europe/London"
    ok = setter(hub, world).status()
    assert ok["can_set"] and ok["setup_command"] is None and ok["hub_epoch"] == T0


def test_ntp_unknown_is_none(hub):
    w = World(ntp="weird")
    assert setter(hub, w).status()["ntp_synchronized"] is None
    assert cs.parse_timedatectl("NTPSynchronized=yes\nTimezone=UTC\n") == {"ntp_synchronized": True, "timezone": "UTC"}
    assert cs.parse_timedatectl("Timezone=Not a zone; rm -rf\n")["timezone"] == ""


# ------------------------------------------------------------------ no shell, fixed text, helper reply
def test_runs_an_argument_list_without_a_shell(hub, world):
    setter(hub, world).set((T0 + 60) * 1000, 10, True, world.m)
    argv = world.calls[-1]
    assert argv == ["sudo", "-n", "/usr/local/sbin/stagewatch-set-time", str(T0 + 60)]
    assert all(not any(ch in a for ch in " ;|&$`\n") for a in argv)
    src = Path(cs.__file__).read_text(encoding="utf-8")
    assert "shell=True" not in src and "os.system" not in src
    assert "shell=False" in src                         # the default runner says so


def test_default_runner_uses_no_shell(monkeypatch):
    seen = {}

    def fake(argv, **kw):
        seen.update(argv=argv, **kw)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(cs.subprocess, "run", fake)
    cs.default_runner(["echo", "x"])
    assert seen["shell"] is False and seen["argv"] == ["echo", "x"] and seen["timeout"] > 0
    assert seen["stdin"] == subprocess.DEVNULL


def test_helper_failure_gives_fixed_text_not_its_output(hub):
    w = World(helper_rc=1, helper_reply="ERROR secret-path /etc/shadow\n")
    with pytest.raises(ClockError) as e:
        setter(hub, w).set((T0 + 60) * 1000, 10, True, w.m)
    assert e.value.code == "failed" and "shadow" not in e.value.message and w.t == T0


@pytest.mark.parametrize("reply", ["", "OK 1761000060 rtc=maybe\n", "OK 1761000060 rtc=no\nextra\n", "whoami\n",
                                   "OK 12 rtc=no\n"])
def test_unexpected_helper_answers_are_refused(hub, reply):
    w = World(helper_reply=reply or "\n")
    with pytest.raises(ClockError) as e:
        setter(hub, w).set((T0 + 60) * 1000, 10, True, w.m)
    assert e.value.code == "bad_reply" and reply.strip()[:6] not in e.value.message or reply == ""


def test_parse_helper_line():
    assert cs.parse_helper_line("OK 1761000060 rtc=yes\n") == (1761000060, "yes")
    assert cs.parse_helper_line("ERROR no\n") is None
    assert cs.parse_helper_line("OK 1761000060 rtc=no\nOK 1761000060 rtc=no\n") is None


def test_every_error_message_is_fixed_plain_text():
    for code, msg in ClockError.MESSAGES.items():
        assert msg and "{" not in msg and "%" not in msg, code
        assert ClockError(code).message == msg


# ------------------------------------------------------------------ side effects
def test_marker_log_flush_and_step(hub, world, caplog):
    flushes = []
    real = hub.recorder.flush
    hub.recorder.flush = lambda: (flushes.append(world.t), real())[1]   # type: ignore[method-assign]
    steps = []
    hub.clock_stepped = lambda s: steps.append(s)                       # type: ignore[method-assign]
    s = setter(hub, world)
    with caplog.at_level(logging.INFO):
        s.set((T0 + 7200) * 1000, 20, True, world.m)
    assert flushes == [T0]                               # saved before the clock moved
    assert steps == [7200.0]
    labels = [m.label for m in hub.recorder.markers()]
    assert "Clock set from a browser (was -7200 s)" in labels
    log_text = "\n".join(r.getMessage() for r in caplog.records)
    assert "Clock set from a browser: changed by +7200 s" in log_text


def test_marker_for_a_clock_that_was_ahead(hub, world):
    setter(hub, world).set((T0 - 90) * 1000, 20, True, world.m)
    assert "Clock set from a browser (was +90 s)" in [m.label for m in hub.recorder.markers()]


def test_clock_step_keeps_readings_fresh_and_heartbeat_running(hub, world):
    hub.update_state("site.speed_of_sound", 340.0) if "site.speed_of_sound" in hub.entities else None
    ent = next(iter(hub.entities.values()))
    ent.updated = 1000.0
    hub.clock_stepped(-3600.0)
    assert ent.updated == -2600.0
    hub.clock_stepped(3600.0)
    assert ent.updated == 1000.0


def test_clock_step_resets_the_run_record_so_a_restart_is_not_misreported(tmp_path):
    import time as t
    h = Hub(tmp_path, emulate=False)
    run = h.recorder.run_id
    h.recorder._db.execute("UPDATE hub_runs SET started = ?, last_seen = ? WHERE id = ?", (t.time() + 7200, t.time() + 7200, run))
    h.recorder._db.commit()
    h.clock_stepped(-7200.0)                              # as if the clock had just been wound back two hours
    row = h.recorder._db.execute("SELECT started, last_seen FROM hub_runs WHERE id = ?", (run,)).fetchone()
    assert row[0] <= t.time() + 1 and row[1] <= t.time() + 1
    assert abs(h.recorder._last_heartbeat - t.time()) < 5
    h.recorder.close()


def test_monotonic_watchdogs_are_not_wall_clock_based():
    guard = (REPO / "src/stagewatch/core/loopguard.py").read_text(encoding="utf-8")
    assert "time.time()" not in guard                    # a step cannot look like a freeze
    launcher = (REPO / "src/stagewatch/launcher.py").read_text(encoding="utf-8")
    assert "time.monotonic()" in launcher


# ------------------------------------------------------------------ emulate: a dry run through the same path
def test_emulate_is_a_dry_run(tmp_path):
    h = Hub(tmp_path, emulate=True)
    w = World()
    s = ClockSetter(h, runner=w.runner, platform="win32", wall=w.wall, mono=w.mono)
    assert s.can_set() == (True, "")
    stepped = []
    h.clock_stepped = lambda x: stepped.append(x)        # type: ignore[method-assign]
    res = s.set((T0 + 3600) * 1000, 10, True, w.m)
    assert res["emulated"] and res["message"] == "Emulate: the clock was not changed."
    assert w.t == T0 and w.calls == [] and stepped == []
    assert not [m for m in h.recorder.markers() if "Clock set" in m.label]
    with pytest.raises(ClockError):                      # still rate limited and confirmed like the real thing
        s.set((T0 + 3600) * 1000, 10, True, w.m)
    h.recorder.close()


def test_emulate_still_needs_confirmation(tmp_path):
    h = Hub(tmp_path, emulate=True)
    w = World()
    with pytest.raises(ClockError) as e:
        ClockSetter(h, runner=w.runner, wall=w.wall, mono=w.mono).set((T0 + 3600) * 1000, 10, False, w.m)
    assert e.value.code == "needs_confirm"
    h.recorder.close()


# ------------------------------------------------------------------ the HTTP API
@pytest.fixture
def api(tmp_path):
    h = Hub(tmp_path, emulate=False)
    w = World(t=__import__("time").time())
    clock = ClockSetter(h, runner=w.runner, platform="linux", wall=w.wall, mono=w.mono, exists=lambda _p: True)
    with TestClient(create_app(h, clock=clock)) as c:
        c.world, c.clock, c.hub = w, clock, h
        assert c.post("/api/admin/setup", json={"pin": "1234"}).status_code == 200
        yield c


def body(c, offset=60, **kw):
    return {"epoch_ms": int((c.world.t + offset) * 1000), "rtt_ms": 20, "confirm_jump": True, **kw}


def test_api_needs_admin(api):
    api.post("/api/admin/logout")
    assert api.get("/api/admin/clock").status_code == 401
    assert api.get("/api/admin/clock/probe").status_code == 401
    assert api.post("/api/admin/clock/set", json=body(api)).status_code == 401
    assert api.world.calls == []


def test_api_post_refuses_cross_origin(api):
    r = api.post("/api/admin/clock/set", json=body(api), headers={"origin": "http://evil.example"})
    assert r.status_code == 403 and api.world.t == api.world.t
    assert not [c for c in api.world.calls if len(c) == 4 and c[2] != "-l"]


def test_api_status_and_probe(api):
    r = api.get("/api/admin/clock")
    assert r.status_code == 200 and r.json()["can_set"] is True and r.headers["cache-control"] == "no-store"
    p = api.get("/api/admin/clock/probe").json()
    assert set(p) == {"epoch_ms"} and isinstance(p["epoch_ms"], int)
    assert abs(p["epoch_ms"] / 1000 - api.world.t) < 1


def test_api_set_then_rate_limit(api):
    r = api.post("/api/admin/clock/set", json=body(api))
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["changed_by_s"] in (60, 61, 59)
    again = api.post("/api/admin/clock/set", json=body(api))
    assert again.status_code == 429 and again.json()["code"] == "rate_limited"


def test_api_jump_needs_confirm(api):
    r = api.post("/api/admin/clock/set", json=body(api, offset=7200, confirm_jump=False))
    assert r.status_code == 409 and r.json()["code"] == "needs_confirm"


@pytest.mark.parametrize("extra", [{"relative_s": 5}, {"tz": "UTC"}, {"cmd": "date"}, {"epoch": 1}])
def test_api_rejects_unknown_and_relative_fields(api, extra):
    r = api.post("/api/admin/clock/set", json={**body(api), **extra})
    assert r.status_code == 422 and api.world.t < T0 * 2
    assert not [c for c in api.world.calls if len(c) == 4 and c[2] != "-l"]


@pytest.mark.parametrize("bad", [{"epoch_ms": "1761000000000"}, {"epoch_ms": 1.7e12}, {"epoch_ms": True},
                                 {"rtt_ms": "5"}, {"rtt_ms": 1.5}, {"confirm_jump": "yes"}, {"epoch_ms": None}])
def test_api_rejects_wrong_types(api, bad):
    r = api.post("/api/admin/clock/set", json={**body(api), **bad})
    assert r.status_code == 422


def test_api_errors_never_echo_the_input(api):
    marker = "SUPERSECRETVALUE123"
    r = api.post("/api/admin/clock/set", json={"epoch_ms": marker, "rtt_ms": 5, "confirm_jump": False, "x": marker})
    assert r.status_code == 422 and marker not in r.text
    r = api.post("/api/admin/clock/set", json={"epoch_ms": 123456789, "rtt_ms": 20})
    assert r.status_code == 422 and "123456789" not in r.text and r.json()["code"] == "out_of_range"
    r = api.post("/api/admin/clock/set", json={"epoch_ms": int((api.world.t + 60) * 1000), "rtt_ms": 99999})
    assert "99999" not in r.text


@pytest.mark.parametrize("platform,reason", [("linux", cs.NOT_SET_UP), ("win32", cs.NOT_LINUX)])
def test_api_not_set_up_and_windows(tmp_path, platform, reason):
    h = Hub(tmp_path, emulate=False)
    w = World(sudo_ok=False)
    clock = ClockSetter(h, runner=w.runner, platform=platform, wall=w.wall, mono=w.mono, exists=lambda _p: True)
    with TestClient(create_app(h, clock=clock)) as c:
        c.post("/api/admin/setup", json={"pin": "1234"})
        st = c.get("/api/admin/clock").json()
        assert st["can_set"] is False and st["reason_if_not"] == reason
        r = c.post("/api/admin/clock/set", json={"epoch_ms": int((w.t + 60) * 1000), "rtt_ms": 5, "confirm_jump": True})
        assert r.status_code == 409 and w.t == T0
    assert not [x for x in w.calls if len(x) == 4 and x[2] != "-l"]


def test_api_emulate_dry_run(tmp_path):
    h = Hub(tmp_path, emulate=True)
    w = World(t=__import__("time").time())
    clock = ClockSetter(h, runner=w.runner, wall=w.wall, mono=w.mono)
    with TestClient(create_app(h, clock=clock)) as c:
        c.post("/api/admin/setup", json={"pin": "1234"})
        assert c.get("/api/admin/clock").json()["is_emulate"] is True
        r = c.post("/api/admin/clock/set", json={"epoch_ms": int((w.t + 90) * 1000), "rtt_ms": 5, "confirm_jump": True})
        assert r.status_code == 200 and r.json()["message"] == "Emulate: the clock was not changed."
    assert w.calls == []
    h.recorder.close()


# ------------------------------------------------------------------ the root helper script, under bash
def _bash():
    exe = shutil.which("bash")
    if sys.platform == "win32" and (exe is None or "system32" in exe.lower()):
        exe = next((p for p in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files (x86)\Git\bin\bash.exe")
                    if os.path.isfile(p)), None)       # Git for Windows, never the WSL launcher
    if exe is None:
        pytest.skip("bash is not installed")
    return exe


@pytest.fixture
def rig(tmp_path):
    """The helper with its PATH and /dev/rtc* swapped for a folder of fake date, hwclock and id."""
    bash = _bash()
    fake = tmp_path / "bin"
    fake.mkdir()
    log = tmp_path / "calls.log"

    def prog(name, body):
        p = fake / name
        p.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8", newline="\n")
        p.chmod(0o755)

    prog("date", f'echo "date $*" >> "{log.as_posix()}"')
    prog("hwclock", f'echo "hwclock $*" >> "{log.as_posix()}"; [ -f "{(tmp_path / "hwfail").as_posix()}" ] && exit 1; exit 0')
    prog("id", 'echo "${FAKE_UID:-0}"')
    src = (REPO / "deploy/pi/stagewatch-set-time").read_text(encoding="utf-8")
    fake_path = fake.as_posix()
    if re.match(r"^[A-Za-z]:/", fake_path):               # Git for Windows: a drive colon would split PATH
        fake_path = "/" + fake_path[0].lower() + fake_path[2:]
    src, n1 = re.subn(r"^PATH=.*$", lambda _m: f'PATH="{fake_path}"', src, flags=re.M)
    src, n2 = re.subn(r"^RTC_GLOB=.*$", f"RTC_GLOB='{(tmp_path / 'rtc*').as_posix()}'", src, flags=re.M)
    assert (n1, n2) == (1, 1)
    script = tmp_path / "helper.sh"
    script.write_text(src, encoding="utf-8", newline="\n")

    def run(*args, uid="0"):
        env = {**os.environ, "FAKE_UID": uid}
        r = subprocess.run([bash, script.as_posix(), *args], capture_output=True, text=True, env=env, timeout=60)
        calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        return r.returncode, r.stdout.strip(), calls

    run.dir = tmp_path
    return run


def test_helper_sets_the_clock_without_a_hardware_clock(rig):
    rc, out, calls = rig("1761000060")
    assert (rc, out) == (0, "OK 1761000060 rtc=no")
    assert calls == ["date -u -s @1761000060"]


def test_helper_writes_the_hardware_clock_when_there_is_one(rig):
    (rig.dir / "rtc0").write_text("")
    rc, out, calls = rig("1761000060")
    assert (rc, out) == (0, "OK 1761000060 rtc=yes")
    assert calls == ["date -u -s @1761000060", "hwclock --systohc"]


def test_helper_reports_a_failed_hardware_clock_but_keeps_the_time(rig):
    (rig.dir / "rtc0").write_text("")
    (rig.dir / "hwfail").write_text("")
    rc, out, calls = rig("1761000060")
    assert (rc, out) == (0, "OK 1761000060 rtc=failed")


@pytest.mark.parametrize("args", [(), ("1761000060", "1761000061"), ("abc",), ("1761000060;ls",), ("-1761000060",),
                                  ("+761000060",), ("17610000600",), ("176100006",), ("1735689599",),
                                  ("4102444800",), ("9999999999",), ("0x6900000",), ("1761000060 ",), ("",),
                                  ("$(id)",), ("1.761e9",)])
def test_helper_refuses_anything_but_one_integer_in_range(rig, args):
    rc, out, calls = rig(*args)
    assert rc == 1 and out.startswith("ERROR ") and calls == []


def test_helper_range_edges(rig):
    assert rig("1735689600")[1] == "OK 1735689600 rtc=no"
    assert rig("4102444799")[1] == "OK 4102444799 rtc=no"


def test_helper_octal_looking_numbers_are_decimal(rig):
    # 0000000999 would be invalid octal; it is below the range, refused cleanly, never mis-read
    rc, out, _ = rig("0000000999")
    assert rc == 1 and out.startswith("ERROR")


def test_helper_must_run_as_root(rig):
    rc, out, calls = rig("1761000060", uid="1000")
    assert rc == 1 and "root" in out and calls == []


def test_helper_and_installer_text():
    helper = (REPO / "deploy/pi/stagewatch-set-time").read_text(encoding="utf-8")
    assert "date -u -s" in helper and "timedatectl set-time" not in helper.replace("(timedatectl set-time refuses", "")
    assert "hwclock --systohc" in helper
    for name in ("stagewatch-set-time", "install-clock-helper.sh", "install.sh", "uninstall.sh"):
        data = (REPO / "deploy/pi" / name).read_bytes()
        assert b"\r" not in data, name
    inst = (REPO / "deploy/pi/install-clock-helper.sh").read_text(encoding="utf-8")
    assert "visudo -cf" in inst and inst.index("visudo -cf") < inst.index("mv -f")
    assert 'ALL=(root) NOPASSWD: $HELPER' in inst and "install -o root -g root -m 0755" in inst
    assert "--uninstall" in inst and "set -euo pipefail" in inst
    assert "install-clock-helper.sh" in (REPO / "deploy/pi/install.sh").read_text(encoding="utf-8")
    assert "--uninstall" in (REPO / "deploy/pi/uninstall.sh").read_text(encoding="utf-8")
    attrs = (REPO / ".gitattributes").read_text(encoding="utf-8")
    assert "deploy/pi/stagewatch-set-time text eol=lf" in attrs


@pytest.mark.parametrize("name", ["stagewatch-set-time", "install-clock-helper.sh", "install.sh", "uninstall.sh"])
def test_pi_scripts_parse(name):
    subprocess.run([_bash(), "-n", (REPO / "deploy/pi" / name).as_posix()], check=True, timeout=60)


def test_javascript_clock_helper():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    r = subprocess.run([node, str(Path(__file__).parent / "js" / "clock_test.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr


def test_admin_page_wiring():
    static = REPO / "src/stagewatch/web/static"
    html = (static / "admin.html").read_text(encoding="utf-8")
    assert html.index("clock.js") < html.index("admin.js")
    js = (static / "admin.js").read_text(encoding="utf-8")
    assert "clockCard" in js and "/api/admin/clock/set" in js and "SW.clock" in js
