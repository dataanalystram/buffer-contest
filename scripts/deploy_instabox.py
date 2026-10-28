#!/usr/bin/env python3
"""Deploy (or redeploy) HuhQueue to an InstaBox machine over the InstaBox API. Safe to re-run.

  INSTABOX_API_KEY=ibk_... python scripts/deploy_instabox.py [--machine huhqueue] [--first-run]

What it does:
  * uploads the committed HEAD (git archive) into /data/home/bufferopensource (no GitHub token on the machine)
  * makes a venv on the persistent disk and `pip install -e .`
  * --first-run: writes /data/home/.huhqueue/huhqueue.env (mode 600) with HUHQ_DEPLOY_SECRET_KEY (env or *_FILE)
    and, if given, HUHQ_DEPLOY_PASSWORD (otherwise the first visitor creates the password), and installs a systemd unit
  * publishes port 8787 (stable https URL) and restarts the service. No HUHQ_PUBLIC_BASE_URL is written: OAuth
    callbacks and media URLs follow the https URL the owner signs in on, so a new machine or URL needs no edits
Data (SQLite, media, saved app keys) lives in /data/home/.huhqueue/data and is never touched.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

API = "https://api.instabox.sh/v1"
CHUNK = 600_000  # base64 chars per exec call (the API takes at most 1 MiB of stdin)
HOME = "/data/home"
APP = f"{HOME}/bufferopensource"
CONF = f"{HOME}/.huhqueue"


def call(method: str, path: str, body: dict | None = None, timeout: int = 650) -> dict:
    req = urllib.request.Request(f"{API}{path}", method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-API-Key": os.environ["INSTABOX_API_KEY"], "Content-Type": "application/json"})
    for i in range(8):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 502, 503, 504) and i < 7:
                time.sleep(5 * (i + 1))
                continue
            raise SystemExit(f"InstaBox {method} {path}: {e.code} {e.read()[:300]!r}")
    raise SystemExit("unreachable")


def sh(mid: str, cmd: str, stdin: str | None = None, timeout: int = 600, check: bool = True) -> str:
    body = {"cmd": cmd, "timeoutSeconds": timeout}
    if stdin is not None:
        body["stdin"] = stdin
    d = call("POST", f"/machines/{mid}/exec", body)
    if check and d["exitCode"] != 0:
        raise SystemExit(f"command failed ({d['exitCode']}): {cmd[:120]}\n{d['stdout'][-1500:]}\n{d['stderr'][-1500:]}")
    return d["stdout"]


def find_machine(name: str) -> str:
    for m in call("GET", "/machines")["machines"]:
        if m["name"] == name or m["id"] == name:
            return m["id"]
    raise SystemExit(f"no InstaBox machine named {name}")


def secret(name: str, required: bool = True) -> str:
    v = os.environ.get(name) or ""
    f = os.environ.get(name + "_FILE")
    if not v and f:
        v = open(f).read().strip()
    if not v and required:
        raise SystemExit(f"--first-run needs {name} or {name}_FILE")
    return v


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--machine", default="huhqueue")
    ap.add_argument("--first-run", action="store_true")
    a = ap.parse_args()
    mid = find_machine(a.machine)
    port = call("POST", f"/machines/{mid}/ports", {"port": 8787, "name": "HuhQueue"})
    url = port["url"].rstrip("/")
    print("machine", mid, "url", url)

    tgz = subprocess.run(["git", "archive", "--format=tar.gz", "HEAD"], capture_output=True, check=True).stdout
    b64 = base64.b64encode(tgz).decode()
    sh(mid, f"rm -f /tmp/hq.b64 && mkdir -p {APP}")
    for i in range(0, len(b64), CHUNK):
        sh(mid, "cat >> /tmp/hq.b64", stdin=b64[i:i + CHUNK])
    sh(mid, f"cd {APP} && find . -mindepth 1 -maxdepth 1 ! -name '.venv' -exec rm -rf {{}} + && "
            f"base64 -d /tmp/hq.b64 | tar xz && rm /tmp/hq.b64 && echo ok")
    print(f"uploaded {len(tgz)} bytes")

    sh(mid, f"test -x {HOME}/venv/bin/python || python3 -m venv {HOME}/venv; "
            f"{HOME}/venv/bin/pip install -q --upgrade pip >/dev/null; cd {APP} && {HOME}/venv/bin/pip install -q -e . && echo installed")

    if a.first_run:
        env = "\n".join([
            f"HUHQ_DATA_DIR={CONF}/data", "HUHQ_HOST=0.0.0.0", "HUHQ_PORT=8787", "HUHQ_PUBLIC_PORT=8788",
            "HUHQ_COOKIE_SECURE=1", "HUHQ_TIMEZONE=Europe/Dublin",
            f"HUHQ_ADMIN_PASSWORD={secret('HUHQ_DEPLOY_PASSWORD', required=False)}",
            f"HUHQ_SECRET_KEY={secret('HUHQ_DEPLOY_SECRET_KEY')}",
        ]) + "\n"
        sh(mid, f"umask 077 && mkdir -p {CONF}/data && cat > {CONF}/huhqueue.env && chmod 600 {CONF}/huhqueue.env", stdin=env)
        unit = f"""[Unit]
Description=HuhQueue social scheduler
After=network-online.target

[Service]
User=user
WorkingDirectory={CONF}
EnvironmentFile={CONF}/huhqueue.env
ExecStart={HOME}/venv/bin/huhqueue serve
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
"""
        sh(mid, "sudo tee /etc/systemd/system/huhqueue.service >/dev/null && sudo systemctl daemon-reload && "
                "sudo systemctl enable huhqueue >/dev/null 2>&1; echo unit", stdin=unit)
    sh(mid, "sudo systemctl restart huhqueue && sleep 4 && curl -fsS http://127.0.0.1:8787/api/health")
    print("deployed:", url)


if __name__ == "__main__":
    sys.exit(main())
