"""Offline desktop inspection with explicit, saved browser actions and screenshots."""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from urllib.request import urlopen

import websocket
from app.browser import ChromeSession


def inspect(path: Path, output: Path, actions: list[dict]) -> dict:
    chrome = next((p for name in ("google-chrome", "chromium", "chromium-browser") if (p := shutil.which(name))), None)
    if not chrome:
        raise RuntimeError("Chrome not found")
    output.mkdir(parents=True, exist_ok=True)
    report = {"html": str(path), "viewport": {"width": 1280, "height": 900}, "states": []}
    with tempfile.TemporaryDirectory(prefix="inspect1019_") as temp:
        profile = Path(temp) / "profile"
        process = subprocess.Popen([chrome, "--headless=new", "--disable-gpu", "--no-first-run",
            "--disable-background-networking", "--disable-extensions", "--host-resolver-rules=MAP * ~NOTFOUND",
            "--remote-allow-origins=*", "--remote-debugging-port=0", f"--user-data-dir={profile}", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        connection = None
        try:
            port_file = profile / "DevToolsActivePort"
            deadline = time.monotonic() + 15
            while not port_file.exists():
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("Chrome startup failed")
                time.sleep(.05)
            port = port_file.read_text().splitlines()[0]
            with urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as response:
                page = next(p for p in json.load(response) if p.get("type") == "page")
            connection = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=30, suppress_origin=True)
            session = ChromeSession(connection)
            for method in ("Page.enable", "Runtime.enable", "Network.enable"):
                session.call(method)
            session.call("Network.setBlockedURLs", {"urls": ["http://*", "https://*", "file://*"]})
            session.call("Emulation.setDeviceMetricsOverride", {"width":1280,"height":900,"deviceScaleFactor":1,"mobile":False})
            session.call("Page.navigate", {"url":"data:text/html;base64," + base64.b64encode(path.read_bytes()).decode()})
            deadline = time.monotonic() + 15
            while session.call("Runtime.evaluate", {"expression":"document.readyState","returnByValue":True}).get("result",{}).get("value") != "complete":
                if time.monotonic() > deadline:
                    raise RuntimeError("Page load timeout")
                time.sleep(.05)
            for i, action in enumerate([{"name":"initial", "js":"true"}] + actions):
                result = session.call("Runtime.evaluate", {"expression": action["js"], "returnByValue":True,"awaitPromise":True})
                time.sleep(.18)
                state = session.call("Runtime.evaluate", {"expression":"""(() => ({
                  text: document.body.innerText,
                  lessonState: window.__lessonDebug || null,
                  controls:[...document.querySelectorAll('input,button,select')].map((e,i)=>({index:i,id:e.id,
                    tag:e.tagName,type:e.type,text:e.innerText,value:e.value,min:e.min,max:e.max,
                    visible:!!(e.offsetWidth||e.offsetHeight||e.getClientRects().length),disabled:e.disabled})),
                  invalidText:/\\b(?:NaN|Infinity|undefined)\\b/.test(document.body.innerText)
                }))()""", "returnByValue":True}).get("result",{}).get("value",{})
                metrics = session.call("Page.getLayoutMetrics").get("cssContentSize", {})
                shot = session.call("Page.captureScreenshot", {"format":"png", "captureBeyondViewport":True,
                    "clip":{"x":0,"y":0,"width":1280,"height":min(6000,max(900,metrics.get("height",900))),"scale":1}})
                filename = f"{i:02d}_{action['name']}.png"
                (output / filename).write_bytes(base64.b64decode(shot["data"]))
                report["states"].append({"name":action["name"],"action":action["js"],"result":result,
                                         "screenshot":filename, **state})
            report["runtime_errors"] = session.errors
        finally:
            if connection:
                connection.close()
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()
    (output / "inspection.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return report


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("html",type=Path)
    parser.add_argument("--actions",type=Path)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    report=inspect(args.html,args.output,json.loads(args.actions.read_text()) if args.actions else [])
    print(json.dumps({"states":len(report['states']),"errors":report['runtime_errors'],"output":str(args.output)},ensure_ascii=False))
