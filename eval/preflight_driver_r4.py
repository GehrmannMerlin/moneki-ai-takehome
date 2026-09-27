"""R4 preflight driver: start the preflight fake LLM, restart our service against it, then evaluate.

Same non-interactive pattern as `preflight_driver.py` (R3): `llm_gateway.py preflight`
starts a fake model, prints three env vars, then waits for you to restart the service.
The `ready_hook` automates the restart so the whole P1-P14 run is reproducible.
R4 keeps its own output dir so it never clobbers the R3 evidence.
"""

import json
import os
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "eval"))

import llm_gateway as lg  # noqa: E402

STARTER = os.path.join(ROOT, "starter")
PY = os.path.join(STARTER, ".venv", "Scripts", "python.exe")
SERVICE_URL = "http://127.0.0.1:8000"
OUT_DIR = os.path.join(ROOT, "eval", "_r4_preflight")

STATE = {"proc": None}
LOG = open(os.path.join(ROOT, "eval", "_r4_preflight_service.log"), "wb")


def kill_port(port=8000):
    try:
        proc = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except Exception:
        return
    out = proc.stdout or ""
    pids = set()
    for line in out.splitlines():
        if (":%d" % port) in line and "LISTENING" in line:
            parts = line.split()
            if parts and parts[-1].isdigit():
                pids.add(parts[-1])
    for pid in pids:
        subprocess.run(
            ["taskkill", "/F", "/PID", pid],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )


def wait_health(timeout=60.0):
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(SERVICE_URL + "/api/health", timeout=5) as r:
                body = json.loads(r.read().decode("utf-8"))
            if body.get("llm_mode") == "live":
                return body
            last = "llm_mode=%s" % body.get("llm_mode")
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
        time.sleep(0.5)
    raise SystemExit("service never became live: %s" % last)


def ready_hook(env):
    print("[driver] restarting service with injected env: %s" % env, flush=True)
    if STATE["proc"] is not None:
        STATE["proc"].kill()
    kill_port(8000)
    time.sleep(1.0)
    e = dict(os.environ)
    e.update({k: v for k, v in env.items() if not k.startswith("__")})
    STATE["proc"] = subprocess.Popen(
        [PY, "-m", "uvicorn", "kbqa.server:app", "--host", "127.0.0.1", "--port", "8000"],
        cwd=STARTER,
        env=e,
        stdout=LOG,
        stderr=subprocess.STDOUT,
    )
    body = wait_health(120.0)
    print("[driver] service live: %s" % json.dumps(body, ensure_ascii=False)[:200], flush=True)


def main():
    result = lg.run_preflight(
        service_url=SERVICE_URL,
        port=18801,
        no_wait=True,
        out_dir=OUT_DIR,
        ready_hook=ready_hook,
    )
    print("PREFLIGHT_PASSED=%s" % result.passed, flush=True)
    if STATE["proc"] is not None:
        STATE["proc"].kill()
    kill_port(8000)
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
