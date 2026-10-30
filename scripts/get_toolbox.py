"""Download the pinned MCP Toolbox for Databases binary into .tools/ (git-ignored, ~290 MB).

    python scripts/get_toolbox.py

Then, in a second terminal from the repo root:
    python scripts/seed_company_db.py
    .tools/toolbox.exe --config toolbox/tools.yaml --port 5000
"""

import platform
import subprocess
import sys
import urllib.request
from pathlib import Path

VERSION = "1.15.0"
TARGET_DIR = Path(__file__).resolve().parent.parent / ".tools"
BASE_URL = "https://storage.googleapis.com/mcp-toolbox-for-databases"


def asset() -> tuple[str, str]:
    """(url path, local file name) for this machine."""
    system, machine = platform.system().lower(), platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "amd64"
    if system == "windows":
        return f"windows/{arch}/toolbox.exe", "toolbox.exe"
    if system == "darwin":
        return f"darwin/{arch}/toolbox", "toolbox"
    return f"linux/{arch}/toolbox", "toolbox"


def main() -> int:
    remote, name = asset()
    target = TARGET_DIR / name
    if target.exists():
        print(f"already installed: {target}")
    else:
        TARGET_DIR.mkdir(exist_ok=True)
        url = f"{BASE_URL}/v{VERSION}/{remote}"
        print(f"downloading {url}")
        urllib.request.urlretrieve(url, target)
        target.chmod(0o755)
    out = subprocess.run([str(target), "--version"], capture_output=True, text=True)
    print(out.stdout.strip() or out.stderr.strip())
    return 0 if f"version {VERSION}" in out.stdout else 1


if __name__ == "__main__":
    sys.exit(main())
