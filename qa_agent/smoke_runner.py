"""Real-run smoke check: start the actual app on a fresh folder and use it like a visitor would.

Why: 17 of 17 acceptance tests once passed on a page that did not work. The tests drove the app
through a test client with their own setup, so they never saw three real defects: the stylesheet and
script were never served, the script had a syntax error, and the database table was created only by
the test setup. This check starts the app the way a person would, and looks.

Standard library only: the runner copies this one file into the sandbox container and runs it there
(no network except loopback), with the locked smoke requests in a JSON file. It also imports cleanly
on the host, where the unit tests drive `run_smoke` directly.

Steps, all decided here, none by the model:
1. find a FastAPI or Flask app in the app folder and start it (a failure to start is a failure);
2. GET / : 5xx is a failure; 404 is fine (an API-only app); an HTML page's local stylesheets,
   scripts and images must each be served (this catches "assets never served");
3. GET every plain GET endpoint the app's OpenAPI document lists (FastAPI): no 5xx;
4. send each locked smoke request (the QA agent's valid-input journey, e.g. POST /signup) and
   require an accepted status (this catches "table never created" on a fresh start).
JavaScript syntax is checked separately by the runner, on the host, with `node --check`.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

SKIP_DIRS = {"qa_tests", "tests", "test", "__pycache__", "venv", ".venv", "node_modules", "site-packages", ".git"}
APP_PATTERN = re.compile(r"^(\w+)\s*(?::[^=\n]+)?=\s*(FastAPI|Flask)\s*\(", re.M)
START_TIMEOUT = 30  # a gate must not fail because the machine is busy; the runner can raise it
REQUEST_TIMEOUT = 10
MAX_OPENAPI_GETS = 20
MAX_ASSETS = 30


def find_app(app_root: Path) -> Optional[dict]:
    """The first FastAPI/Flask app object defined in the app folder, shallowest file first."""
    files = sorted((p for p in app_root.rglob("*.py") if not SKIP_DIRS & set(p.relative_to(app_root).parts[:-1])),
                   key=lambda p: (len(p.parts), str(p)))
    for path in files:
        try:
            match = APP_PATTERN.search(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if match:
            return {"framework": match.group(2).lower(), "var": match.group(1), "module": path.stem, "cwd": path.parent}
    return None


def start_command(app: dict, port: int) -> list[str]:
    if app["framework"] == "fastapi":
        return [sys.executable, "-m", "uvicorn", f"{app['module']}:{app['var']}", "--host", "127.0.0.1",
                "--port", str(port)]
    return [sys.executable, "-c",
            f"import {app['module']} as m; m.{app['var']}.run(host='127.0.0.1', port={port})"]


class _LocalAssets(HTMLParser):
    """Collects stylesheet, script and image URLs that point at the app itself."""

    def __init__(self):
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        url = a.get("src") if tag in ("script", "img") else (a.get("href") if tag == "link" and
                                                              "stylesheet" in (a.get("rel") or "").lower() else None)
        if url:
            self.urls.append(url)


def local_assets(html: str, page_url: str) -> list[str]:
    parser = _LocalAssets()
    try:
        parser.feed(html)
    except Exception:
        pass
    base, found = urlparse(page_url), []
    for raw in parser.urls:
        url = urljoin(page_url, raw.strip())
        parsed = urlparse(url)
        if parsed.scheme in ("http", "https") and parsed.netloc == base.netloc and url not in found:
            found.append(url)
    return found[:MAX_ASSETS]


def _request(method: str, url: str, body: Optional[dict] = None) -> tuple[int, str, str]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data is not None else {})
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return resp.status, resp.headers.get("Content-Type", ""), resp.read(1_000_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type", "") if exc.headers else "", exc.read(2000).decode("utf-8", "replace")


def _short(text: str, n: int = 160) -> str:
    return " ".join(text.split())[:n]


def _last_lines(text: str, count: int = 3, width: int = 300) -> str:
    """The end of a log: for a traceback, the error line, which is the part that matters."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    joined = " | ".join(lines[-count:])
    return joined[-width:]


def _wait_for_port(port: int, proc: subprocess.Popen, timeout: float = START_TIMEOUT) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline and proc.poll() is None:
        with socket.socket() as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.2)
    return False


def run_smoke(app_root: Path, requests: list[dict], port: int, env: Optional[dict] = None,
              start_timeout: float = START_TIMEOUT) -> dict:
    """Run the check; returns {'outcome', 'steps': [{name, ok, detail}], 'note'}."""
    steps: list[dict] = []

    def step(name: str, ok: bool, detail: str = "") -> bool:
        steps.append({"name": name, "ok": ok, "detail": detail})
        return ok

    app = find_app(app_root)
    if not app:
        return {"outcome": "skipped", "steps": [], "note": "No FastAPI or Flask app found in the app folder."}

    log = tempfile.TemporaryFile()
    proc = subprocess.Popen(start_command(app, port), cwd=app["cwd"], stdout=log, stderr=subprocess.STDOUT,
                            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", **(env or {})})
    base = f"http://127.0.0.1:{port}"
    try:
        if not _wait_for_port(port, proc, start_timeout):
            log.seek(0)
            step("app starts", False, "did not start listening: " + _last_lines(log.read().decode("utf-8", "replace")))
            return {"outcome": "failed", "steps": steps, "note": ""}
        step("app starts", True, f"{app['framework']} app {app['module']}:{app['var']} is listening")

        status, ctype, text = _request("GET", base + "/")
        if status >= 500:
            step("GET /", False, f"returned {status}: {_short(text)}")
        elif status == 404:
            step("GET /", True, "404 (no page at /; treated as an API-only app)")
        elif status >= 400:
            step("GET /", False, f"returned {status}")
        else:
            step("GET /", True, f"{status}")
            if "html" in ctype.lower():
                for url in local_assets(text, base + "/"):
                    s, _, _ = _request("GET", url)
                    step(f"asset {urlparse(url).path}", 200 <= s < 300,
                         "served" if 200 <= s < 300 else f"the page links to it but it returned {s}")

        if app["framework"] == "fastapi":
            s, _, text = _request("GET", base + "/openapi.json")
            if s == 200:
                try:
                    paths = json.loads(text).get("paths", {})
                except ValueError:
                    paths = {}
                gets = [p for p, ops in paths.items() if "get" in ops and "{" not in p and p != "/"][:MAX_OPENAPI_GETS]
                for p in gets:
                    s, _, body = _request("GET", base + p)
                    step(f"GET {p} (listed by the app)", s < 500, f"{s}" if s < 500 else f"returned {s}: {_short(body)}")

        for r in requests:
            s, _, body = _request(r["method"], base + r["path"], r.get("body"))
            ok = s in r["expect"]
            step(f"{r['method']} {r['path']} (locked smoke request)", ok,
                 f"{s}" if ok else f"returned {s}, expected {r['expect']}: {_short(body)}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
    return {"outcome": "passed" if all(x["ok"] for x in steps) else "failed", "steps": steps, "note": ""}


def main(argv: list[str]) -> int:
    """Container entry point: smoke_runner.py CONFIG.json OUT.json (paths inside the container)."""
    config = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    result = run_smoke(Path(config["app_root"]), config.get("requests", []), config.get("port", 8765),
                       config.get("env"), config.get("start_timeout", START_TIMEOUT))
    Path(argv[2]).write_text(json.dumps(result), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
