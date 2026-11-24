"""Environment pins shared by tests/conftest.py and scripts/ci_schema.py.

A few tests assert environment invariants (assets under SCRIPT_DIR, an
existing cookie file, existing download roots). apply() pins those vars to
paths that exist in ANY environment — the checkout for assets, tmp dirs
for the rest. It must run BEFORE the first `import configurations.settings`
(load_dotenv never overrides vars that are already set, and neither does
setdefault, so a real .env on the dev box always wins).
"""
import os
import tempfile
from pathlib import Path


def apply(project_root: Path) -> None:
    os.environ.setdefault("SCRIPT_DIR", str(project_root))

    root = Path(tempfile.mkdtemp(prefix="harzad_ci_"))
    (root / "downloads" / "mp3s").mkdir(parents=True)
    (root / "downloads" / "videos").mkdir(parents=True)
    os.environ.setdefault("DOWNLOAD_MP3_PATH", str(root / "downloads" / "mp3s"))
    os.environ.setdefault("DOWNLOAD_VIDEO_PATH", str(root / "downloads" / "videos"))
    os.environ.setdefault("HOST_ROOT", str(root / "downloads"))

    cookie = root / "cookies.txt"
    cookie.write_text("# Netscape HTTP Cookie File\n")
    os.environ.setdefault("YT_COOKIE_FILE", str(cookie))
