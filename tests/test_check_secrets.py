import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_secrets  # noqa: E402


def test_release_fixtures_are_the_only_allowed_config_and_db_files():
    ok = ["tests/fixtures/v1/config.yaml", "tests/fixtures/v1/stagewatch.sqlite3"]
    assert check_secrets.check(ok, staged=False) == []
    for bad in ("config.yaml", "data/config.yaml", "tests/fixtures/config.yaml",
                "tests/fixtures/v1/sub/config.yaml", "tests/fixtures/vx/stagewatch.sqlite3",
                "tests/fixtures/v1/stagewatch.sqlite3-wal", "tests/fixtures/v1/secret.key",
                "src/tests/fixtures/v1/config.yaml"):
        assert check_secrets.check([bad], staged=False), bad
