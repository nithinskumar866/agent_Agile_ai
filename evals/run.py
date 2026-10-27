"""Run the AgentCorp eval set with `adk eval`.

    .venv\\Scripts\\python -m evals.run            # all cases
    .venv\\Scripts\\python -m evals.run explicit_approval praise_does_not_publish

`adk eval` imports pm_agent from its file path and, unlike `adk web`, does not put the
repo root on sys.path, so pm_agent's sibling packages (dev_agent, shared, evals) would not
import. This runner sets PYTHONPATH and passes the eval set and config.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main(case_ids: list[str]) -> int:
    # Model replies can contain emoji; a Windows console or redirect defaults to cp1252.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    evalset = ROOT / "evals" / "agentcorp.evalset.json"
    target = f"{evalset}:{','.join(case_ids)}" if case_ids else str(evalset)
    adk = Path(sys.executable).with_name("adk.exe" if os.name == "nt" else "adk")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(ROOT), os.getenv("PYTHONPATH")])),
           "PYTHONIOENCODING": "utf-8"}
    cmd = [str(adk), "eval", str(ROOT / "pm_agent"), target,
           "--config_file_path", str(ROOT / "evals" / "test_config.json"), "--print_detailed_results"]
    proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    not_evaluated = []
    for line in proc.stdout:
        print(line, end="")
        # ADK counts a metric that crashed (e.g. failed to import) as NOT_EVALUATED, and
        # a case whose metrics were all skipped still shows PASSED. Treat that as a failure.
        if line.startswith("Metric:") and "Status: NOT_EVALUATED" in line:
            not_evaluated.append(line.strip())
    code = proc.wait()
    if not_evaluated:
        print("\nEVAL INVALID: these metrics were not evaluated (see errors above):", *not_evaluated, sep="\n  ")
        return code or 2
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
