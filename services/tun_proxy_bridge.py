"""Local HTTP proxy bridge for macOS TUN-based VPN clients."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time


class TunProxyBridge:
    def __init__(self) -> None:
        self.process: subprocess.Popen | None = None
        self.url = ""
        self._managed_env: dict[str, str | None] = {}

    @staticmethod
    def _enabled() -> bool:
        value = str(os.environ.get("ACCOUNT_MANAGER_TUN_PROXY") or "auto").strip().lower()
        if value in {"0", "false", "no", "off", "disabled"}:
            return False
        return sys.platform == "darwin" or value in {"1", "true", "yes", "on", "enabled"}

    @staticmethod
    def _port_ready(host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return True
        except OSError:
            return False

    def _install_proxy_env(self, url: str) -> None:
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            self._managed_env[key] = os.environ.get(key)
            os.environ[key] = url

    def start(self) -> str:
        if not self._enabled():
            return ""
        if self.process and self.process.poll() is None and self.url:
            return self.url

        host = str(os.environ.get("ACCOUNT_MANAGER_TUN_PROXY_HOST") or "127.0.0.1")
        port = int(os.environ.get("ACCOUNT_MANAGER_TUN_PROXY_PORT") or "18080")
        url = f"http://{host}:{port}"
        if self._port_ready(host, port):
            self.url = url
            self._install_proxy_env(url)
            print(f"[TunProxy] 复用本地代理: {url}")
            return url

        child_env = os.environ.copy()
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            child_env.pop(key, None)
        self.process = subprocess.Popen(
            [sys.executable, "-m", "pproxy", "-l", url],
            env=child_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 3
        while time.time() < deadline:
            if self.process.poll() is not None:
                break
            if self._port_ready(host, port):
                self.url = url
                self._install_proxy_env(url)
                print(f"[TunProxy] 已启动: {url}")
                return url
            time.sleep(0.05)

        self.stop()
        print(f"[TunProxy] 启动失败: {url}")
        return ""

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=1)
        self.process = None
        self.url = ""
        for key, previous in self._managed_env.items():
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous
        self._managed_env.clear()


tun_proxy_bridge = TunProxyBridge()
