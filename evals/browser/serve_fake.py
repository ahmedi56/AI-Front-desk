"""Start the app for the browser walkthrough with ONE command (works on Windows):

    python evals/browser/serve_fake.py

It makes a fresh throw-away database in the temp folder (never dev.db), adds the demo business, starts the FAKE AI
(no internet, $0) and serves the app on http://localhost:8811. Stop with Ctrl+C.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
work = Path(tempfile.mkdtemp(prefix="frontdesk-walk-"))

from cryptography.fernet import Fernet  # noqa: E402

os.environ.update({
    "APP_ENV": "development", "DATABASE_URL": f"sqlite:///{(work / 'walk.db').as_posix()}", "COOKIE_SECURE": "false",
    "PUBLIC_BASE_URL": "http://localhost:8811", "LLM_PROVIDER": "openai_compat", "LLM_FALLBACK_PROVIDER": "",
    "LLM_BASE_URL": "http://127.0.0.1:8765/v1", "LLM_API_KEY": "shared-test-key-0000", "MODEL_SIMPLE": "fake-model",
    "MODEL_HARD": "fake-model", "ENCRYPTION_KEY": Fernet.generate_key().decode(), "ALLOW_PAID_LLM": "false",
    "ANTHROPIC_API_KEY": "", "GLOBAL_KILL_SWITCH": "false",
})
os.chdir(work)  # so the laptop's own .env is not read
sys.path.insert(0, str(ROOT))
env = {**os.environ, "PYTHONPATH": str(ROOT)}
for cmd in (["-m", "app.migrate"], ["-m", "app.cli", "demo-business"]):
    subprocess.run([sys.executable, *cmd], check=True, env=env, cwd=work)

fake = subprocess.Popen([sys.executable, str(Path(__file__).resolve().parent / "fake_ai.py")], cwd=work)

import uvicorn  # noqa: E402

try:
    uvicorn.run("app.main:app", host="127.0.0.1", port=8811, log_level="warning")
finally:
    fake.terminate()
