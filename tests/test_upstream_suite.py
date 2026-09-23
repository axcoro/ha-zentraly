"""Keep upstream regressions isolated from the fork's Home Assistant stubs."""
from pathlib import Path
import subprocess
import sys


def test_upstream_regressions():
    root = Path(__file__).resolve().parents[1]
    suite = root / "tests_upstream"
    assert suite.is_dir(), "The upstream regression suite must be present"
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", str(suite)],
        cwd=root, capture_output=True, text=True, timeout=120, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
