"""Run the real Code node JavaScript (from workflows/*.json) in Node.js and
check it gives the same results as the Python copy in lead_logic/."""

import unittest

from helpers import ERROR, NODE, SAMPLES, cases, config_values, llm_response, run_js, sample, settings

from lead_logic import logic


@unittest.skipUnless(NODE, "Node.js is not installed")
class JsParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        names = sorted(path.name for path in SAMPLES.glob("*.json"))
        cls.names = names
        cls.js_leads = dict(zip(names, run_js("Normalize Lead", [sample(name) for name in names])))
        valid = [cls.js_leads[name] for name in names if cls.js_leads[name]["is_valid"]]
        cls.requests = run_js("Build LLM Request", valid, {"Config": [config_values()]})

    def test_normalize_matches_python(self):
        python_leads = dict(zip(self.names, logic.normalize_batch([sample(name) for name in self.names])))
        for name in self.names:
            with self.subTest(sample=name):
                self.assertEqual(self.js_leads[name], python_leads[name])

    def test_repeat_in_same_batch_is_dropped(self):
        self.assertEqual(self.js_leads["09-duplicate-of-01.json"]["invalid_reason"], "duplicate_in_batch")

    def test_llm_request_shape(self):
        self.assertEqual(len(self.requests), 7)
        for request in self.requests:
            body = request["llm_request"]
            self.assertEqual(body["model"], "claude-haiku-5-5")
            self.assertNotIn("temperature", body)
            self.assertIn("untrusted", body["system"])
            self.assertIn(request["lead"]["message"], body["messages"][0]["content"])
            self.assertEqual(request["settings"]["hot_threshold"], 70)

    def test_classify_and_score_matches_python(self):
        for case in cases():
            with self.subTest(sample=case["sample"], llm=case["llm"]):
                lead = self.js_leads[case["sample"]]
                request = next(r for r in self.requests if r["lead"]["lead_id"] == lead["lead_id"])
                js = run_js("Classify & Score", [llm_response(case["llm"])], {"Build LLM Request": [request]})[0]
                self.assertEqual(js, logic.classify_and_score(lead, llm_response(case["llm"]), settings()))
                for key, value in case["expect"].items():
                    self.assertEqual(js[key], value, key)

    def test_format_error_hides_lead_data(self):
        payload = {"execution": {"id": "231", "lastNodeExecuted": "Save to Google Sheets",
                                 "error": {"message": "The caller does not have permission"}},
                   "workflow": {"name": "Lead Intake - AI Classify and Route"}}
        out = run_js("Format Error", [payload], workflow=ERROR)[0]
        self.assertIn("Failed node: Save to Google Sheets", out["alert_text"])
        self.assertEqual(out["execution_id"], "231")


if __name__ == "__main__":
    unittest.main()
