import gzip
import json
import os
import shutil
import subprocess

import pytest


def load_rnote(path):
    with open(path, "rb") as f:
        return json.loads(gzip.decompress(f.read()))


def rnote_cli():
    return os.environ.get("RNOTE_CLI") or shutil.which("rnote-cli")


@pytest.fixture
def check_with_rnote():
    """Validate a .rnote file with the real Rnote engine when rnote-cli is available."""

    def check(path):
        exe = rnote_cli()
        if not exe:
            return None
        r = subprocess.run([exe, "test", str(path)], capture_output=True, text=True)
        assert r.returncode == 0 and "succeeded" in r.stdout, r.stdout + r.stderr
        return True

    return check
