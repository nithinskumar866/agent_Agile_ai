"""Packaging: the Dockerfile, .dockerignore, runtime requirements and compose file stay safe and consistent.

Static checks run in every test run. One test really builds and runs the image (about four minutes); it is
off unless AGENTCORP_TEST_DOCKER_BUILD=1.
"""

import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
IGNORE = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


def instructions(name: str) -> list[str]:
    return [ln.strip() for ln in DOCKERFILE.splitlines() if ln.strip().upper().startswith(name + " ")]


# ── the image ───────────────────────────────────────────────────────────────

def test_the_image_runs_as_an_unprivileged_user_and_the_user_is_set_before_the_server_starts():
    users = instructions("USER")
    assert users and users[-1] != "USER root" and "agent" in users[-1]
    assert DOCKERFILE.index("USER agent") < DOCKERFILE.index("CMD")


def test_the_server_listens_on_the_port_the_platform_gives_it():
    """Cloud Run sets $PORT; a hard-coded port would fail its startup check."""
    cmd = DOCKERFILE[DOCKERFILE.index("CMD"):]
    assert "$PORT" in cmd and "--host 0.0.0.0" in cmd
    assert "PORT=8080" in DOCKERFILE  # a default for running it by hand; the platform's value wins


def test_the_image_has_a_readiness_check_and_exposes_its_port():
    assert instructions("HEALTHCHECK") and "/list-apps" in DOCKERFILE
    assert any("EXPOSE" in line for line in DOCKERFILE.splitlines())


def test_no_secret_is_baked_into_the_image():
    lowered = DOCKERFILE.lower()
    assert not re.search(r"\b(api_key|secret|password|token)\s*=", lowered)
    assert not re.search(r"^(copy|add)\s.*\.env", lowered, re.M)
    assert not re.search(r"^arg\s+\w*(key|secret|token|password)", lowered, re.M)


def test_only_what_runs_is_copied_into_the_image():
    copied = {m.group(1) for m in re.finditer(r"^COPY\s+(?!--from)(\S+)\s", DOCKERFILE, re.M)}
    assert {"pm_agent", "dev_agent", "qa_agent", "shared", "knowledge", "requirements-runtime.txt"} <= copied
    assert not copied & {"tests", "docs", "evals", ".", ".git", "workspace", "data", "audit"}


def test_the_base_image_matches_the_one_the_test_sandbox_uses():
    from qa_agent import runner
    assert f"FROM {runner.IMAGE}" in DOCKERFILE  # one Python version everywhere


def test_the_node_version_matches_what_the_pinned_mcp_server_needs():
    """The filesystem MCP server is pinned because newer releases need Node 24; 22 must stay."""
    assert "node:22-slim" in DOCKERFILE


# ── .dockerignore ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("entry", [".env", "**/.env", "*.env", ".git", ".venv", "workspace", "data", "audit", "tests"])
def test_dockerignore_keeps_secrets_and_local_state_out(entry):
    assert entry in IGNORE


def test_dockerignore_does_not_hide_the_knowledge_documents():
    """A pattern like *.md would silently drop knowledge/*/*.md."""
    assert "*.md" not in IGNORE and "**/*.md" not in IGNORE and "knowledge" not in IGNORE


# ── runtime requirements ────────────────────────────────────────────────────

def _pin(path: str, package: str) -> str:
    text = (ROOT / path).read_text(encoding="utf-8")
    return re.search(rf"^{re.escape(package)}\[[^\]]*\]==([\d.]+)", text, re.M).group(1)


def test_the_runtime_requirements_pin_the_same_adk_version_as_development():
    assert _pin("requirements-runtime.txt", "google-adk") == _pin("requirements.txt", "google-adk")


def test_the_runtime_image_does_not_carry_development_tools():
    lines = (ROOT / "requirements-runtime.txt").read_text(encoding="utf-8").splitlines()
    runtime = "\n".join(ln for ln in lines if not ln.lstrip().startswith("#"))  # the comments may name pytest
    assert "pytest" not in runtime and "eval" not in re.search(r"google-adk\[([^\]]*)\]", runtime).group(1)
    for needed in ("litellm", "pyyaml", "pypdf", "pyflakes"):  # imported by the agents at run time
        assert needed in runtime


# ── compose ─────────────────────────────────────────────────────────────────

def test_compose_runs_the_agent_and_toolbox_and_wires_them_by_service_name():
    services = COMPOSE["services"]
    assert set(services) == {"agent", "toolbox"}
    assert services["agent"]["environment"]["TOOLBOX_URL"] == "http://toolbox:5000"
    assert "toolbox" in services["agent"]["depends_on"]


def test_compose_mounts_the_database_read_only_and_holds_no_secrets():
    services = COMPOSE["services"]
    assert "./data:/app/data:ro" in services["toolbox"]["volumes"]
    env = services["agent"]["environment"]
    assert not any(re.search(r"KEY|SECRET|TOKEN|PASSWORD", name) for name in env)  # keys come from env_file
    assert services["agent"]["env_file"][0]["required"] is False


def test_compose_drops_privileges_for_the_agent():
    agent = COMPOSE["services"]["agent"]
    assert agent["cap_drop"] == ["ALL"] and "no-new-privileges:true" in agent["security_opt"]


def test_the_toolbox_image_version_is_the_one_the_binary_script_pins():
    """One version of Toolbox everywhere: local binary and container."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import get_toolbox
    assert COMPOSE["services"]["toolbox"]["image"].endswith(f":{get_toolbox.VERSION}")


# ── a real build (slow, off by default) ─────────────────────────────────────

@pytest.mark.docker
@pytest.mark.skipif(not os.getenv("AGENTCORP_TEST_DOCKER_BUILD"), reason="set AGENTCORP_TEST_DOCKER_BUILD=1 (about 4 minutes)")
def test_the_image_builds_runs_as_non_root_and_answers_on_its_port():
    tag, name, port = "agentcorp:pytest", "agentcorp-pytest", "8097"
    subprocess.run(["docker", "build", "-t", tag, str(ROOT)], check=True, capture_output=True, timeout=1200)
    try:
        who = subprocess.run(["docker", "run", "--rm", "--entrypoint", "id", tag, "-u"], capture_output=True, text=True, timeout=120)
        assert who.stdout.strip() != "0"
        subprocess.run(["docker", "run", "-d", "--name", name, "-p", f"{port}:{port}", "-e", f"PORT={port}", tag],
                       check=True, capture_output=True, timeout=120)
        for _ in range(40):
            try:
                body = urllib.request.urlopen(f"http://127.0.0.1:{port}/list-apps", timeout=3).read().decode()
                break
            except OSError:
                time.sleep(2)
        else:
            pytest.fail("the container never answered on its port")
        assert "pm_agent" in body and "qa_agent" in body and "dev_agent" in body
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=60)
