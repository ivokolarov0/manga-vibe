"""Every web script must parse: one syntax error stops the whole UI from loading."""
import shutil
import subprocess
from pathlib import Path

import pytest

APP = Path(__file__).parent.parent / "app"
# the app's own scripts and the built-in plugins' (e.g. saved searches)
JS = sorted((APP / "web" / "static" / "js").glob("*.js")) + sorted((APP / "plugins" / "builtin").rglob("*.js"))


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
@pytest.mark.parametrize("path", JS, ids=lambda p: p.name)
def test_script_parses(path):
    r = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
