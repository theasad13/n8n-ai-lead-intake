"""Shared test helpers: paths, fixtures and a bridge to the Node.js harness."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

WORKFLOWS = ROOT / "workflows"
MAIN = WORKFLOWS / "lead-intake-main.json"
ERROR = WORKFLOWS / "lead-intake-error-handler.json"
SAMPLES = ROOT / "harness" / "samples"
LLM_RESPONSES = ROOT / "harness" / "fixtures" / "llm_responses"
CASES = ROOT / "harness" / "fixtures" / "cases.json"
NODE = shutil.which("node")


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sample(name: str) -> dict:
    return load(SAMPLES / name)


def llm_response(name: str) -> dict:
    return load(LLM_RESPONSES / name)


def cases() -> list[dict]:
    return load(CASES)


def config_values(workflow_path: Path = MAIN) -> dict:
    """The default values set in the workflow's Config node."""
    workflow = load(workflow_path)
    config = next(node for node in workflow["nodes"] if node["name"] == "Config")
    return {a["name"]: a["value"] for a in config["parameters"]["assignments"]["assignments"]}


def settings() -> dict:
    config = config_values()
    return {key: config[key] for key in ("hot_threshold", "warm_threshold", "sender_name", "company_name")}


def run_js(node_name: str, items: list[dict], nodes: dict | None = None, workflow: Path = MAIN) -> list[dict]:
    """Run a Code node's JavaScript from the workflow file through harness/run_code_node.js."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
        json.dump({"items": items, "nodes": nodes or {}}, handle)
        input_path = handle.name
    try:
        result = subprocess.run(
            [NODE, str(ROOT / "harness" / "run_code_node.js"), str(workflow), node_name, input_path],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
    finally:
        Path(input_path).unlink(missing_ok=True)
    if result.returncode != 0:
        raise AssertionError(f"node harness failed for {node_name}:\n{result.stderr}")
    return json.loads(result.stdout)
