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


@pytest.fixture
def render_with_rnote(tmp_path):
    """Render every page of a .rnote file to PNG with the real Rnote engine.

    Returns a list of RGB Pillow images (one per page), or None when rnote-cli or
    Pillow is not available. `rnote-cli test` only deserializes a file; exporting
    also renders every stroke, so broken image data fails here.
    """

    def render(path):
        exe = rnote_cli()
        if not exe:
            return None
        try:
            from PIL import Image
        except ImportError:
            return None
        out = tmp_path / ("pages_" + os.path.basename(str(path)))
        out.mkdir()
        # scale factor 1: one pixel per document unit, so pixel positions are document positions
        r = subprocess.run([exe, "export", "--on-conflict", "overwrite", "doc-pages", "-o", str(out), "-f", "png",
                            "--bitmap-scalefactor", "1", str(path)], capture_output=True, text=True)
        assert r.returncode == 0, (r.stdout + r.stderr)[-2000:]
        pages = sorted(out.iterdir(), key=lambda p: int("".join(ch for ch in p.stem if ch.isdigit()) or 0))
        assert pages, "rnote-cli exported no pages"
        return [Image.open(p).convert("RGB") for p in pages]

    return render
