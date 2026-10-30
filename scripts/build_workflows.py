"""Build the importable n8n workflow JSON files from src/.

The Code node JavaScript lives in src/code_nodes/*.js and the shared rules in
src/rules.json, so both can be reviewed and tested as normal files. This script
embeds them into workflows/*.json. Run it after editing anything in src/:

    python scripts/build_workflows.py          # write workflows/*.json
    python scripts/build_workflows.py --check  # exit 1 if the files are stale
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
OUT = ROOT / "workflows"
NAMESPACE = uuid.UUID("6f1c0d1e-5a4b-4c3d-9e8f-7a6b5c4d3e2f")

MAIN_NAME = "Lead Intake - AI Classify and Route"
ERROR_NAME = "Lead Intake - Error Handler"
MAIN_FILE = "lead-intake-main.json"
ERROR_FILE = "lead-intake-error-handler.json"


def stable_id(*parts: str) -> str:
    return str(uuid.uuid5(NAMESPACE, "/".join(parts)))


def code(name: str) -> str:
    rules = json.loads((SRC / "rules.json").read_text(encoding="utf-8"))
    rules.pop("_comment", None)
    js = (SRC / "code_nodes" / name).read_text(encoding="utf-8")
    return js.replace("__RULES__", json.dumps(rules, ensure_ascii=False))


def credential(kind: str, label: str) -> dict:
    return {kind: {"id": "REPLACE_ME_" + kind.upper(), "name": label}}


def node(workflow: str, name: str, type_: str, version: float, position: tuple[int, int],
         parameters: dict, **extra) -> dict:
    item = {
        "parameters": parameters,
        "id": stable_id(workflow, name),
        "name": name,
        "type": type_,
        "typeVersion": version,
        "position": list(position),
    }
    item.update(extra)
    return item


def sticky(workflow: str, name: str, position: tuple[int, int], width: int, height: int,
           content: str, color: int = 7) -> dict:
    return node(workflow, name, "n8n-nodes-base.stickyNote", 1, position,
                {"content": content, "height": height, "width": width, "color": color})


def if_condition(workflow: str, name: str, left: str, operator: dict, right="") -> dict:
    return {
        "conditions": {
            "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
            "conditions": [{
                "id": stable_id(workflow, name, "condition"),
                "leftValue": left,
                "rightValue": right,
                "operator": operator,
            }],
            "combinator": "and",
        },
        "options": {},
    }


def link(*targets: str) -> list[dict]:
    return [{"node": target, "type": "main", "index": 0} for target in targets]


def config_assignment(workflow: str, name: str, value, type_: str) -> dict:
    return {"id": stable_id(workflow, "Config", name), "name": name, "value": value, "type": type_}


def build_main() -> dict:
    w = MAIN_NAME
    config_values = [
        ("hot_threshold", 70, "number"),
        ("warm_threshold", 40, "number"),
        ("llm_model", "claude-haiku-5-5", "string"),
        ("llm_api_url", "https://api.anthropic.com/v1/messages", "string"),
        ("sheet_id", "REPLACE_WITH_GOOGLE_SHEET_ID", "string"),
        ("sheet_name", "Leads", "string"),
        ("sender_name", "Your Name", "string"),
        ("company_name", "Your Company", "string"),
    ]
    nodes = [
        sticky(w, "Note: Overview", (-460, -420), 520, 400, (
            "## AI lead intake\n"
            "Receives a lead from a web form (webhook) or an inbox (IMAP), cleans it, drops repeats, "
            "asks Claude to label intent, budget and urgency, then scores it with fixed rules.\n\n"
            "**Outputs:** one row per lead in Google Sheets, a Discord alert for hot leads, "
            "and a Gmail **draft** reply for hot and warm leads.\n\n"
            "**Nothing is sent to the lead automatically.** A person reviews and sends each draft.\n\n"
            "Edit the **Config** node first: thresholds, model, Sheet ID, your name and company."), 4),
        sticky(w, "Note: Setup", (-460, 0), 520, 300, (
            "## Setup checklist\n"
            "1. Create credentials: Header Auth (name `x-api-key`, value = your Anthropic key), "
            "Google Sheets OAuth2, Gmail OAuth2, Discord Webhook.\n"
            "2. Open each node with a red warning and pick your credential.\n"
            "3. Create a Sheet tab named `Leads` with the header row from `docs/sheet-columns.csv`.\n"
            "4. Workflow Settings -> Error workflow -> **Lead Intake - Error Handler**.\n"
            "5. Activate, then POST a sample from `harness/samples/` to the production webhook URL."), 6),
        sticky(w, "Note: Intake", (100, -420), 700, 260, (
            "## 1. Intake and cleaning\n"
            "Webhook path: `POST /webhook/lead-intake` (JSON or form fields: name, email, company, phone, "
            "message, budget, website).\n`website` is a hidden honeypot field: bots fill it, people do not.\n\n"
            "The IMAP trigger is **disabled**. Enable it and add IMAP credentials to read leads from an inbox."), 5),
        sticky(w, "Note: AI", (1060, -420), 700, 260, (
            "## 2. AI labels, rules decide\n"
            "Claude returns JSON labels only. **Classify & Score** checks every label against the allowed "
            "values and falls back to keyword rules if the API fails or answers badly, so a lead is never lost.\n\n"
            "Score = intent points + budget points + urgency points (0-100). Spam always scores 0. "
            "Any amount the lead wrote (e.g. `$5k`) overrides the model's budget guess."), 3),
        sticky(w, "Note: Outputs", (1960, -420), 640, 260, (
            "## 3. Save, alert, draft\n"
            "Sheets uses **append or update** on `lead_id`, so re-runs do not create duplicate rows.\n\n"
            "Optional nodes (disabled): Slack alert, Airtable upsert, HubSpot contact upsert. "
            "Enable the ones you use and add credentials.\n\n"
            "The Gmail node only **creates a draft**. Do not change it to Send."), 2),

        node(w, "Lead Webhook", "n8n-nodes-base.webhook", 2, (120, 0), {
            "httpMethod": "POST",
            "path": "lead-intake",
            "responseMode": "onReceived",
            "responseCode": 202,
            "options": {},
        }, webhookId=stable_id(w, "Lead Webhook", "webhook")),
        node(w, "Email Lead (IMAP)", "n8n-nodes-base.emailReadImap", 2, (120, 200), {
            "mailbox": "INBOX",
            "postProcessAction": "read",
            "format": "simple",
            "options": {"customEmailConfig": "[\"UNSEEN\"]"},
        }, disabled=True, credentials=credential("imap", "Leads inbox (IMAP)")),
        node(w, "Config", "n8n-nodes-base.set", 3.4, (360, 100), {
            "mode": "manual",
            "assignments": {"assignments": [config_assignment(w, *value) for value in config_values]},
            "includeOtherFields": True,
            "options": {},
        }),
        node(w, "Normalize Lead", "n8n-nodes-base.code", 2, (580, 100), {
            "jsCode": code("normalize_lead.js"),
        }),
        node(w, "Valid Lead?", "n8n-nodes-base.if", 2.2, (800, 100), if_condition(
            w, "Valid Lead?", "={{ $json.is_valid }}",
            {"type": "boolean", "operation": "true", "singleValue": True})),
        node(w, "Drop Invalid Lead", "n8n-nodes-base.noOp", 1, (1020, 280), {}),
        node(w, "Dedupe Lead", "n8n-nodes-base.removeDuplicates", 2, (1020, 80), {
            "operation": "removeItemsSeenInPreviousExecutions",
            "dedupeValue": "={{ $json.dedupe_key }}",
            "options": {},
        }),
        node(w, "Build LLM Request", "n8n-nodes-base.code", 2, (1240, 80), {
            "jsCode": code("build_llm_request.js"),
        }),
        node(w, "Classify with Claude", "n8n-nodes-base.httpRequest", 4.2, (1460, 80), {
            "method": "POST",
            "url": "={{ $('Config').first().json.llm_api_url }}",
            "authentication": "genericCredentialType",
            "genericAuthType": "httpHeaderAuth",
            "sendHeaders": True,
            "headerParameters": {"parameters": [
                {"name": "anthropic-version", "value": "2026-10-21"},
                {"name": "content-type", "value": "application/json"},
            ]},
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify($json.llm_request) }}",
            "options": {"timeout": 30000},
        }, credentials=credential("httpHeaderAuth", "Anthropic API (x-api-key header)"),
            retryOnFail=True, maxTries=3, waitBetweenTries=2000, onError="continueRegularOutput"),
        node(w, "Classify & Score", "n8n-nodes-base.code", 2, (1680, 80), {
            "mode": "runOnceForEachItem",
            "jsCode": code("classify_and_score.js"),
        }),
        node(w, "Save to Google Sheets", "n8n-nodes-base.googleSheets", 4.5, (1980, -100), {
            "operation": "appendOrUpdate",
            "documentId": {"__rl": True, "value": "={{ $('Config').first().json.sheet_id }}", "mode": "id"},
            "sheetName": {"__rl": True, "value": "={{ $('Config').first().json.sheet_name }}", "mode": "name"},
            "columns": {"mappingMode": "autoMapInputData", "value": {}, "matchingColumns": ["lead_id"], "schema": []},
            "options": {},
        }, credentials=credential("googleSheetsOAuth2Api", "Google Sheets account")),
        node(w, "Hot Lead?", "n8n-nodes-base.if", 2.2, (1980, 100), if_condition(
            w, "Hot Lead?", "={{ $json.tier }}", {"type": "string", "operation": "equals"}, "hot")),
        node(w, "Discord Hot-Lead Alert", "n8n-nodes-base.discord", 2, (2220, 40), {
            "authentication": "webhook",
            "operation": "sendLegacy",
            "content": (
                "=**Hot lead** ({{ $json.score }}/100): {{ $json.name || $json.email }}"
                "{{ $json.company ? ' - ' + $json.company : '' }}\n"
                "Intent: {{ $json.intent }} | Budget: {{ $json.budget_band }} | Urgency: {{ $json.urgency }}\n"
                "{{ $json.summary }}\n"
                "Lead ID: {{ $json.lead_id }}. A reply draft is waiting in Gmail."),
            "options": {},
        }, credentials=credential("discordWebhookApi", "Discord #leads webhook")),
        node(w, "Slack Hot-Lead Alert (optional)", "n8n-nodes-base.slack", 2.2, (2220, 200), {
            "select": "channel",
            "channelId": {"__rl": True, "value": "#leads", "mode": "name"},
            "text": "=Hot lead ({{ $json.score }}/100): {{ $json.name || $json.email }} - {{ $json.summary }}",
            "otherOptions": {},
        }, disabled=True, credentials=credential("slackApi", "Slack bot")),
        node(w, "Needs Reply Draft?", "n8n-nodes-base.if", 2.2, (1980, 320), if_condition(
            w, "Needs Reply Draft?", "={{ $json.tier }}", {"type": "string", "operation": "notEquals"}, "cold")),
        node(w, "Create Gmail Draft", "n8n-nodes-base.gmail", 2.1, (2220, 380), {
            "resource": "draft",
            "operation": "create",
            "subject": "=Re: your enquiry",
            "emailType": "text",
            "message": "={{ $json.suggested_reply }}",
            "options": {"sendTo": "={{ $json.email }}"},
        }, credentials=credential("gmailOAuth2", "Gmail account")),
        node(w, "Airtable Upsert (optional)", "n8n-nodes-base.airtable", 2.1, (1980, 540), {
            "operation": "upsert",
            "base": {"__rl": True, "value": "REPLACE_WITH_AIRTABLE_BASE_ID", "mode": "id"},
            "table": {"__rl": True, "value": "REPLACE_WITH_AIRTABLE_TABLE_ID", "mode": "id"},
            "columns": {"mappingMode": "autoMapInputData", "value": {}, "matchingColumns": ["lead_id"], "schema": []},
            "options": {},
        }, disabled=True, credentials=credential("airtableTokenApi", "Airtable token")),
        node(w, "HubSpot Contact Upsert (optional)", "n8n-nodes-base.hubspot", 2.1, (1980, 720), {
            "authentication": "appToken",
            "resource": "contact",
            "operation": "upsert",
            "email": "={{ $json.email }}",
            "additionalFields": {},
        }, disabled=True, credentials=credential("hubspotAppToken", "HubSpot private app")),
    ]
    connections = {
        "Lead Webhook": {"main": [link("Config")]},
        "Email Lead (IMAP)": {"main": [link("Config")]},
        "Config": {"main": [link("Normalize Lead")]},
        "Normalize Lead": {"main": [link("Valid Lead?")]},
        "Valid Lead?": {"main": [link("Dedupe Lead"), link("Drop Invalid Lead")]},
        "Dedupe Lead": {"main": [link("Build LLM Request")]},
        "Build LLM Request": {"main": [link("Classify with Claude")]},
        "Classify with Claude": {"main": [link("Classify & Score")]},
        "Classify & Score": {"main": [link(
            "Save to Google Sheets", "Hot Lead?", "Needs Reply Draft?",
            "Airtable Upsert (optional)", "HubSpot Contact Upsert (optional)")]},
        "Hot Lead?": {"main": [link("Discord Hot-Lead Alert", "Slack Hot-Lead Alert (optional)"), []]},
        "Needs Reply Draft?": {"main": [link("Create Gmail Draft"), []]},
    }
    return workflow(MAIN_NAME, nodes, connections)


def build_error_handler() -> dict:
    w = ERROR_NAME
    nodes = [
        sticky(w, "Note: Error handler", (-60, -300), 560, 240, (
            "## Error handler\n"
            "Runs when the main lead intake workflow fails. Posts one short message with the workflow name, "
            "failed node and error text to Discord. Lead contents are not included.\n\n"
            "In the main workflow: Settings -> Error workflow -> pick this workflow."), 3),
        node(w, "Error Trigger", "n8n-nodes-base.errorTrigger", 1, (0, 0), {}),
        node(w, "Format Error", "n8n-nodes-base.code", 2, (220, 0), {"jsCode": code("format_error.js")}),
        node(w, "Discord Error Alert", "n8n-nodes-base.discord", 2, (440, 0), {
            "authentication": "webhook",
            "operation": "sendLegacy",
            "content": "={{ $json.alert_text }}",
            "options": {},
        }, credentials=credential("discordWebhookApi", "Discord #ops webhook")),
    ]
    connections = {
        "Error Trigger": {"main": [link("Format Error")]},
        "Format Error": {"main": [link("Discord Error Alert")]},
    }
    return workflow(ERROR_NAME, nodes, connections)


def workflow(name: str, nodes: list[dict], connections: dict) -> dict:
    return {
        "name": name,
        "nodes": nodes,
        "connections": connections,
        "active": False,
        "settings": {"executionOrder": "v1"},
        "pinData": {},
        "meta": {"templateCredsSetupCompleted": False},
        "tags": [],
    }


def render(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def outputs() -> dict[Path, str]:
    return {
        OUT / MAIN_FILE: render(build_main()),
        OUT / ERROR_FILE: render(build_error_handler()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if workflows/ is out of date")
    args = parser.parse_args()

    stale = []
    for path, text in outputs().items():
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == text:
            continue
        if args.check:
            stale.append(path.name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
            print(f"wrote {path.relative_to(ROOT)}")
    if stale:
        print("out of date, run python scripts/build_workflows.py: " + ", ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
