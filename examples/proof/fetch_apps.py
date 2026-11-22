"""Download the open-source apps the proof runs use, at pinned versions, next to their contracts.

    python examples/proof/fetch_apps.py            # both
    python examples/proof/fetch_apps.py flaskr     # one

- OWASP Juice Shop 20.4.0 (MIT): the official prebuilt release for this OS and Node 24 -> juice-shop/app/
- flaskr, the Flask tutorial app (BSD-3-Clause), from pallets/flask at a pinned commit -> flaskr/app/

Nothing here is committed: the app folders are git-ignored. Needs Node.js 24 for Juice Shop and Flask (already a
Sentri dev dependency) for flaskr.
"""

from __future__ import annotations

import io
import platform
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
JUICE_VERSION = "20.4.0"
FLASK_COMMIT = "49943b5"  # pallets/flask, 2026-10-30


def _get(url: str) -> bytes:
    print(f"downloading {url}")
    with urllib.request.urlopen(url, timeout=600) as resp:  # noqa: S310 - fixed https URLs above
        return bytes(resp.read())


def juice_shop() -> None:
    target = HERE / "juice-shop" / "app"
    if (target / "build" / "app.js").exists():
        print(f"juice shop already in {target}")
        return
    system = {"Windows": "win32", "Darwin": "darwin", "Linux": "linux"}[platform.system()]
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
    ext = "zip" if system == "win32" else "tgz"
    name = f"juice-shop-{JUICE_VERSION}_node24_{system}_{arch}.{ext}"
    data = _get(f"https://github.com/juice-shop/juice-shop/releases/download/v{JUICE_VERSION}/{name}")
    with tempfile.TemporaryDirectory() as tmp:
        if ext == "zip":
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                archive.extractall(tmp)
        else:
            with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                archive.extractall(tmp, filter="data")
        (root,) = [p for p in Path(tmp).iterdir() if p.is_dir()]
        shutil.move(str(root), target)
    print(f"juice shop -> {target}")


def flaskr() -> None:
    target = HERE / "flaskr" / "app"
    if (target / "flaskr" / "__init__.py").exists():
        print(f"flaskr already in {target}")
        return
    data = _get(f"https://codeload.github.com/pallets/flask/tar.gz/{FLASK_COMMIT}")
    with tempfile.TemporaryDirectory() as tmp, tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(tmp, filter="data")
        (root,) = [p for p in Path(tmp).iterdir() if p.is_dir()]
        shutil.copytree(root / "examples" / "tutorial", target)
    print(f"flaskr -> {target}")


if __name__ == "__main__":
    wanted = set(sys.argv[1:]) or {"juice-shop", "flaskr"}
    if "juice-shop" in wanted:
        juice_shop()
    if "flaskr" in wanted:
        flaskr()
