"""Free public URL for the edge server (media + OAuth callbacks).

* cloudflared quick tunnel:  cloudflared tunnel --url http://127.0.0.1:8788   (no account needed,
  random https://<words>.trycloudflare.com that changes every start)
* ngrok (free account, one static domain): ngrok http 8788 [--url your-name.ngrok-free.app]

Only the edge port is exposed. The UI/API stays on localhost.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time

import httpx

URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


class TunnelManager:
    def __init__(self, core):
        self.core = core
        self.proc: subprocess.Popen | None = None
        self.kind = ""
        self.url = ""
        self.error = ""
        self.log: list[str] = []

    def status(self) -> dict:
        running = bool(self.proc and self.proc.poll() is None)
        return {"running": running, "kind": self.kind, "url": self.url if running else "", "error": self.error,
                "cloudflared_installed": bool(shutil.which("cloudflared")), "ngrok_installed": bool(shutil.which("ngrok")),
                "public_base_url": self.core.public_base_url(), "edge_port": self.core.settings.public_port,
                "log": self.log[-15:]}

    def start(self, kind: str = "cloudflared", domain: str = "", wait: float = 25.0) -> dict:
        self.stop()
        self.error, self.url, self.kind, self.log = "", "", kind, []
        port = self.core.settings.public_port
        if kind == "cloudflared":
            exe = shutil.which("cloudflared")
            if not exe:
                self.error = "cloudflared is not installed. macOS: brew install cloudflared - Windows: winget install Cloudflare.cloudflared - Linux: see SETUP.md"
                return self.status()
            cmd = [exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"]
        elif kind == "ngrok":
            exe = shutil.which("ngrok")
            if not exe:
                self.error = "ngrok is not installed (https://ngrok.com/download) - then run: ngrok config add-authtoken <token>"
                return self.status()
            cmd = [exe, "http", str(port), "--log", "stdout"] + (["--url", domain] if domain else [])
        else:
            self.error = f"unknown tunnel '{kind}'"
            return self.status()
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        threading.Thread(target=self._read, daemon=True).start()
        deadline = time.time() + wait
        while time.time() < deadline and not self.url and self.proc.poll() is None:
            if kind == "ngrok":
                self._ngrok_api()
            time.sleep(0.5)
        if self.url:
            self.core.update_prefs({"public_base_url": self.url})
        elif not self.error:
            self.error = "Tunnel started but no public URL yet - check the log below."
        return self.status()

    def _read(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            self.log.append(line.rstrip()[:300])
            m = URL_RE.search(line)
            if m and not self.url:
                self.url = m.group(0)

    def _ngrok_api(self) -> None:
        try:
            r = httpx.get("http://127.0.0.1:4040/api/tunnels", timeout=2)
            for t in r.json().get("tunnels", []):
                if t.get("public_url", "").startswith("https://"):
                    self.url = t["public_url"]
        except Exception:
            pass

    def stop(self) -> dict:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            if self.url and self.core.prefs().get("public_base_url") == self.url:
                self.core.update_prefs({"public_base_url": ""})
        self.proc = None
        return self.status()
