"""Optional: run the main workflow inside a real local n8n, fully offline.

    N8N_BIN=/path/to/node_modules/n8n/bin/n8n python scripts/n8n_smoke_test.py

What it does, in a throwaway N8N_USER_FOLDER:
  1. starts a fake Claude API on 127.0.0.1 that answers with the saved replies
     in harness/fixtures/llm_responses/ (no real API, no cost);
  2. builds a test copy of the main workflow: the Webhook/IMAP triggers become a
     Manual Trigger plus a Code node that emits every sample lead, the model URL
     points at the fake API, and the nodes after "Classify & Score" (Sheets,
     Discord, Gmail, CRM) are removed because they need real accounts;
  3. imports a dummy Header Auth credential and the test workflow, executes it
     twice with `n8n execute`, and checks the results against lead_logic/.

The second run must reach "Classify & Score" with zero items: that proves the
dedupe history survives between executions.
"""

from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from build_workflows import build_main, stable_id  # noqa: E402

from lead_logic import logic  # noqa: E402

SAMPLES = ROOT / "harness" / "samples"
REPLIES = ROOT / "harness" / "fixtures" / "llm_responses"
CREDENTIAL_ID = "smokeTestCred001"
DOWNSTREAM = {"Save to Google Sheets", "Hot Lead?", "Discord Hot-Lead Alert", "Slack Hot-Lead Alert (optional)",
              "Needs Reply Draft?", "Create Gmail Draft", "Airtable Upsert (optional)",
              "HubSpot Contact Upsert (optional)"}
# Which saved reply the fake API returns, chosen by a phrase in the lead message.
REPLY_FOR = [
    ("patient enquiries", "hot-new-project.json"),
    ("stopped working", "support-existing.json"),
    ("junior developer", "job-seeker.json"),
    ("backlinks", "spam.json"),
    ("white-label", "fenced-json.json"),
    ("invoicing tool", "invalid-labels.json"),
]
DEFAULT_REPLY = "not-json.json"


def reply_name(message_text: str) -> str:
    return next((name for phrase, name in REPLY_FOR if phrase in message_text), DEFAULT_REPLY)


class FakeClaude(BaseHTTPRequestHandler):
    calls: list[dict] = []

    def do_POST(self):  # noqa: N802 (http.server naming)
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        FakeClaude.calls.append({"headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
        text = body.get("messages", [{}])[0].get("content", "")
        payload = (REPLIES / reply_name(text)).read_bytes()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


def smoke_workflow(api_url: str) -> dict:
    workflow = build_main()
    workflow["name"] = "Lead Intake - offline smoke test"
    samples = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(SAMPLES.glob("*.json"))]
    nodes = [n for n in workflow["nodes"]
             if n["name"] not in DOWNSTREAM and n["type"] not in
             ("n8n-nodes-base.webhook", "n8n-nodes-base.emailReadImap", "n8n-nodes-base.stickyNote")]
    for node in nodes:
        if node["name"] == "Config":
            for assignment in node["parameters"]["assignments"]["assignments"]:
                if assignment["name"] == "llm_api_url":
                    assignment["value"] = api_url
        if node["name"] == "Classify with Claude":
            node["credentials"] = {"httpHeaderAuth": {"id": CREDENTIAL_ID, "name": "Fake Claude key"}}
    nodes += [
        {"parameters": {}, "id": stable_id("smoke", "Manual Trigger"), "name": "Manual Trigger",
         "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1, "position": [0, 100]},
        {"parameters": {"jsCode": "return " + json.dumps(samples) + ".map((json) => ({ json }));"},
         "id": stable_id("smoke", "Sample Leads"), "name": "Sample Leads",
         "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [200, 100]},
    ]
    connections = {k: v for k, v in workflow["connections"].items()
                   if k not in DOWNSTREAM and k not in ("Lead Webhook", "Email Lead (IMAP)", "Classify & Score")}
    connections["Manual Trigger"] = {"main": [[{"node": "Sample Leads", "type": "main", "index": 0}]]}
    connections["Sample Leads"] = {"main": [[{"node": "Config", "type": "main", "index": 0}]]}
    workflow.update(nodes=nodes, connections=connections)
    return workflow


def n8n(command: list[str], env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(command + list(args), capture_output=True, text=True, encoding="utf-8",
                          env=env, timeout=600)


def items_of(run_output: str, node: str) -> list[dict]:
    start = run_output.find("{")
    data = json.loads(run_output[start:])
    runs = data["data"]["resultData"]["runData"]
    if node not in runs:
        return []
    return [item["json"] for item in (runs[node][0]["data"]["main"][0] or [])]


def main() -> int:
    target = os.environ.get("N8N_BIN")
    if not target:
        sys.exit("set N8N_BIN to the n8n CLI, e.g. node_modules/n8n/bin/n8n")
    command = ["node", target] if Path(target).is_file() else [target]

    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeClaude)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    api_url = f"http://127.0.0.1:{server.server_port}/v1/messages"
    failures: list[str] = []

    with tempfile.TemporaryDirectory(prefix="n8n-smoke-") as folder:
        env = dict(os.environ, N8N_USER_FOLDER=folder, N8N_DIAGNOSTICS_ENABLED="false",
                   N8N_VERSION_NOTIFICATIONS_ENABLED="false")
        work = Path(folder)
        (work / "cred.json").write_text(json.dumps([{
            "id": CREDENTIAL_ID, "name": "Fake Claude key", "type": "httpHeaderAuth",
            "data": {"name": "x-api-key", "value": "offline-smoke-test"},
        }]), encoding="utf-8")
        (work / "wf.json").write_text(json.dumps(smoke_workflow(api_url)), encoding="utf-8")
        for args in (("import:credentials", f"--input={work / 'cred.json'}"),
                     ("import:workflow", f"--input={work / 'wf.json'}")):
            result = n8n(command, env, *args)
            if result.returncode != 0:
                print(result.stdout[-3000:], result.stderr[-3000:])
                return 1
        listing = n8n(command, env, "list:workflow").stdout
        match = re.search(r"^(\w+)\|Lead Intake - offline smoke test$", listing, re.M)
        if not match:
            print("could not find imported workflow:\n" + listing)
            return 1
        workflow_id = match.group(1)

        first = n8n(command, env, "execute", f"--id={workflow_id}", "--rawOutput")
        if first.returncode != 0:
            print(first.stdout[-3000:], first.stderr[-3000:])
            return 1
        normalized = items_of(first.stdout, "Normalize Lead")
        deduped = items_of(first.stdout, "Dedupe Lead")
        scored = items_of(first.stdout, "Classify & Score")
        print(f"run 1: {len(normalized)} leads in, {sum(l['is_valid'] for l in normalized)} valid, "
              f"{len(deduped)} after dedupe, {len(scored)} scored, {len(FakeClaude.calls)} fake API calls")

        settings = {"hot_threshold": 70, "warm_threshold": 40, "sender_name": "Your Name", "company_name": "Your Company"}
        leads_by_id = {lead["lead_id"]: lead for lead in normalized if lead["is_valid"]}
        for result in scored:
            lead = leads_by_id[result["lead_id"]]
            reply = json.loads((REPLIES / reply_name(lead["message"])).read_text(encoding="utf-8"))
            expected = logic.classify_and_score(lead, reply, settings)
            status = "same as Python" if result == expected else "DIFFERS from Python"
            if result != expected:
                failures.append(f"{lead['lead_id']} differs from lead_logic")
            print(f"  {lead['lead_id']} {result['classifier']:<9} {result['intent']:<17} "
                  f"{result['score']:>3} {result['tier']:<5} {status}")
        for call in FakeClaude.calls:
            if call["headers"].get("x-api-key") != "offline-smoke-test":
                failures.append("x-api-key header missing on API call")
            if call["headers"].get("anthropic-version") != "2026-10-21":
                failures.append("anthropic-version header missing on API call")
        if len(scored) != len({l["dedupe_key"] for l in normalized if l["is_valid"]}):
            failures.append("expected one scored item per unique valid lead")

        second = n8n(command, env, "execute", f"--id={workflow_id}", "--rawOutput")
        rescored = items_of(second.stdout, "Classify & Score") if second.returncode == 0 else None
        print(f"run 2 (same leads again): {0 if not rescored else len(rescored)} scored")
        if second.returncode != 0 or rescored:
            failures.append("second run should drop every lead as already seen")

    server.shutdown()
    if failures:
        print("FAILED:\n  " + "\n  ".join(sorted(set(failures))))
        return 1
    print("n8n smoke test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
