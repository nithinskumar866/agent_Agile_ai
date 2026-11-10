"""The real-run smoke check: start the actual app on a fresh folder and use it.

Born from INC-5 (docs/incidents.md): 17 of 17 acceptance tests passed on a sign-up page that did not
work, because the tests never saw three real defects. The `test_inc5_*` tests rebuild those defects.
Most tests run the smoke runner on this machine against tiny real FastAPI apps; one runs the whole
thing in the Docker sandbox.
"""

import asyncio
import shutil
import socket
import sys
from pathlib import Path

import pytest

from dev_agent import tools as dev_tools
from pm_agent import tools as pm_tools
from qa_agent import logic, runner, smoke_runner
from qa_agent import tools as qa_tools
from shared.testsuite import LockedFile, SmokeRequest, SmokeResult, SmokeStep, TestSuite, load_suite
from tests.test_qa_agent import (ALL_PASS, CASES, GOOD_CODE, TEST_FILE, junit, needs_docker, use_fakes, write)
from tests.test_workspace import FakeContext, FakeFilesystem, ctx, say, ws  # noqa: F401 — fixtures

GOOD_APP = '''\
import sqlite3
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

HERE = Path(__file__).resolve().parent
DB = HERE / "signup.db"
app = FastAPI()
app.mount("/static", StaticFiles(directory=str(HERE.parent / "frontend")), name="static")


@app.on_event("startup")
def init_db():
    with sqlite3.connect(DB) as conn:
        conn.execute("create table if not exists emails (email text unique)")


@app.get("/", response_class=HTMLResponse)
def page():
    return ('<html><head><link rel="stylesheet" href="/static/style.css"></head>'
            '<body><script src="/static/app.js"></script></body></html>')


@app.post("/signup")
def signup(payload: dict):
    with sqlite3.connect(DB) as conn:
        conn.execute("insert or ignore into emails values (?)", (payload.get("email"),))
    return {"message": "Thank you"}
'''
GOOD_JS = 'document.addEventListener("DOMContentLoaded", () => { console.log("ready"); });\n'
# The real defect from the sign-up page: the handler's closing brace is missing.
BROKEN_JS = 'form.addEventListener("submit", async (event) => {\n    event.preventDefault();\n);\n'
SIGNUP = [{"method": "POST", "path": "/signup", "body": {"email": "smoke@example.com"}, "expect": [200]}]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_project(root: Path, app_src: str = GOOD_APP, js: str = GOOD_JS) -> Path:
    (root / "backend").mkdir(parents=True)
    (root / "frontend").mkdir()
    (root / "backend" / "main.py").write_text(app_src, encoding="utf-8")
    (root / "frontend" / "style.css").write_text("body { margin: 0 }\n", encoding="utf-8")
    (root / "frontend" / "app.js").write_text(js, encoding="utf-8")
    return root


def smoke(project: Path, requests=SIGNUP) -> dict:
    requests = [SmokeRequest.model_validate(r).model_dump() for r in requests]
    return smoke_runner.run_smoke(project / "backend", requests, free_port())


def failed(result: dict) -> list[str]:
    return [f"{s['name']}: {s['detail']}" for s in result["steps"] if not s["ok"]]


# ── the pieces ──────────────────────────────────────────────────────────────

def test_a_smoke_request_must_be_a_plain_path_and_status_list():
    assert SmokeRequest(path="/signup").expect == [200, 201, 204]
    for bad in ("signup", "//evil.example/x", "http://evil.example/x", "/has space"):
        with pytest.raises(ValueError):
            SmokeRequest(path=bad)
    with pytest.raises(ValueError):
        SmokeRequest(path="/x", expect=[])
    with pytest.raises(ValueError):
        SmokeRequest(path="/x", expect=[42])


def test_the_app_is_found_by_its_definition_and_test_folders_are_skipped(tmp_path):
    (tmp_path / "qa_tests").mkdir()
    (tmp_path / "qa_tests" / "conftest.py").write_text("app = FastAPI()\n", encoding="utf-8")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "server.py").write_text("from flask import Flask\napi: Flask = Flask(__name__)\n", encoding="utf-8")
    app = smoke_runner.find_app(tmp_path)
    assert (app["framework"], app["module"], app["var"]) == ("flask", "server", "api")
    assert app["cwd"] == tmp_path / "pkg"
    assert smoke_runner.find_app(tmp_path / "pkg" / "nothing") is None


def test_only_the_apps_own_stylesheets_scripts_and_images_are_fetched():
    html = ('<link rel="stylesheet" href="style.css"><link rel="icon" href="/favicon.ico">'
            '<script src="/static/app.js"></script><script src="https://cdn.example.com/x.js"></script>'
            '<img src="img/logo.png"><script>inline()</script>')
    urls = smoke_runner.local_assets(html, "http://127.0.0.1:8765/")
    assert urls == ["http://127.0.0.1:8765/style.css", "http://127.0.0.1:8765/static/app.js",
                    "http://127.0.0.1:8765/img/logo.png"]


# ── the real app, started for real ──────────────────────────────────────────

def test_a_working_app_passes(tmp_path):
    result = smoke(make_project(tmp_path))
    assert result["outcome"] == "passed", failed(result)
    names = [s["name"] for s in result["steps"]]
    assert {"app starts", "GET /", "asset /static/style.css", "asset /static/app.js"} <= set(names)
    assert any("locked smoke request" in n for n in names)


def test_inc5_the_three_defects_that_passed_17_tests_are_all_caught(tmp_path):
    """Rebuilds the real sign-up page defects: relative asset links the server never served, a
    JavaScript file with a missing brace, and a table created only by the test setup."""
    source = (GOOD_APP
              .replace('app.mount("/static", StaticFiles(directory=str(HERE.parent / "frontend")), name="static")\n', "")
              .replace('/static/style.css', 'style.css').replace('/static/app.js', 'app.js')
              .replace('@app.on_event("startup")\ndef init_db():', "def init_db():"))  # never called at startup
    project = make_project(tmp_path, source, BROKEN_JS)

    result = smoke(project)
    problems = failed(result)
    assert result["outcome"] == "failed"
    assert any("asset /style.css" in p and "404" in p for p in problems), problems  # 1. files never served
    assert any("locked smoke request" in p and "500" in p for p in problems), problems  # 3. table never created

    steps, note = runner.check_javascript(project)
    assert any(not s.ok and "frontend/app.js" in s.name for s in steps), steps  # 2. syntax error
    assert not note


def test_an_app_that_cannot_start_fails_with_the_reason(tmp_path):
    result = smoke(make_project(tmp_path, "import module_that_does_not_exist\n" + GOOD_APP))
    assert result["outcome"] == "failed"
    assert "did not start" in failed(result)[0] and "module_that_does_not_exist" in failed(result)[0]


def test_a_listed_get_endpoint_that_crashes_fails(tmp_path):
    crashing = GOOD_APP + '\n@app.get("/stats")\ndef stats():\n    raise RuntimeError("boom")\n'
    problems = failed(smoke(make_project(tmp_path, crashing)))
    assert any("GET /stats" in p and "500" in p for p in problems), problems


def test_an_api_only_app_with_no_page_at_the_root_is_fine(tmp_path):
    api_only = GOOD_APP.replace('@app.get("/", response_class=HTMLResponse)', '@app.get("/page", response_class=HTMLResponse)')
    result = smoke(make_project(tmp_path, api_only))
    assert result["outcome"] == "passed", failed(result)
    assert any(s["name"] == "GET /" and "API-only" in s["detail"] for s in result["steps"])


def test_a_locked_request_answered_with_the_wrong_status_fails_and_says_which(tmp_path):
    problems = failed(smoke(make_project(tmp_path), [{"method": "POST", "path": "/signup", "body": {}, "expect": [418]}]))
    assert any("returned 200, expected [418]" in p for p in problems), problems


def test_no_web_app_means_skipped_with_a_note_not_passed(tmp_path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    result = smoke_runner.run_smoke(tmp_path / "backend", [], free_port())
    assert result["outcome"] == "skipped" and "No FastAPI or Flask app" in result["note"]


# ── JavaScript syntax ───────────────────────────────────────────────────────

def test_javascript_that_parses_passes_and_a_broken_file_is_named(tmp_path):
    make_project(tmp_path)
    steps, note = runner.check_javascript(tmp_path)
    assert [s.ok for s in steps] == [True] and note == ""
    (tmp_path / "frontend" / "app.js").write_text(BROKEN_JS, encoding="utf-8")
    steps, _ = runner.check_javascript(tmp_path)
    assert steps[0].ok is False and "Unexpected token" in steps[0].detail


def test_a_project_without_javascript_has_no_js_steps(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert runner.check_javascript(tmp_path) == ([], "")


def test_javascript_that_could_not_be_checked_is_said_out_loud(tmp_path, monkeypatch):
    make_project(tmp_path)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    steps, note = runner.check_javascript(tmp_path)
    assert steps == [] and "NOT syntax-checked" in note


def test_node_modules_are_not_checked(tmp_path):
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "bad.js").write_text(BROKEN_JS, encoding="utf-8")
    assert runner.check_javascript(tmp_path) == ([], "")


# ── locking and running through the tools ───────────────────────────────────

def approved_web_project(ctx):
    """Like tests.test_qa_agent.approved_project, but the stack is FastAPI: a web app."""
    pm_tools.set_engagement("new_build", "finance", "Calculator service", "", tool_context=ctx)
    pm_tools.add_text_items("goals", ["Do sums"], "client", tool_context=ctx)
    pm_tools.add_text_items("success_metrics", ["Correct sums"], "client", tool_context=ctx)
    pm_tools.add_text_items("in_scope", ["Adding"], "client", tool_context=ctx)
    pm_tools.add_text_items("out_of_scope", ["Division"], "client", tool_context=ctx)
    pm_tools.add_requirement("functional", "Add amounts", "must", "client", tool_context=ctx,
                             acceptance_criteria=["2 + 3 gives 5", "Invalid amounts are rejected"])
    pm_tools.add_requirement("non_functional", "Runs offline", "must", "client", tool_context=ctx,
                             category="security", acceptance_criteria=["No network access is needed"])
    say(ctx, "I approve the PRD.")
    assert asyncio.run(pm_tools.publish_prd("v1", True, tool_context=ctx)).get("ok")
    dev_tools.recommend_tech_stack("backend", "FastAPI", "Simple", tool_context=ctx)
    dev_tools.add_project_scaffold([{"path": "backend/app/", "description": "App package"},
                                    {"path": "backend/requirements.txt", "description": "Deps"}], tool_context=ctx)
    assert asyncio.run(dev_tools.publish_architecture("v1", tool_context=ctx))["ok"]


def locked_web_project(ctx, smoke_requests=SIGNUP):
    approved_web_project(ctx)
    assert write(ctx, smoke_requests=smoke_requests)["ok"]
    assert qa_tools.lock_test_suite(tool_context=ctx)["ok"]
    res = asyncio.run(dev_tools.implement_scaffold(
        [{"path": "backend/app/__init__.py", "content": ""}, {"path": "backend/app/calc.py", "content": GOOD_CODE},
         {"path": "backend/requirements.txt", "content": ""}], tool_context=ctx))
    assert res.get("ok"), res


def fake_run(monkeypatch, smoke_result):
    monkeypatch.setattr(qa_tools, "run_suite", lambda project, suite: runner.RunOutcome(
        exit_code=0, results=ALL_PASS, output="fake", smoke=smoke_result))


def test_a_web_app_cannot_be_locked_without_a_smoke_request(ws, ctx, monkeypatch):
    use_fakes(monkeypatch)
    approved_web_project(ctx)
    assert write(ctx)["ok"]
    refused = qa_tools.lock_test_suite(tool_context=ctx)
    assert "smoke request" in refused["error"] and "POST /signup" in refused["error"]
    assert write(ctx, smoke_requests=SIGNUP)["ok"] and qa_tools.lock_test_suite(tool_context=ctx)["ok"]
    assert load_suite(ctx.state).smoke_requests[0].path == "/signup"


def test_other_stacks_do_not_need_smoke_requests(ws, ctx, monkeypatch):
    from tests.test_qa_agent import locked_project  # stack: plain Python
    use_fakes(monkeypatch)
    locked_project(ctx)
    assert load_suite(ctx.state).locked and load_suite(ctx.state).smoke_requests == []


def test_smoke_requests_are_validated_and_json_text_is_accepted(ws, ctx, monkeypatch):
    use_fakes(monkeypatch)
    approved_web_project(ctx)
    bad = write(ctx, smoke_requests=[{"method": "POST", "path": "http://evil.example/x"}])
    assert "Nothing was written" in bad["error"] and "smoke request" in " ".join(bad["problems"])
    ok = write(ctx, smoke_requests='[{"method": "GET", "path": "/health"}]')
    assert ok["ok"] and load_suite(ctx.state).smoke_requests[0].path == "/health"


def test_the_smoke_requests_are_locked_with_the_tests(ws, ctx, monkeypatch):
    use_fakes(monkeypatch)
    locked_web_project(ctx)
    again = write(ctx, smoke_requests=[{"method": "GET", "path": "/"}])  # the coder cannot loosen them
    assert "locked" in again["error"]
    assert load_suite(ctx.state).smoke_requests[0].path == "/signup"


def test_passing_tests_are_not_enough_if_the_smoke_check_fails(ws, ctx, monkeypatch):
    """INC-5 at the verdict level: every acceptance test passed, the real app did not work."""
    use_fakes(monkeypatch)
    locked_web_project(ctx)
    fake_run(monkeypatch, SmokeResult(outcome="failed", steps=[
        SmokeStep(name="app starts", ok=True), SmokeStep(name="POST /signup (locked smoke request)", ok=False,
                                                         detail="returned 500, expected [200]: no such table")]))
    res = asyncio.run(qa_tools.run_tests(tool_context=ctx))
    assert res["passed"] == 3 and res["failed"] == 0  # all acceptance tests green...
    assert res["outcome"] == "failed" and res["verdict"] == "failed"  # ...and still not a pass
    assert res["smoke"]["outcome"] == "failed" and "no such table" in res["smoke"]["failed_steps"][0]
    assert "fixes the code" in res["next"]


@pytest.mark.parametrize("outcome", ["passed", "skipped"])
def test_a_passing_or_not_applicable_smoke_check_lets_the_run_pass(ws, ctx, monkeypatch, outcome):
    use_fakes(monkeypatch)
    locked_web_project(ctx)
    fake_run(monkeypatch, SmokeResult(outcome=outcome, note="No FastAPI or Flask app found." if outcome == "skipped" else ""))
    res = asyncio.run(qa_tools.run_tests(tool_context=ctx))
    assert res["outcome"] == "passed" and res["verdict"] == "passed" and res["smoke"]["outcome"] == outcome


def test_the_report_shows_the_smoke_steps_and_what_it_does_not_cover(ws, ctx, monkeypatch):
    use_fakes(monkeypatch)
    locked_web_project(ctx)
    fake_run(monkeypatch, SmokeResult(outcome="failed", steps=[
        SmokeStep(name="asset /style.css", ok=False, detail="the page links to it but it returned 404")],
        note="JavaScript was NOT syntax-checked: node is not installed on this machine."))
    asyncio.run(qa_tools.run_tests(tool_context=ctx))
    text = logic.render_report_markdown(load_suite(ctx.state), __import__("shared.spec", fromlist=["x"]).load_spec(ctx.state), "calc")
    assert "## Real-run smoke check" in text and "FAILED" in text and "returned 404" in text
    assert "NOT syntax-checked" in text and "does not click" in text


# ── the whole thing in the Docker sandbox ───────────────────────────────────

@pytest.mark.docker
@needs_docker
def test_real_sandbox_smoke_passes_a_good_app_and_catches_inc5_style_defects(ws, monkeypatch):
    """The acceptance tests pass in both cases; only the smoke check tells them apart."""
    reqs = "fastapi\nuvicorn\n"
    suite = TestSuite(version=1, locked=True, app_dir="backend", smoke_requests=[SmokeRequest(**SIGNUP[0])],
                      files=[LockedFile(path="qa_tests/test_ok.py", sha256="x", content="def test_ok():\n    assert True\n")])

    good = make_project(ws / "good")
    (good / "backend" / "requirements.txt").write_text(reqs, encoding="utf-8")
    outcome = runner.run_suite(good, suite)
    assert outcome.exit_code == 0 and outcome.smoke.outcome == "passed", [s for s in outcome.smoke.steps if not s.ok]
    assert any(s.name == "asset /static/app.js" and s.ok for s in outcome.smoke.steps)

    broken_src = GOOD_APP.replace('@app.on_event("startup")\ndef init_db():', "def init_db():")
    broken = make_project(ws / "broken", broken_src, BROKEN_JS)
    (broken / "backend" / "requirements.txt").write_text(reqs, encoding="utf-8")
    outcome = runner.run_suite(broken, suite)
    assert outcome.exit_code == 0  # the acceptance tests are green...
    assert outcome.smoke.outcome == "failed"  # ...and the real run is not
    bad = [s.name for s in outcome.smoke.steps if not s.ok]
    assert "POST /signup (locked smoke request)" in bad and any("app.js" in n for n in bad), bad
