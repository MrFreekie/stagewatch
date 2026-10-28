"""RME unit settings in config.yaml: additive (no schema bump), validated, capped at eight, and a damaged
section resets only itself. Addresses and serials are placeholders.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from stagewatch.core.config import RME_MAX_UNITS, Config, ConfigStore, RmeDeviceConfig
from stagewatch.version import CONFIG_SCHEMA_VERSION

FIX = Path(__file__).parent / "fixtures"


def unit(i: int, **kw) -> dict:
    return {"id": f"unit_{i}", "host": f"192.168.1.{10 + i}", **kw}


def write_and_load(tmp_path, mutate):
    path = tmp_path / "config.yaml"
    store = ConfigStore(path)
    store.config = Config()
    store.config.site.name = "Kept venue"
    store.save()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    mutate(raw)
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    again = ConfigStore(path)
    return again.load(), again


def test_default_is_no_units_and_no_schema_bump():
    assert Config().rme_devices == []
    assert RME_MAX_UNITS == 8
    d = RmeDeviceConfig(id="U", host="192.168.1.9")
    assert (d.port, d.name, d.area, d.serial, d.record_input_changes) == (80, "", "", "", False)
    assert CONFIG_SCHEMA_VERSION == 2                      # no bump: only an additive key


@pytest.mark.parametrize("name", ["v1", "v2"])
def test_files_from_before_this_feature_load_unchanged(name):
    cfg = ConfigStore(FIX / name / "config.yaml").load()
    assert cfg.rme_devices == []


def test_validation_of_one_unit():
    ok = RmeDeviceConfig(id="Stage L pre!", host=" 192.168.1.9 ", port=8080, name="  A​ name \n", area="x" * 80, serial="SN 1/2<>")
    assert ok.id == "stage_l_pre" and ok.host == "192.168.1.9" and ok.port == 8080
    assert ok.name == "A name" and ok.area == "x" * 40 and ok.serial == "SN12"
    assert len(RmeDeviceConfig(id="a" * 90, host="10.0.0.1").id) == 40
    for bad in ({"id": "", "host": "10.0.0.1"}, {"id": "u", "host": "12mic.local"}, {"id": "u", "host": ""},
                {"id": "u", "host": "10.0.0.1", "port": 0}, {"id": "u", "host": "10.0.0.1", "port": 65536},
                {"id": "u", "host": "10.0.0.1", "port": True}, {"id": "u", "host": "10.0.0.1", "port": "x"},
                {"id": 5, "host": "10.0.0.1"}, {"host": "10.0.0.1"}):
        with pytest.raises(ValidationError):
            RmeDeviceConfig.model_validate(bad)
    assert RmeDeviceConfig(id="u", host="fd00::5").host == "fd00::5"


def test_unknown_keys_in_a_unit_are_ignored():
    assert RmeDeviceConfig.model_validate({"id": "u", "host": "10.0.0.1", "future_option": 1}).id == "u"


def test_a_list_keeps_valid_units_and_drops_duplicates_and_surplus(caplog):
    caplog.set_level(logging.WARNING)
    rows = [unit(i) for i in range(10)] + [unit(0), {"id": "other", "host": "192.168.1.10"}]
    cfg = Config.model_validate({"rme_devices": rows})
    assert [d.id for d in cfg.rme_devices] == [f"unit_{i}" for i in range(8)]
    assert "surplus" in caplog.text or "duplicate" in caplog.text
    assert "192.168" not in caplog.text


def test_same_address_twice_keeps_the_first_only():
    cfg = Config.model_validate({"rme_devices": [unit(1), {"id": "again", "host": "192.168.1.11"}]})
    assert [d.id for d in cfg.rme_devices] == ["unit_1"]


def test_saved_units_survive_a_save_and_load(tmp_path):
    cfg = Config()
    cfg.rme_devices = [RmeDeviceConfig(id="stage_l", host="192.168.1.9", port=81, name="Stage L", area="Left", serial="SN-1")]
    path = tmp_path / "config.yaml"
    store = ConfigStore(path)
    store.config = cfg
    store.save()
    assert ConfigStore(path).load().rme_devices == cfg.rme_devices


def test_a_damaged_section_resets_only_itself(tmp_path, caplog):
    caplog.set_level(logging.WARNING)

    def mutate(raw):
        raw["rme_devices"] = [unit(1, serial="SN-LEAK-1"), {"id": "bad", "host": "not an address", "serial": "SN-LEAK-2"},
                              {"id": "bad2", "host": "192.168.1.30", "port": 99999}, "junk", 5, None,
                              unit(2, port="eighty")]

    loaded, store = write_and_load(tmp_path, mutate)
    assert not store.recovery_required and loaded.site.name == "Kept venue"
    assert [d.id for d in loaded.rme_devices] == ["unit_1"]
    assert "SN-LEAK" not in caplog.text and "192.168" not in caplog.text and "not an address" not in caplog.text


@pytest.mark.parametrize("junk", ["garbage", 5, {"id": "u"}, True])
def test_a_section_that_is_not_a_list_becomes_empty(tmp_path, junk):
    loaded, store = write_and_load(tmp_path, lambda raw: raw.__setitem__("rme_devices", junk))
    assert loaded.rme_devices == [] and loaded.site.name == "Kept venue" and not store.recovery_required


def test_salvage_of_a_file_damaged_elsewhere_keeps_the_valid_units(tmp_path):
    def mutate(raw):
        raw["rme_devices"] = [unit(1), {"id": "bad", "host": "nope"}, unit(2)]
        raw["thresholds"] = "this is not a list"

    loaded, _ = write_and_load(tmp_path, mutate)
    assert [d.id for d in loaded.rme_devices] == ["unit_1", "unit_2"] and loaded.site.name == "Kept venue"


def test_an_older_build_ignores_the_list_and_forgets_it_on_its_next_save(tmp_path):
    """Documented downgrade behaviour: no schema bump, so the older build loads the file, ignores rme_devices and
    drops it when it saves. Everything else stays."""
    path = tmp_path / "config.yaml"
    store = ConfigStore(path)
    store.config = Config()
    store.config.site.name = "Kept venue"
    store.config.rme_devices = [RmeDeviceConfig(id="u", host="192.168.1.9")]
    store.save()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "rme_devices" in raw
    older = {k: v for k, v in raw.items() if k != "rme_devices"}                     # what the older build writes back
    path.write_text(yaml.safe_dump(older), encoding="utf-8")
    again = ConfigStore(path).load()
    assert again.rme_devices == [] and again.site.name == "Kept venue"
