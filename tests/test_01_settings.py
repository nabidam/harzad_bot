"""#1 configurations.settings — env-matrix loading and defaults.

The settings module reads os.environ at import time (after load_dotenv()),
so each scenario runs in a fresh interpreter. To keep the project's .env
out of the picture, the isolated run imports a copy of the package from a
scratch directory (load_dotenv resolves .env relative to the importing
module, not the cwd).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

PROJ = Path(__file__).resolve().parent.parent
PY = PROJ / ".venv" / "bin" / "python"

REQUIRED = [
    "TOKEN", "BOT_NAME", "SQLITE", "ADMINS",
    "SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "PROXY_LIST",
    "ADMIN_TELEGRAM_USER_ID", "INSTAGRAM_USER", "INSTAGRAM_PASSWORD",
    "DOWNLOAD_MP3_PATH", "DOWNLOAD_VIDEO_PATH", "HOSTNAME", "HOST_ROOT",
    "SCRIPT_DIR", "HF_TOKEN", "REPLICATE_API_TOKEN", "TOGETHER_API_KEY",
    "DB_URI", "YT_COOKIE_FILE", "YT_PROXY",
]

SCRATCH: Path | None = None


def _scratch() -> Path:
    """Copy configurations/settings.py into a scratch package (no .env up-tree)."""
    global SCRATCH
    if SCRATCH is None or not SCRATCH.exists():
        SCRATCH = Path(tempfile.mkdtemp(prefix="settest_", dir="/tmp/opencode"))
        pkg = SCRATCH / "configurations"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        shutil.copy(PROJ / "configurations" / "settings.py", pkg / "settings.py")
    return SCRATCH


def run_settings(code: str, env: dict, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(PY), "-c", code],
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(cwd), **env},
        cwd=cwd, capture_output=True, text=True, timeout=60,
    )


FULL_ENV = {k: "x" for k in REQUIRED} | {
    "ADMINS": "1,2, 3",
    "WEB_HOST": "10.0.0.9",
    "WEB_PORT": "9999",
    "MINIAPP_URL": "https://app.example/",
    "LLM_BASE_URL": "http://127.0.0.1:1234/v1",
    "LLM_API_KEY": "k",
    "LLM_MODEL": "m",
}

READ = (
    "import json, configurations.settings as s\n"
    "print(json.dumps({k: getattr(s, k) for k in ["
    "'TOKEN','NAME','ADMINS','WEB_HOST','WEB_PORT','MINIAPP_URL',"
    "'LLM_BASE_URL','LLM_API_KEY','LLM_MODEL','DB_URI','YT_PROXY',"
    "'WEBHOOK']}))\n"
)


def _load(env: dict) -> dict:
    r = run_settings(READ, env, _scratch())
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_full_env_parsed():
    d = _load(FULL_ENV)
    assert d["TOKEN"] == "x"
    assert d["NAME"] == "x"
    assert d["ADMINS"] == ["1", "2", " 3"]  # split(',') — no strip, as-is
    assert d["WEB_HOST"] == "10.0.0.9"
    assert d["WEB_PORT"] == 9999  # int()
    assert d["MINIAPP_URL"] == "https://app.example/"
    assert d["LLM_MODEL"] == "m"
    assert d["WEBHOOK"] is False


def test_llm_defaults_when_unset():
    env = {k: v for k, v in FULL_ENV.items() if not k.startswith("LLM_")}
    d = _load(env)
    assert d["LLM_BASE_URL"] == "http://127.0.0.1:40002/v1"
    assert d["LLM_API_KEY"] == "not-needed"
    assert d["LLM_MODEL"] == "gemma4"


def test_web_defaults_when_unset():
    env = {k: v for k, v in FULL_ENV.items()
           if k not in ("WEB_HOST", "WEB_PORT", "MINIAPP_URL")}
    d = _load(env)
    assert d["WEB_HOST"] == "127.0.0.1"
    assert d["WEB_PORT"] == 8377
    assert d["MINIAPP_URL"] == ""  # empty = mini app disabled


@pytest.mark.parametrize("missing", REQUIRED)
def test_missing_required_var_keyerrors(missing):
    env = {k: v for k, v in FULL_ENV.items() if k != missing}
    r = run_settings("import configurations.settings", env, _scratch())
    assert r.returncode != 0
    assert f"KeyError: '{missing}'" in r.stderr


def test_no_env_no_dotenv_fails():
    # No vars at all and no .env reachable from the scratch dir.
    r = run_settings("import configurations.settings", {}, _scratch())
    assert r.returncode != 0
    assert "KeyError: 'TOKEN'" in r.stderr


def test_project_import_uses_dotenv():
    # In-repo import (the normal path): .env supplies the values.
    code = (
        "import configurations.settings as s\n"
        "assert s.TOKEN and s.TOKEN != 'x'\n"
        "assert s.NAME == 'harzad_bot'\n"
        "print('ok')\n"
    )
    r = subprocess.run(
        [str(PY), "-c", code],
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(PROJ)},
        cwd=PROJ, capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "ok"
