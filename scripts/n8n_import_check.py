"""Optional: import both workflows into a throwaway local n8n and list them back.

Needs an n8n install (npm or Docker is not used here). Point N8N_BIN at the
n8n CLI entry, for example node_modules/n8n/bin/n8n:

    N8N_BIN=/path/to/node_modules/n8n/bin/n8n python scripts/n8n_import_check.py

It uses a temporary N8N_USER_FOLDER, so your real n8n data is not touched,
and it makes no network calls of its own. Workflows are imported, not run.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = sorted((ROOT / "workflows").glob("*.json"))


def n8n_command() -> list[str]:
    target = os.environ.get("N8N_BIN") or shutil.which("n8n")
    if not target:
        sys.exit("N8N_BIN is not set and n8n is not on PATH")
    return ["node", target] if target.endswith(("n8n", ".js")) and Path(target).is_file() else [target]


def main() -> int:
    command = n8n_command()
    with tempfile.TemporaryDirectory(prefix="n8n-import-check-") as folder:
        env = dict(os.environ, N8N_USER_FOLDER=folder, N8N_DIAGNOSTICS_ENABLED="false",
                   N8N_VERSION_NOTIFICATIONS_ENABLED="false", N8N_RUNNERS_ENABLED="false")
        for path in WORKFLOWS:
            result = subprocess.run(command + ["import:workflow", f"--input={path}"],
                                    capture_output=True, text=True, env=env, timeout=600)
            print(f"import {path.name}: exit {result.returncode}")
            if result.returncode != 0:
                print(result.stdout[-2000:], result.stderr[-2000:])
                return 1
        listing = subprocess.run(command + ["list:workflow"], capture_output=True, text=True, env=env, timeout=600)
        print(listing.stdout.strip())
        expected = {"Lead Intake - AI Classify and Route", "Lead Intake - Error Handler"}
        missing = [name for name in expected if name not in listing.stdout]
        if listing.returncode != 0 or missing:
            print("missing after import: " + ", ".join(missing))
            return 1
    print("n8n import check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
