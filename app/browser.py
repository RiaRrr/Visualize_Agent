"""Render generated HTML through a disposable headless Chrome profile."""
from __future__ import annotations

import asyncio
import base64
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

import websocket


class ChromeSession:
    def __init__(self, connection):
        self.connection = connection
        self.next_id = 0
        self.errors: list[str] = []

    def call(self, method: str, params: dict | None = None) -> dict:
        self.next_id += 1
        request_id = self.next_id
        self.connection.send(json.dumps({"id": request_id, "method": method, "params": params or {}}))
        while True:
            message = json.loads(self.connection.recv())
            if message.get("method") == "Runtime.exceptionThrown":
                details = message.get("params", {}).get("exceptionDetails", {})
                self.errors.append(details.get("exception", {}).get("description") or details.get("text", "脚本错误"))
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(f"Chrome {method}: {message['error'].get('message', 'unknown error')}")
                return message.get("result", {})


def _render_sync(html: str) -> bytes:
    chrome = next((path for name in ("google-chrome", "chromium", "chromium-browser") if (path := shutil.which(name))), None)
    if not chrome:
        raise RuntimeError("找不到 Chrome/Chromium")
    with tempfile.TemporaryDirectory(prefix="visualize_chrome_", ignore_cleanup_errors=True) as temp:
        profile = Path(temp) / "profile"
        process = subprocess.Popen(
            [chrome, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
             "--disable-background-networking", "--disable-extensions",
             "--host-resolver-rules=MAP * ~NOTFOUND", "--remote-allow-origins=*",
             "--remote-debugging-port=0", f"--user-data-dir={profile}", "about:blank"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            port_file = profile / "DevToolsActivePort"
            deadline = time.monotonic() + 15
            while not port_file.exists():
                if process.poll() is not None:
                    raise RuntimeError("Chrome 启动失败")
                if time.monotonic() > deadline:
                    raise TimeoutError("等待 Chrome 启动超时")
                time.sleep(.05)
            port = port_file.read_text().splitlines()[0]
            with urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as response:
                pages = json.load(response)
            target = next(page for page in pages if page.get("type") == "page")
            connection = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=20, suppress_origin=True)
            try:
                session = ChromeSession(connection)
                session.call("Page.enable")
                session.call("Runtime.enable")
                session.call("Network.enable")
                session.call("Network.setBlockedURLs", {"urls": ["http://*", "https://*", "file://*"]})
                session.call("Emulation.setDeviceMetricsOverride", {"width": 1280, "height": 800, "deviceScaleFactor": 1, "mobile": False})
                encoded = base64.b64encode(html.encode("utf-8")).decode("ascii")
                session.call("Page.navigate", {"url": f"data:text/html;base64,{encoded}"})
                deadline = time.monotonic() + 15
                while True:
                    state = session.call("Runtime.evaluate", {"expression": "document.readyState", "returnByValue": True})
                    if state.get("result", {}).get("value") == "complete":
                        break
                    if time.monotonic() > deadline:
                        raise TimeoutError("页面加载超时")
                    time.sleep(.1)
                time.sleep(.6)
                metrics = session.call("Page.getLayoutMetrics")
                size = metrics.get("cssContentSize") or metrics.get("contentSize") or {}
                if size.get("width", 0) > 2000 or size.get("height", 0) > 5000:
                    raise ValueError("页面尺寸过大，无法完整审核")
                width = max(1280, int(size.get("width", 1280)))
                height = max(800, int(size.get("height", 800)))
                shot = session.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True,
                                                               "clip": {"x": 0, "y": 0, "width": width, "height": height, "scale": 1}})
                screenshot = base64.b64decode(shot["data"])
                if session.errors:
                    raise ValueError("页面脚本运行错误：" + session.errors[0][:200])
                return screenshot
            finally:
                connection.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


async def render_html(html: str) -> bytes:
    return await asyncio.to_thread(_render_sync, html)
