import copy
import subprocess
import sys
import unittest

from helpers import ERROR, MAIN, ROOT, config_values, load

import validate_workflows as vw


def by_name(workflow):
    return {node["name"]: node for node in workflow["nodes"]}


class ShippedWorkflowTests(unittest.TestCase):
    def test_both_workflows_pass_the_validator(self):
        for path in (MAIN, ERROR):
            with self.subTest(path=path.name):
                self.assertEqual(vw.validate_file(path), [])

    def test_committed_json_matches_src(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_workflows.py"), "--check"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_rules_are_embedded_in_code_nodes(self):
        for node in load(MAIN)["nodes"]:
            if node["type"] == "n8n-nodes-base.code":
                self.assertNotIn("__RULES__", node["parameters"]["jsCode"], node["name"])

    def test_main_flow_order(self):
        connections = load(MAIN)["connections"]

        def targets(source, output=0):
            return [t["node"] for t in connections[source]["main"][output]]

        self.assertEqual(targets("Lead Webhook"), ["Config"])
        self.assertEqual(targets("Valid Lead?", 0), ["Dedupe Lead"])
        self.assertEqual(targets("Valid Lead?", 1), ["Drop Invalid Lead"])
        self.assertEqual(targets("Classify with Claude"), ["Classify & Score"])
        self.assertIn("Save to Google Sheets", targets("Classify & Score"))
        self.assertEqual(targets("Hot Lead?", 0)[0], "Discord Hot-Lead Alert")
        self.assertEqual(targets("Needs Reply Draft?", 0), ["Create Gmail Draft"])

    def test_llm_failure_does_not_stop_the_lead(self):
        http = by_name(load(MAIN))["Classify with Claude"]
        self.assertEqual(http["onError"], "continueRegularOutput")
        self.assertTrue(http["retryOnFail"])
        self.assertEqual(http["parameters"]["authentication"], "genericCredentialType")

    def test_dedupe_remembers_previous_executions(self):
        dedupe = by_name(load(MAIN))["Dedupe Lead"]["parameters"]
        self.assertEqual(dedupe["operation"], "removeItemsSeenInPreviousExecutions")
        self.assertIn("dedupe_key", dedupe["dedupeValue"])

    def test_gmail_only_creates_drafts(self):
        gmail = by_name(load(MAIN))["Create Gmail Draft"]["parameters"]
        self.assertEqual((gmail["resource"], gmail["operation"]), ("draft", "create"))

    def test_optional_nodes_ship_disabled(self):
        nodes = by_name(load(MAIN))
        for name in ("Email Lead (IMAP)", "Slack Hot-Lead Alert (optional)",
                     "Airtable Upsert (optional)", "HubSpot Contact Upsert (optional)"):
            self.assertTrue(nodes[name].get("disabled"), name)

    def test_config_defaults_are_placeholders(self):
        config = config_values()
        self.assertEqual(config["sheet_id"], "REPLACE_WITH_GOOGLE_SHEET_ID")
        self.assertEqual((config["hot_threshold"], config["warm_threshold"]), (70, 40))
        self.assertEqual(config["llm_model"], "claude-haiku-5-5")

    def test_workflows_are_documented_with_sticky_notes(self):
        main_notes = [n for n in load(MAIN)["nodes"] if n["type"] == vw.STICKY]
        self.assertGreaterEqual(len(main_notes), 5)
        self.assertTrue(any("draft" in n["parameters"]["content"].lower() for n in main_notes))


class ValidatorCatchesProblems(unittest.TestCase):
    def setUp(self):
        self.main = load(MAIN)

    def problems_after(self, change):
        workflow = copy.deepcopy(self.main)
        change(workflow)
        return " | ".join(vw.validate(workflow))

    def node(self, workflow, name):
        return by_name(workflow)[name]

    def test_connection_to_missing_node(self):
        def change(w):
            w["connections"]["Config"]["main"][0][0]["node"] = "Nope"
        self.assertIn("missing node: Nope", self.problems_after(change))

    def test_orphan_node(self):
        def change(w):
            del w["connections"]["Needs Reply Draft?"]
        self.assertIn("Create Gmail Draft: not reachable", self.problems_after(change))

    def test_hard_coded_api_key(self):
        def change(w):
            self.node(w, "Config")["parameters"]["assignments"]["assignments"][0]["value"] = "sk-ant-api03-abcdefghijklmnop"
        self.assertIn("Anthropic API key", self.problems_after(change))

    def test_hard_coded_webhook_url(self):
        def change(w):
            self.node(w, "Discord Hot-Lead Alert")["parameters"]["content"] = "https://discord.com/api/webhooks/123/abc"
        self.assertIn("Discord webhook URL", self.problems_after(change))

    def test_gmail_send_is_rejected(self):
        def change(w):
            self.node(w, "Create Gmail Draft")["parameters"]["resource"] = "message"
        self.assertIn("drafts only", self.problems_after(change))

    def test_smtp_send_node_is_rejected(self):
        def change(w):
            node = copy.deepcopy(self.node(w, "Drop Invalid Lead"))
            node.update(name="Send Email", id="x", type="n8n-nodes-base.emailSend")
            w["nodes"].append(node)
            w["connections"]["Valid Lead?"]["main"][1].append({"node": "Send Email", "type": "main", "index": 0})
        self.assertIn("sends email automatically", self.problems_after(change))

    def test_real_credential_id(self):
        def change(w):
            self.node(w, "Save to Google Sheets")["credentials"]["googleSheetsOAuth2Api"]["id"] = "aB3dE5"
        self.assertIn("real-looking id", self.problems_after(change))

    def test_unknown_type_and_future_version(self):
        def change(w):
            self.node(w, "Config")["typeVersion"] = 9
            self.node(w, "Drop Invalid Lead")["type"] = "n8n-nodes-base.madeUp"
        problems = self.problems_after(change)
        self.assertIn("newer than n8n 2.44.6", problems)
        self.assertIn("unknown node type", problems)

    def test_duplicate_name_and_active_flag(self):
        def change(w):
            w["active"] = True
            w["nodes"].append(copy.deepcopy(self.node(w, "Config")))
        problems = self.problems_after(change)
        self.assertIn("duplicate node name: Config", problems)
        self.assertIn("exported as active", problems)

    def test_missing_required_node(self):
        def change(w):
            self.node(w, "Dedupe Lead")["disabled"] = True
        self.assertIn("removeDuplicates", self.problems_after(change))

    def test_no_trigger(self):
        def change(w):
            w["nodes"] = [n for n in w["nodes"] if n["type"] not in vw.TRIGGER_TYPES]
            w["connections"].pop("Lead Webhook")
            w["connections"].pop("Email Lead (IMAP)")
        self.assertIn("no trigger node", self.problems_after(change))

    def test_invalid_json_file(self):
        bad = ROOT / "tests" / "_bad.json"
        bad.write_text("{not json", encoding="utf-8")
        try:
            self.assertIn("invalid JSON", vw.validate_file(bad)[0])
        finally:
            bad.unlink()


if __name__ == "__main__":
    unittest.main()
