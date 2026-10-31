"""'Sensor in the sun' advisory and battery voltage. Expected values are worked by hand in each docstring."""

import time
from datetime import datetime, timezone
from pathlib import Path

from stagewatch.core.hub import Hub
from stagewatch.core.model import Device, Entity, Kind
from stagewatch.core.sunwatch import SunWatch, is_daytime
from stagewatch.integrations.esphome.mapping import sensor_kind

STATIC = Path(__file__).resolve().parent.parent / "src" / "stagewatch" / "web" / "static"


def _run(sw, steps, series, daytime=True, start=1000.0):
    """Feed one reading per minute: series(i) -> {id: degC}; returns the flagged set after each."""
    out = []
    for i in range(steps + 1):
        out.append(sw.update(series(i), start + 60.0 * i, daytime))
    return out


def test_hot_against_two_others_is_flagged_after_ten_minutes_not_before():
    """C reads 26 against 20 and 21: median of all three 21.0, excess 5.0 >= 4.0 degC. The
    hold is 600 s, so with a reading every 60 s it is not flagged at 540 s and is flagged at 600 s."""
    sw = SunWatch()
    res = _run(sw, 10, lambda i: {"a": 20.0, "b": 21.0, "c": 26.0})
    assert res[9] == set()
    assert res[10] == {"c"}


def test_a_small_spread_is_not_flagged():
    """C at 23.5 against 20 and 21: excess 23.5 - 21.0 = 2.5 < 4.0 degC, however long it lasts."""
    sw = SunWatch()
    assert _run(sw, 30, lambda i: {"a": 20.0, "b": 21.0, "c": 23.5})[-1] == set()


def test_night_is_never_flagged():
    sw = SunWatch()
    assert _run(sw, 15, lambda i: {"a": 20.0, "b": 21.0, "c": 30.0}, daytime=False)[-1] == set()


def test_fast_rise_the_others_did_not_follow_is_flagged():
    """C climbs 0.5 degC/min (20 -> 24.5 at 540 s, rise 4.5 >= 3.0) while a and b stay at 20. The rise
    needs 90 % of the 600 s window (540 s) of history: not judged at 480 s (rise 4.0), flagged at 540 s,
    well before the 600 s 'well above the others' hold could finish."""
    sw = SunWatch()
    res = _run(sw, 10, lambda i: {"a": 20.0, "b": 20.0, "c": 20.0 + 0.5 * i})
    assert res[8] == set()
    assert res[9] == {"c"}


def test_everything_warming_together_is_weather_not_sun():
    """All three climb 0.5 degC/min (rise 5.0 each): the others' median rise 5.0 >= 5.0 / 2, so no flag."""
    sw = SunWatch()
    assert _run(sw, 12, lambda i: {k: 20.0 + 0.5 * i for k in "abc"})[-1] == set()


def test_a_lone_sensor_can_be_flagged_on_a_fast_rise_only():
    sw = SunWatch()
    assert _run(sw, 10, lambda i: {"a": 20.0 + 0.4 * i})[-1] == {"a"}   # rise 4.0 >= 3.0
    assert _run(SunWatch(), 30, lambda i: {"a": 35.0})[-1] == set()      # hot but steady: no comparison, no flag


def test_flag_clears_only_after_five_minutes_back_within_two_degrees():
    """Flagged at 26 against a 20 and b 21 (600 s). Then c drops to 22.0: median of all three is 21.0, so
    the excess is 1.0 < 2.0 (and the rise is 22.0 - 26.0 = -4.0). A single reading must not clear it:
    the quiet spell starts at 1660 s and the flag clears only once 300 s have passed, at 1960 s."""
    sw = SunWatch()
    _run(sw, 10, lambda i: {"a": 20.0, "b": 21.0, "c": 26.0})
    assert sw.flagged == {"c"}
    for t in range(1660, 1960, 60):
        sw.update({"a": 20.0, "b": 21.0, "c": 22.0}, float(t), True)
        assert sw.flagged == {"c"}, t
    sw.update({"a": 20.0, "b": 21.0, "c": 22.0}, 1960.0, True)
    assert sw.flagged == set()


def test_a_passing_cloud_for_one_reading_does_not_clear_the_flag():
    """A dip to 22.0 (excess 1.0) at 1660 s, then 26.0 again at 1720 s (excess 5.0 >= 2.0) restarts the
    five-minute spell, so a second dip from 1780 s is still flagged at 2000 s (220 s in) ..."""
    sw = SunWatch()
    _run(sw, 10, lambda i: {"a": 20.0, "b": 21.0, "c": 26.0})
    sw.update({"a": 20.0, "b": 21.0, "c": 22.0}, 1660.0, True)
    sw.update({"a": 20.0, "b": 21.0, "c": 26.0}, 1720.0, True)
    for t in range(1780, 2001, 60):
        sw.update({"a": 20.0, "b": 21.0, "c": 22.0}, float(t), True)
    assert sw.flagged == {"c"}


def test_a_cold_sensor_among_three_is_not_a_hot_one():
    """10, 20, 20: the median of all three is 20.0, so the cold one is at -10.0 and the others at 0.0.
    Nobody reaches +4.0, however long it lasts (comparing with the others' median would call a hot)."""
    sw = SunWatch()
    assert _run(sw, 30, lambda i: {"a": 10.0, "b": 20.0, "c": 20.0})[-1] == set()


def test_a_sensor_warming_from_below_is_not_sun():
    """c carried in from a cold van: 5.0 + 0.5 per minute, 9.5 at 540 s (rise 4.5 >= 3.0) while a and b
    sit at 12.0. It ends 12.0 - 9.5 = 2.5 BELOW the others, so it is not flagged."""
    sw = SunWatch()
    assert _run(sw, 10, lambda i: {"a": 12.0, "b": 12.0, "c": 5.0 + 0.5 * i})[-1] == set()


def test_a_fast_rise_must_end_two_degrees_above_the_others():
    """c from 9.5 by 0.5 per minute reaches 14.0 at 540 s (rise 4.5), exactly 14.0 - 12.0 = 2.0 above
    a and b: flagged. Ending at 13.5 (start 9.0, 1.5 above) is not."""
    assert _run(SunWatch(), 9, lambda i: {"a": 12.0, "b": 12.0, "c": 9.5 + 0.5 * i})[-1] == {"c"}
    assert _run(SunWatch(), 9, lambda i: {"a": 12.0, "b": 12.0, "c": 9.0 + 0.5 * i})[-1] == set()


def test_two_sensors_never_keep_one_out():
    """floor((2 - 1) / 2) = 0: with two sensors, half would go, so neither is flagged even on a fast rise."""
    sw = SunWatch()
    assert _run(sw, 10, lambda i: {"a": 20.0, "c": 20.0 + 0.5 * i})[-1] == set()


def test_the_hold_timer_only_runs_in_daytime():
    """20, 21, 26 (excess 5.0) for 20 minutes of night, then day from 2200 s. The timer starts at 2200 s,
    so at 2740 s (540 s) it is not flagged and at 2800 s (600 s) it is. Level alone at dawn is not enough."""
    sw = SunWatch()
    for i in range(20):
        sw.update({"a": 20.0, "b": 21.0, "c": 26.0}, 1000.0 + 60.0 * i, False)
    assert sw.flagged == set()
    for t in range(2200, 2741, 60):
        sw.update({"a": 20.0, "b": 21.0, "c": 26.0}, float(t), True)
    assert sw.flagged == set()
    sw.update({"a": 20.0, "b": 21.0, "c": 26.0}, 2800.0, True)
    assert sw.flagged == {"c"}


def test_the_daytime_window_is_0500_to_2130():
    assert not is_daytime(4, 59)
    assert is_daytime(5, 0)
    assert is_daytime(21, 29)
    assert not is_daytime(21, 30)


def test_flag_holds_between_two_and_four_degrees():
    """Hysteresis: at +3.0 (above the 2.0 clear level) a flagged sensor stays flagged."""
    sw = SunWatch()
    _run(sw, 10, lambda i: {"a": 20.0, "b": 21.0, "c": 26.0})
    sw.update({"a": 20.0, "b": 21.0, "c": 23.5}, 1660.0, True)
    assert sw.flagged == {"c"}


def _hub(tmp_path):
    h = Hub(tmp_path)
    h.config.site.smoothing_tau_s = 0
    h.config.site.outlier_reject = False
    h.config.site.timezone = "UTC"
    return h


def _temps(h, vals):
    for k, v in vals.items():
        if f"{k}.temperature" not in h.entities:
            h.register_device(Device(k, k, "test"))
            h.register_entity(Entity(f"{k}.temperature", k, "temperature", Kind.TEMPERATURE))
        h.update_state(f"{k}.temperature", v)


def test_flagged_sensor_is_kept_out_of_the_site_average_and_reported(tmp_path):
    """a 20, b 21, c 26 at 12:00 UTC for 10 minutes. With c out: (20 + 21) / 2 = 20.5, not
    (20 + 21 + 26) / 3 = 22.333. The public site block names c as kept out."""
    h = _hub(tmp_path)
    noon = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc).timestamp()
    for i in range(11):
        _temps(h, {"a": 20.0, "b": 21.0, "c": 26.0})
        for e in h.entities.values():
            if e.value is not None:
                e.updated = noon + 60 * i
        h.compute_site(noon + 60 * i)
        if i == 9:
            assert abs(h.entities["site.temperature"].value - 22.333333) < 1e-4
            assert "sun" not in h.site_meta
    assert abs(h.entities["site.temperature"].value - 20.5) < 1e-9
    assert h.site_meta["sun"] == [{"id": "c.temperature", "kept_out": True}]
    assert h.site_meta["sensors"]["temperature"] == 2
    assert h.average_info["temperature"]["sensors"]["c.temperature"]["state"] == "sun"


def test_only_sensor_is_flagged_but_still_used(tmp_path):
    """One sensor rising 0.4 degC/min for 10 min (20 -> 24): flagged, but with nothing else to
    average it stays in, so the site temperature is 24.0 and the note says it is still used."""
    h = _hub(tmp_path)
    noon = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc).timestamp()
    for i in range(11):
        _temps(h, {"a": 20.0 + 0.4 * i})
        h.entities["a.temperature"].updated = noon + 60 * i
        h.compute_site(noon + 60 * i)
    assert abs(h.entities["site.temperature"].value - 24.0) < 1e-9
    assert h.site_meta["sun"] == [{"id": "a.temperature", "kept_out": False}]


def test_battery_voltage_maps_to_voltage_kind_and_dashboard_shows_it():
    """An ESPHome gauge's 'voltage' device class (or a bare V unit) is a voltage reading, never a temperature."""
    assert sensor_kind("voltage", "V") == Kind.VOLTAGE
    assert sensor_kind("", "V") == Kind.VOLTAGE
    assert sensor_kind("battery", "%") == Kind.BATTERY
    js = (STATIC / "dashboard.js").read_text(encoding="utf-8")
    assert "getting low" in js and "toFixed(2)} V" in js
