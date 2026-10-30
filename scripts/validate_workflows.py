"""Static checks for the n8n workflow JSON files. Standard library only.

    python scripts/validate_workflows.py                 # check workflows/*.json
    python scripts/validate_workflows.py path/to/x.json  # check one file

Checks: JSON shape, unique node names and ids, every connection points at a
real node, every node is reachable from a trigger, required nodes exist, node
types and versions are known, credentials are placeholders, no secrets are
hard-coded, and no node can send email on its own.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Highest typeVersion of each node type used here, as shipped in n8n 2.44.6
# (read from node_modules/n8n-nodes-base/dist/nodes/*). Lower versions still load.
KNOWN_NODE_VERSIONS = {
    "n8n-nodes-base.webhook": 2.2,
    "n8n-nodes-base.emailReadImap": 2.2,
    "n8n-nodes-base.errorTrigger": 1,
    "n8n-nodes-base.set": 3.5,
    "n8n-nodes-base.code": 2,
    "n8n-nodes-base.if": 2.3,
    "n8n-nodes-base.noOp": 1,
    "n8n-nodes-base.removeDuplicates": 2.4,
    "n8n-nodes-base.httpRequest": 4.5,
    "n8n-nodes-base.googleSheets": 4.7,
    "n8n-nodes-base.discord": 2,
    "n8n-nodes-base.slack": 2.7,
    "n8n-nodes-base.gmail": 2.2,
    "n8n-nodes-base.airtable": 2.2,
    "n8n-nodes-base.hubspot": 2.2,
    "n8n-nodes-base.stickyNote": 1,
}
TRIGGER_TYPES = {"n8n-nodes-base.webhook", "n8n-nodes-base.emailReadImap", "n8n-nodes-base.errorTrigger"}
STICKY = "n8n-nodes-base.stickyNote"
# Node types that can send an email by themselves. Only Gmail drafts are allowed.
SENDING_TYPES = {"n8n-nodes-base.emailSend", "n8n-nodes-base.microsoftOutlook", "n8n-nodes-base.sendGrid",
                 "n8n-nodes-base.mailgun", "n8n-nodes-base.mailjet"}

REQUIRED = {
    "main": {
        "n8n-nodes-base.webhook": 1,
        "n8n-nodes-base.code": 3,
        "n8n-nodes-base.removeDuplicates": 1,
        "n8n-nodes-base.httpRequest": 1,
        "n8n-nodes-base.googleSheets": 1,
        "n8n-nodes-base.discord": 1,
        "n8n-nodes-base.gmail": 1,
        STICKY: 3,
    },
    "error": {
        "n8n-nodes-base.errorTrigger": 1,
        "n8n-nodes-base.discord": 1,
        STICKY: 1,
    },
}

SECRET_PATTERNS = {
    "Anthropic API key": r"sk-ant-[A-Za-z0-9_\-]{10,}",
    "OpenAI-style API key": r"\bsk-[A-Za-z0-9]{20,}",
    "Slack token": r"\bxox[abprs]-[A-Za-z0-9\-]{10,}",
    "Slack webhook URL": r"hooks\.slack\.com/services/[A-Za-z0-9/]+",
    "Discord webhook URL": r"discord(?:app)?\.com/api/webhooks/\d+",
    "GitHub token": r"\bgh[pousr]_[A-Za-z0-9]{20,}",
    "Google API key": r"\bAIza[0-9A-Za-z_\-]{30,}",
    "AWS access key": r"\bAKIA[0-9A-Z]{16}\b",
    "Bearer token": r"Bearer\s+[A-Za-z0-9._\-]{20,}",
    "Private key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "HubSpot token": r"\bpat-(?:na|eu)\d-[0-9a-f\-]{20,}",
}


def kind_of(workflow: dict) -> str:
    types = {node.get("type") for node in workflow.get("nodes", [])}
    return "error" if "n8n-nodes-base.errorTrigger" in types else "main"


def validate(workflow: dict, raw_text: str | None = None) -> list[str]:
    """Return a list of problems. An empty list means the workflow passed."""
    problems: list[str] = []
    if not isinstance(workflow.get("name"), str) or not workflow["name"].strip():
        problems.append("workflow has no name")
    nodes = workflow.get("nodes")
    connections = workflow.get("connections")
    if not isinstance(nodes, list) or not nodes:
        return problems + ["workflow has no nodes list"]
    if not isinstance(connections, dict):
        return problems + ["workflow has no connections object"]
    if workflow.get("active"):
        problems.append("workflow is exported as active; ship it inactive")

    by_name: dict[str, dict] = {}
    ids: set[str] = set()
    for node in nodes:
        name = node.get("name")
        if not name:
            problems.append("a node has no name")
            continue
        if name in by_name:
            problems.append(f"duplicate node name: {name}")
        by_name[name] = node
        if node.get("id") in ids:
            problems.append(f"duplicate node id: {node.get('id')}")
        ids.add(node.get("id"))
        problems += _check_node(node)

    problems += _check_connections(connections, by_name)
    problems += _check_reachable(connections, by_name)
    problems += _check_required(nodes, kind_of(workflow))
    text = raw_text if raw_text is not None else json.dumps(workflow)
    for label, pattern in SECRET_PATTERNS.items():
        if re.search(pattern, text):
            problems.append(f"possible hard-coded secret: {label}")
    return problems


def _check_node(node: dict) -> list[str]:
    problems = []
    name, type_ = node["name"], node.get("type")
    if type_ not in KNOWN_NODE_VERSIONS:
        problems.append(f"{name}: unknown node type {type_}")
    elif not isinstance(node.get("typeVersion"), (int, float)) or node["typeVersion"] > KNOWN_NODE_VERSIONS[type_]:
        problems.append(f"{name}: typeVersion {node.get('typeVersion')} is newer than n8n 2.44.6 supports")
    position = node.get("position")
    if not (isinstance(position, list) and len(position) == 2 and all(isinstance(v, (int, float)) for v in position)):
        problems.append(f"{name}: position must be [x, y]")
    if not isinstance(node.get("parameters"), dict):
        problems.append(f"{name}: parameters must be an object")
        return problems
    for cred_type, cred in (node.get("credentials") or {}).items():
        if not str(cred.get("id", "")).startswith("REPLACE_ME"):
            problems.append(f"{name}: credential {cred_type} has a real-looking id; use a REPLACE_ME placeholder")
    params = node["parameters"]
    if type_ == "n8n-nodes-base.gmail" and params.get("resource") != "draft":
        problems.append(f"{name}: Gmail node must create drafts only (resource 'draft'), never send")
    if type_ in SENDING_TYPES and not node.get("disabled"):
        problems.append(f"{name}: {type_} sends email automatically; use a Gmail draft instead")
    if type_ == "n8n-nodes-base.code" and not str(params.get("jsCode", "")).strip():
        problems.append(f"{name}: Code node has no jsCode")
    if type_ == "n8n-nodes-base.httpRequest" and params.get("authentication") in (None, "none"):
        problems.append(f"{name}: HTTP Request must use a stored credential, not inline headers")
    return problems


def _check_connections(connections: dict, by_name: dict) -> list[str]:
    problems = []
    for source, outputs in connections.items():
        if source not in by_name:
            problems.append(f"connection from missing node: {source}")
            continue
        if by_name[source].get("type") == STICKY:
            problems.append(f"sticky note {source} must not have connections")
        for branches in outputs.get("main", []):
            for target in branches or []:
                if target.get("node") not in by_name:
                    problems.append(f"{source} connects to missing node: {target.get('node')}")
                elif target.get("type") != "main":
                    problems.append(f"{source} -> {target.get('node')}: connection type must be main")
    return problems


def _check_reachable(connections: dict, by_name: dict) -> list[str]:
    seen = {name for name, node in by_name.items() if node.get("type") in TRIGGER_TYPES}
    if not seen:
        return ["workflow has no trigger node"]
    queue = list(seen)
    while queue:
        current = queue.pop()
        for branches in connections.get(current, {}).get("main", []):
            for target in branches or []:
                if target.get("node") in by_name and target["node"] not in seen:
                    seen.add(target["node"])
                    queue.append(target["node"])
    return [f"{name}: not reachable from any trigger" for name, node in by_name.items()
            if node.get("type") != STICKY and name not in seen]


def _check_required(nodes: list[dict], kind: str) -> list[str]:
    problems = []
    for type_, minimum in REQUIRED[kind].items():
        count = sum(1 for node in nodes if node.get("type") == type_ and not node.get("disabled"))
        if count < minimum:
            problems.append(f"{kind} workflow needs at least {minimum} enabled {type_} node(s), found {count}")
    return problems


def validate_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        return [f"invalid JSON: {error}"]
    return validate(data, text)


def main(argv: list[str]) -> int:
    paths = [Path(arg) for arg in argv] or sorted((ROOT / "workflows").glob("*.json"))
    failed = False
    for path in paths:
        problems = validate_file(path)
        if problems:
            failed = True
            print(f"FAIL {path.name}")
            for problem in problems:
                print(f"  - {problem}")
        else:
            data = json.loads(path.read_text(encoding="utf-8"))
            print(f"OK   {path.name} ({len(data['nodes'])} nodes, {kind_of(data)} workflow)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
