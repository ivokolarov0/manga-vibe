"""Every web script must parse: one syntax error stops the whole UI from loading."""
import shutil
import subprocess
from pathlib import Path

import pytest

JS = sorted((Path(__file__).parent.parent / "app" / "web" / "static" / "js").glob("*.js"))


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
@pytest.mark.parametrize("path", JS, ids=lambda p: p.name)
def test_script_parses(path):
    r = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
