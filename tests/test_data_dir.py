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


def test_resolve_data_dir_emulate_always_uses_subfolder(monkeypatch, tmp_path):
    monkeypatch.setenv("STAGEWATCH_DATA", str(tmp_path / "env"))
    # explicit --data-dir (what the managed launcher passes) + --emulate -> <dir>/emulate
    assert cli.resolve_data_dir(tmp_path / "real", emulate=True) == (tmp_path / "real" / "emulate").resolve()
    assert cli.resolve_data_dir(tmp_path / "real", emulate=False) == (tmp_path / "real").resolve()
    # no --data-dir: default (or $STAGEWATCH_DATA) + emulate subfolder, same as before
    assert cli.resolve_data_dir(None, emulate=True) == (tmp_path / "env" / "emulate").resolve()
    assert cli.resolve_data_dir(None, emulate=False) == (tmp_path / "env").resolve()


def test_print_data_dir_with_explicit_dir_and_emulate(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("sys.argv", ["stagewatch", "--data-dir", str(tmp_path), "--emulate", "--print-data-dir"])
    cli.main()
    assert capsys.readouterr().out.strip() == str((tmp_path / "emulate").resolve())


def test_reset_pin_cli_emulate_targets_emulate_subfolder(tmp_path, capsys):
    (tmp_path / "emulate").mkdir()
    (tmp_path / "emulate" / "config.yaml").write_text("admin:\n  pin_hash: x\n", encoding="utf-8")
    cli.reset_admin_pin_cli(["--data-dir", str(tmp_path), "--emulate"])
    assert "pin_hash: x" not in (tmp_path / "emulate" / "config.yaml").read_text(encoding="utf-8")
    assert not (tmp_path / "config.yaml").exists()  # the real folder is untouched


def test_updater_accepts_emulate_subfolder_and_backs_up_real_dir(tmp_path):
    from stagewatch.core import updater as up
    from stagewatch import updater_common as uc

    class FakeHub:
        emulate = True
        data_dir = tmp_path / "data" / "emulate"

    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    uc.write_marker(repo / ".git" / uc.MARKER_NAME, {
        "repo_path": str(repo), "origin_url": "https://example.invalid/x.git", "git_path": str(tmp_path / "git"),
        "uv_path": str(tmp_path / "uv"), "data_dir": str(tmp_path / "data"), "emulate": True})
    u = up.Updater.detect(FakeHub(), env={}, repo=repo)
    assert u.managed and u.reason == "not_supervised"          # not "data_dir_mismatch"
    assert u.data_dir == tmp_path / "data"                      # backups act on the real folder


def test_backup_never_includes_emulate_subfolder(tmp_path):
    from stagewatch import backup as bk
    data, sd = tmp_path / "data", tmp_path / "state"
    (data / "emulate").mkdir(parents=True)
    (data / "emulate" / "config.yaml").write_text("simulated", encoding="utf-8")
    (data / "config.yaml").write_text("real", encoding="utf-8")
    m = bk.create_backup(data, sd)
    assert list(m["files"]) == ["config.yaml"]
    assert not (data / "backups" / m["id"] / "emulate").exists()
