"""Schedule auto-markers: a marker (source "schedule") at doors, soundcheck and act start/end as
each planned moment arrives. Driven by a fake clock (check(now)); covers per-row overrides,
catch-up after a restart, no duplicates across restarts and re-saves, and edits."""

import pytest
from fastapi.testclient import TestClient

from stagewatch.core import schedule as sched
from stagewatch.core.hub import Hub
from stagewatch.web.server import create_app

T0 = 1_790_000_000.0  # any fixed instant; the fake clock runs from here
MIN = 60.0


def row(kind, title, start, end=None, stage="", marker=None, id=None):
    return {"id": id, "stage": stage, "kind": kind, "title": title, "planned_start": T0 + start * MIN,
            "planned_end": None if end is None else T0 + end * MIN, "setlist": "", "marker": marker}


DAY = [
    row("crew_call", "Crew call", -300),
    row("soundcheck", "Headliner", -240, -180),
    row("soundcheck", "Support soundcheck", -170, -150),
    row("doors", "Doors open", 0),
    row("act", "Support: The Harbour Lights", 30, 70),
    row("changeover", "Changeover", 70, 85),
    row("act", "Headliner: Kestrel Road", 85),          # no end of its own: cut off by the curfew
    row("act", "DJ set", 200),                           # after the curfew, no end: start only
    row("curfew", "Curfew", 180),
]


def labels(hub, show_id=None):
    return [(m.label, m.ts) for m in hub.recorder.markers(show_id) if m.source == "schedule"]


def load(hub, rows):
    hub.schedule.replace(None, [dict(r) for r in rows])
    return hub.schedule.items()


@pytest.fixture
def hub(tmp_path):
    h = Hub(tmp_path)
    yield h
    if not h.recorder._closed:
        h.recorder.close()


# ------------------------------------------------------------------ pure
def test_moments_and_labels():
    items = [{**r, "id": i + 1} for i, r in enumerate(DAY)]
    got = [(m.item_id, m.edge, (m.ts - T0) / MIN, m.label) for m in sched.marker_moments(items)]
    assert got == [
        (1, "start", -300, "Crew call"),
        (2, "start", -240, "Soundcheck: Headliner"),
        (3, "start", -170, "Support soundcheck"),           # already says soundcheck
        (4, "start", 0, "Doors"),
        (5, "start", 30, "Support: The Harbour Lights on stage"),
        (5, "end", 70, "Support: The Harbour Lights off stage"),
        (6, "start", 70, "Changeover"),
        (7, "start", 85, "Headliner: Kestrel Road on stage"),
        (7, "end", 180, "Headliner: Kestrel Road off stage"),   # cut off by the curfew
        (9, "start", 180, "Curfew"),
        (8, "start", 200, "DJ set on stage"),                    # no end, no later curfew: no end marker
    ]
    on = {i["id"]: sched.marker_on(i) for i in items}
    assert on == {1: False, 2: True, 3: True, 4: True, 5: True, 6: False, 7: True, 8: True, 9: False}


def test_labels_with_stage_and_a_curfew_before_the_own_end():
    items = [{**row("act", "Band", 0, 120, stage="Main stage"), "id": 1},
             {**row("curfew", "Curfew", 90, stage="Main stage"), "id": 2},
             {**row("curfew", "Tent curfew", 30, stage="Tent"), "id": 3},
             {**row("soundcheck", "Line check / SOUND-CHECK", -60, stage="Main stage"), "id": 4},
             {**row("doors", "Gates", -30, stage="Main stage"), "id": 5}]
    got = {(m.item_id, m.edge): (m.label, (m.ts - T0) / MIN) for m in sched.marker_moments(items)}
    assert got[(1, "start")] == ("Band on stage (Main stage)", 0)
    assert got[(1, "end")] == ("Band off stage (Main stage)", 90)   # the Main stage curfew, not the tent's
    assert got[(4, "start")] == ("Line check / SOUND-CHECK (Main stage)", -60)
    assert got[(5, "start")] == ("Doors (Main stage)", -30)
    long = {**row("act", "x" * 120, 0, stage="Main stage"), "id": 9}
    assert len(sched.marker_moments([long])[0].label) == sched.MARKER_LABEL_MAX


def test_marker_on_overrides():
    assert sched.marker_on({"kind": "act", "marker": None}) is True
    assert sched.marker_on({"kind": "act", "marker": 0}) is False
    assert sched.marker_on({"kind": "curfew", "marker": 1}) is True
    assert sched.marker_on({"kind": "load_in"}) is False
    assert sched.MARKER_KINDS == ("soundcheck", "doors", "act")


# ------------------------------------------------------------------ the clock runs
def test_markers_arrive_with_their_moments(hub):
    load(hub, DAY)
    seen = []
    hub.bus.subscribe("marker", lambda _t, m: seen.append(m.label))
    assert [m.label for m in hub.check_schedule_markers(T0 - 301 * MIN)] == []
    hub.check_schedule_markers(T0 - 240 * MIN)
    assert labels(hub) == [("Soundcheck: Headliner", T0 - 240 * MIN)]   # crew call: off by default
    hub.check_schedule_markers(T0 - 1)
    hub.check_schedule_markers(T0)
    assert labels(hub)[-1] == ("Doors", T0)
    hub.check_schedule_markers(T0 + 30 * MIN + 5)  # five seconds late: placed at the planned time
    assert labels(hub)[-1] == ("Support: The Harbour Lights on stage", T0 + 30 * MIN)
    hub.check_schedule_markers(T0 + 6 * 3600)
    assert labels(hub) == [
        ("Soundcheck: Headliner", T0 - 240 * MIN), ("Support soundcheck", T0 - 170 * MIN), ("Doors", T0),
        ("Support: The Harbour Lights on stage", T0 + 30 * MIN),
        ("Support: The Harbour Lights off stage", T0 + 70 * MIN),
        ("Headliner: Kestrel Road on stage", T0 + 85 * MIN),
        ("Headliner: Kestrel Road off stage", T0 + 180 * MIN),
        ("DJ set on stage", T0 + 200 * MIN),
    ]
    assert seen == [label for label, _ in labels(hub)]  # each one went out to open screens
    assert hub.check_schedule_markers(T0 + 7 * 3600) == []


def test_act_without_end_or_curfew_gets_no_off_stage_marker(hub):
    load(hub, [row("act", "Open mic", 0), row("act", "Closing band", 60)])
    hub.check_schedule_markers(T0 + 86400)
    assert [label for label, _ in labels(hub)] == ["Open mic on stage", "Closing band on stage"]


def test_per_row_overrides(hub):
    load(hub, [row("act", "Secret set", 0, 30, marker=0), row("changeover", "Changeover", 30, 45, marker=1),
               row("curfew", "Curfew", 60), row("doors", "Doors", -30, marker=None),
               row("load_in", "Load in", -600, marker=1)])
    hub.check_schedule_markers(T0 + 86400)
    assert labels(hub) == [("Load in", T0 - 600 * MIN), ("Doors", T0 - 30 * MIN), ("Changeover", T0 + 30 * MIN)]


def test_schedule_wide_switch(hub):
    load(hub, DAY)
    hub.config.site.schedule_auto_markers = False
    hub.check_schedule_markers(T0 + 1)
    assert labels(hub) == []
    hub.config.site.schedule_auto_markers = True
    hub.check_schedule_markers(T0 + 31 * MIN)  # what passed while it was off is not filled in
    assert labels(hub) == [("Support: The Harbour Lights on stage", T0 + 30 * MIN)]


# ------------------------------------------------------------------ restarts and re-saves
def test_catch_up_after_a_restart_and_no_duplicates(tmp_path):
    hub = Hub(tmp_path)
    load(hub, DAY)
    hub.check_schedule_markers(T0 - 200 * MIN)  # running: the first soundcheck is marked
    hub.recorder.close()                        # Stagewatch is off through doors and the support act
    hub = Hub(tmp_path)
    hub.check_schedule_markers(T0 + 75 * MIN)   # start-up catch-up
    assert labels(hub) == [
        ("Soundcheck: Headliner", T0 - 240 * MIN), ("Support soundcheck", T0 - 170 * MIN), ("Doors", T0),
        ("Support: The Harbour Lights on stage", T0 + 30 * MIN),
        ("Support: The Harbour Lights off stage", T0 + 70 * MIN)]
    before = labels(hub)
    hub.recorder.close()
    for _ in range(2):  # restart twice more: nothing is added again
        hub = Hub(tmp_path)
        hub.check_schedule_markers(T0 + 75 * MIN)
        assert labels(hub) == before
        hub.recorder.close()


def test_start_up_runs_the_catch_up(tmp_path):
    import asyncio
    hub = Hub(tmp_path)
    past = [{**row("doors", "Doors", 0), "planned_start": 1_000_000.0}]  # long ago (a fixed instant)
    load(hub, past)
    hub.recorder.close()
    hub = Hub(tmp_path)

    async def run():
        await hub.start()
        await hub.stop()
    asyncio.run(run())
    from stagewatch.core.recorder import Recorder
    rec = Recorder(tmp_path / "stagewatch.sqlite3")
    assert [(m.label, m.ts, m.source) for m in rec.markers()] == [("Doors", 1_000_000.0, "schedule")]
    rec.close()


def test_resaving_the_schedule_never_duplicates(hub):
    items = load(hub, DAY)
    hub.check_schedule_markers(T0 + 75 * MIN)
    before = labels(hub)
    assert len(before) == 5
    hub.schedule.replace(None, [dict(i) for i in items])          # re-save: same ids
    hub.check_schedule_markers(T0 + 75 * MIN)
    assert labels(hub) == before
    hub.schedule.replace(None, [{**i, "id": None} for i in items])  # typed in again: new ids
    hub.check_schedule_markers(T0 + 75 * MIN)
    assert labels(hub) == before


def test_edit_before_the_moment_moves_the_marker(hub):
    items = load(hub, [row("doors", "Doors", 0), row("act", "Band", 30, 90)])
    hub.check_schedule_markers(T0 - 10 * MIN)
    doors = items[0]
    hub.schedule.replace(None, [{**doors, "planned_start": T0 + 15 * MIN}, dict(items[1])])  # doors delayed
    hub.check_schedule_markers(T0 + 1 * MIN)
    assert labels(hub) == []                                   # not at the old time
    hub.check_schedule_markers(T0 + 15 * MIN)
    assert labels(hub) == [("Doors", T0 + 15 * MIN)]
    # an edit after the moment: the marker already placed stays, and no second one appears
    items = hub.schedule.items()
    hub.schedule.replace(None, [{**i, "planned_start": i["planned_start"] - 5 * MIN} if i["kind"] == "doors"
                                else dict(i) for i in items])
    hub.check_schedule_markers(T0 + 20 * MIN)
    assert labels(hub) == [("Doors", T0 + 15 * MIN)]


def test_only_the_current_show_is_marked(hub):
    load(hub, DAY)
    day1 = hub.recorder.show_id
    hub.start_show("Day 2")                      # Day 1's schedule is never looked at again
    hub.check_schedule_markers(T0 + 86400)
    assert labels(hub, day1) == [] and labels(hub) == []
    load(hub, [row("doors", "Doors", 0)])
    hub.check_schedule_markers(T0 + 1)
    assert labels(hub) == [("Doors", T0)] and labels(hub, day1) == []


def test_deleted_schedule_marker_is_not_put_back(hub):
    load(hub, [row("doors", "Doors", 0)])
    (m,) = hub.check_schedule_markers(T0)
    hub.delete_marker(m.id)
    hub.check_schedule_markers(T0 + 60)
    assert labels(hub) == []


# ------------------------------------------------------------------ API
@pytest.fixture
def client(tmp_path):
    h = Hub(tmp_path)
    with TestClient(create_app(h, manage_hub=False)) as c:
        c.hub = h
        yield c
    h.recorder.close()


def _admin(client):
    assert client.post("/api/admin/setup", json={"pin": "1234"}).status_code == 200


def _put(client, items):
    s = client.get("/api/schedule").json()
    return client.put("/api/admin/schedule", json={"show_id": s["show_id"], "revision": s["revision"], "items": items})


def test_put_schedule_marker_setting(client):
    _admin(client)
    r = _put(client, [{"kind": "act", "title": "Band", "start": "20:00", "marker": False},
                      {"kind": "changeover", "title": "Changeover", "start": "21:00", "marker": True},
                      {"kind": "doors", "title": "Doors", "start": "19:00", "marker": None},
                      {"kind": "curfew", "title": "Curfew", "start": "23:00"}])
    assert r.status_code == 200, r.text
    got = {i["title"]: i["marker"] for i in r.json()["items"]}
    assert got == {"Band": False, "Changeover": True, "Doors": True, "Curfew": False}
    assert {i["title"]: i["marker"] for i in client.get("/api/schedule").json()["items"]} == got  # public shape
    stored = {i["title"]: i["marker"] for i in client.hub.schedule.items()}
    assert stored == {"Band": 0, "Changeover": 1, "Doors": None, "Curfew": None}
    # an item sent back without "marker" keeps what it has; "null" goes back to the default
    items = [{k: v for k, v in i.items() if k in ("id", "date", "kind", "title", "start", "end", "stage", "setlist")}
             for i in client.get("/api/schedule").json()["items"]]
    for i in items:
        if i["title"] == "Changeover":
            i["marker"] = None
    assert _put(client, items).status_code == 200
    stored = {i["title"]: i["marker"] for i in client.hub.schedule.items()}
    assert stored == {"Band": 0, "Changeover": None, "Doors": None, "Curfew": None}
    for bad in ("yes", 1, "true"):
        assert _put(client, [{"kind": "act", "title": "Band", "start": "20:00", "marker": bad}]).status_code == 422


def test_schedule_settings_switch(client):
    body = {"auto_markers": False}
    assert client.put("/api/admin/schedule/settings", json=body).status_code == 401
    _admin(client)
    assert client.put("/api/admin/schedule/settings", json=body, headers={"Origin": "http://evil.example"}).status_code == 403
    for bad in ({"auto_markers": "no"}, {}, {"auto_markers": False, "x": 1}):
        assert client.put("/api/admin/schedule/settings", json=bad).status_code == 422
    assert client.hub.config.site.schedule_auto_markers is True
    r = client.put("/api/admin/schedule/settings", json=body)
    assert r.status_code == 200 and r.json() == {"auto_markers": False}
    assert client.get("/api/admin/state").json()["config"]["site"]["schedule_auto_markers"] is False
    # the Site card saves without this field: it stays off
    site = {"name": "Field", "altitude_m": 10, "reference_distance_m": 30, "stale_after_s": 60,
            "smoothing_tau_s": 30, "outlier_reject": True, "timezone": "", "day_rollover": "06:00"}
    assert client.put("/api/admin/site", json=site).status_code == 200
    assert client.hub.config.site.schedule_auto_markers is False
    assert "schedule_auto_markers: false" in (client.hub.data_dir / "config.yaml").read_text(encoding="utf-8")
    assert client.get("/api/admin/state").json()["schedule_limits"]["marker_kinds"] == ["soundcheck", "doors", "act"]
