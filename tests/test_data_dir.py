from pathlib import Path

import stagewatch.__main__ as cli

REPO = Path(__file__).resolve().parents[1]


def test_default_data_dir_is_outside_repo(monkeypatch):
    monkeypatch.delenv("STAGEWATCH_DATA", raising=False)
    d = cli.default_data_dir().resolve()
    assert REPO not in d.parents and d != REPO


def test_emulate_uses_separate_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("STAGEWATCH_DATA", str(tmp_path))
    assert cli.default_data_dir() == tmp_path
    assert cli.default_data_dir(emulate=True) == tmp_path / "emulate"
