"""Company memory: the SQLite seed, the read-only Toolbox config, and the PM wiring.

Tests marked `toolbox` start the real binary from .tools/ (skipped when it is absent).
"""

import asyncio
import re
import socket
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

import pytest
import yaml

from pm_agent import company_memory
from pm_agent.agent import root_agent
from pm_agent.prompt import ROLE

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import seed_company_db  # noqa: E402

TOOLS_YAML = ROOT / "toolbox" / "tools.yaml"
BINARY = ROOT / ".tools" / "toolbox.exe"


def documents():
    return [d for d in yaml.safe_load_all(TOOLS_YAML.read_text(encoding="utf-8")) if d]


# --- the seed -----------------------------------------------------------------------------

def test_seed_builds_expected_rows(tmp_path):
    path = seed_company_db.build(tmp_path / "company.db")
    with closing(sqlite3.connect(path)) as conn, conn:
        assert conn.execute("select count(*) from projects").fetchone()[0] == 3
        assert conn.execute("select count(*) from lessons").fetchone()[0] >= 4
        origins = {r[0] for r in conn.execute("select data_origin from projects")}
    assert origins == {"delivered", "sample"}


def test_seed_is_idempotent_and_reset_rebuilds(tmp_path):
    path = seed_company_db.build(tmp_path / "company.db")
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("delete from lessons")
    seed_company_db.build(path)  # exists, no reset: untouched
    with closing(sqlite3.connect(path)) as conn, conn:
        assert conn.execute("select count(*) from lessons").fetchone()[0] == 0
    seed_company_db.build(path, reset=True)
    with closing(sqlite3.connect(path)) as conn, conn:
        assert conn.execute("select count(*) from lessons").fetchone()[0] > 0


def test_origin_must_be_delivered_or_sample(tmp_path):
    path = seed_company_db.build(tmp_path / "company.db")
    with closing(sqlite3.connect(path)) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute("insert into projects values (9,'x','x','x','x','x',1,1,'x','made-up')")


# --- the Toolbox config -------------------------------------------------------------------

def test_config_exposes_only_named_select_tools():
    tools = [d for d in documents() if d["kind"] == "tool"]
    assert {t["name"] for t in tools} == {"find_past_projects", "get_past_project", "find_lessons"}
    for tool in tools:
        assert tool["type"] == "sqlite-sql"  # never sqlite-execute-sql (free-form SQL)
        assert re.match(r"\s*SELECT\b", tool["statement"], re.I), tool["name"]
        assert not re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|PRAGMA)\b",
                             tool["statement"], re.I), tool["name"]
        assert "templateParameters" not in tool  # template params would allow SQL injection


def test_config_opens_the_database_read_only():
    source = next(d for d in documents() if d["kind"] == "source")
    assert source["type"] == "sqlite"
    assert "mode=ro" in source["database"]


def test_toolset_lists_exactly_the_defined_tools():
    docs = documents()
    defined = {d["name"] for d in docs if d["kind"] == "tool"}
    toolset = next(d for d in docs if d["kind"] == "toolset")
    assert toolset["name"] == company_memory.TOOLSET_NAME
    assert set(toolset["tools"]) == defined


# --- the PM wiring ------------------------------------------------------------------------

def test_memory_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("TOOLBOX_URL", "")
    assert company_memory.company_memory_toolset() is None


def test_pm_has_the_memory_toolset_and_prompt_explains_it():
    assert any(isinstance(t, company_memory.OptionalToolset) for t in root_agent.tools)
    for name in ("find_past_projects", "get_past_project", "find_lessons"):
        assert name in ROLE
    assert "never the client's requirements" in ROLE.replace("\n", " ")


def test_a_down_server_means_no_tools_not_a_crash(monkeypatch, caplog):
    monkeypatch.setenv("TOOLBOX_URL", "http://127.0.0.1:1")  # nothing listens here
    toolset = company_memory.company_memory_toolset()
    assert asyncio.run(toolset.get_tools()) == []
    assert asyncio.run(toolset.get_tools()) == []
    assert asyncio.run(toolset.close()) is None
    assert sum("unavailable" in r.message for r in caplog.records) == 1  # warned once


# --- the real server ----------------------------------------------------------------------

needs_toolbox = pytest.mark.skipif(not BINARY.exists(), reason="run scripts/get_toolbox.py first")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def toolbox_url(tmp_path):
    db = seed_company_db.build(tmp_path / "company.db")
    config = tmp_path / "tools.yaml"
    config.write_text(TOOLS_YAML.read_text(encoding="utf-8")
                      .replace("file:data/company.db?mode=ro", f"file:{db.as_posix()}?mode=ro"),
                      encoding="utf-8")
    port = free_port()
    proc = subprocess.Popen([str(BINARY), "--config", str(config), "--port", str(port),
                             "--disable-version-check"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
                break
            except OSError:
                time.sleep(0.3)
        else:
            pytest.fail("Toolbox did not start")
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.kill()
        proc.wait(timeout=10)


@pytest.mark.toolbox
@needs_toolbox
def test_real_toolbox_serves_three_lookups(toolbox_url, monkeypatch):
    monkeypatch.setenv("TOOLBOX_URL", toolbox_url)
    toolset = company_memory.company_memory_toolset()

    async def run():
        tools = {t.name: t for t in await toolset.get_tools()}
        found = await tools["find_past_projects"].run_async(args={"keyword": "sign-up"}, tool_context=None)
        lesson = await tools["find_lessons"].run_async(args={"topic": "static"}, tool_context=None)
        await toolset.close()
        return set(tools), found, lesson

    names, found, lesson = asyncio.run(run())
    assert names == {"find_past_projects", "get_past_project", "find_lessons"}
    assert "Email sign-up page" in found
    assert "static" in lesson.lower()


@pytest.mark.toolbox
@needs_toolbox
def test_real_toolbox_refuses_writes_to_the_database(tmp_path):
    db = seed_company_db.build(tmp_path / "company.db")
    config = tmp_path / "probe.yaml"
    config.write_text(
        f"kind: source\nname: s\ntype: sqlite\ndatabase: \"file:{db.as_posix()}?mode=ro\"\n---\n"
        "kind: tool\nname: probe\ntype: sqlite-sql\nsource: s\ndescription: probe\n"
        "statement: INSERT INTO lessons (project_id, topic, lesson) VALUES (1,'x','x')\n",
        encoding="utf-8")
    result = subprocess.run([str(BINARY), "invoke", "probe", "{}", "--config", str(config)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode != 0 and "readonly" in (result.stdout + result.stderr).lower()
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("select count(*) from lessons where topic='x'").fetchone()[0] == 0
