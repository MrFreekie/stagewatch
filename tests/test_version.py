import re
import sqlite3
import sys
from pathlib import Path

import stagewatch
from stagewatch.core.config import ConfigStore
from stagewatch.core.recorder import Recorder
from stagewatch.version import CONFIG_SCHEMA_VERSION, DB_SCHEMA_VERSION, build_info

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import bump_version  # noqa: E402


def test_versions_in_sync():
    py = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M).group(1)
    assert py == stagewatch.__version__
    assert f"## [{py}]" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def test_build_info():
    info = build_info()
    assert info["version"] == stagewatch.__version__
    assert info["config_schema"] == CONFIG_SCHEMA_VERSION


def test_schema_versions_written(tmp_path):
    store = ConfigStore(tmp_path / "config.yaml")
    store.load()
    store.save()
    assert f"schema_version: {CONFIG_SCHEMA_VERSION}" in (tmp_path / "config.yaml").read_text()
    Recorder(tmp_path / "db.sqlite3").close()
    db = sqlite3.connect(tmp_path / "db.sqlite3")
    assert db.execute("PRAGMA user_version").fetchone()[0] == DB_SCHEMA_VERSION
    db.close()


def test_bump_logic():
    assert bump_version.next_version("1.2.3", "patch") == "1.2.4"
    assert bump_version.next_version("1.2.3", "minor") == "1.3.0"
    assert bump_version.next_version("1.2.3", "major") == "2.0.0"
    log = ("# Changelog\n\n## [Unreleased]\n\n### Fixed\n- thing\n\n## [1.2.3] - 2026-01-01\n- old\n\n"
           "[Unreleased]: https://github.com/o/r/compare/v1.2.3...HEAD\n"
           "[1.2.3]: https://github.com/o/r/releases/tag/v1.2.3\n")
    out = bump_version.update_changelog(log, "1.2.3", "1.2.4", "2026-02-02")
    assert "## [Unreleased]\n\n## [1.2.4] - 2026-02-02\n\n### Fixed\n- thing" in out
    assert "[Unreleased]: https://github.com/o/r/compare/v1.2.4...HEAD" in out
    assert "[1.2.4]: https://github.com/o/r/compare/v1.2.3...v1.2.4" in out
