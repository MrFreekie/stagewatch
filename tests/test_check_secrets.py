import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_secrets  # noqa: E402

CONFIG = "tests/fixtures/v1/config.yaml"


def test_release_fixtures_are_the_only_allowed_config_and_db_files():
    ok = [CONFIG, "tests/fixtures/v1/stagewatch.sqlite3"]
    assert check_secrets.check(ok, staged=False) == []  # the real fixtures, content-scanned
    for bad in ("config.yaml", "data/config.yaml", "tests/fixtures/config.yaml",
                "tests/fixtures/v1/sub/config.yaml", "tests/fixtures/vx/stagewatch.sqlite3",
                "tests/fixtures/v123/config.yaml", "tests/fixtures/v1x/config.yaml",
                "tests/fixtures/v1/stagewatch.sqlite3-wal", "tests/fixtures/v1/secret.key",
                "src/tests/fixtures/v1/config.yaml"):
        assert check_secrets.check([bad], staged=False), bad


@pytest.mark.parametrize("path", ["stagewatch.sqlite3.pre-v2.bak", "tests/fixtures/v1/config.yaml.bak",
                                  "notes.bak", "x/stagewatch.sqlite3.pre-v2.bak.tmp",
                                  "config.invalid.yaml", "data2/config.invalid-20260101-1.yml"])
def test_backup_and_quarantine_files_are_blocked(path):
    assert check_secrets.check([path], staged=False)


@pytest.mark.parametrize("extra", [
    "  pin_hash: pbkdf2_sha256$200000$AAAAAAAAAAAAAAAAAAAAAA==$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=\n",
    "  noise_psk: QkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkI=\n",
    "  password: someone-real-password\n",
])
def test_fixtures_are_still_scanned_for_other_secrets(monkeypatch, extra):
    real = (ROOT / CONFIG).read_text(encoding="utf-8")
    monkeypatch.setattr(check_secrets, "staged_content", lambda path, staged, binary_ok=False: real + extra)
    assert check_secrets.check([CONFIG], staged=False)
    monkeypatch.setattr(check_secrets, "staged_content", lambda path, staged, binary_ok=False: real)
    assert check_secrets.check([CONFIG], staged=False) == []
