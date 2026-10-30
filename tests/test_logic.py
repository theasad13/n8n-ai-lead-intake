import unittest

from helpers import cases, llm_response, sample, settings

from lead_logic import logic


class CleaningTests(unittest.TestCase):
    def test_strips_html_scripts_and_entities(self):
        text = logic.clean("<p>Hi</p><script>alert(1)</script> Tom &amp; Jerry&nbsp;&lt;3")
        self.assertEqual(text, "Hi Tom & Jerry <3")

    def test_collapses_whitespace_and_truncates(self):
        self.assertEqual(logic.clean("  a \n\t b  ", 2), "a ")

    def test_none_becomes_empty(self):
        self.assertEqual(logic.clean(None), "")

    def test_fnv1a_known_vectors(self):
        self.assertEqual(logic.fnv1a(""), "811c9dc5")
        self.assertEqual(logic.fnv1a("a"), "e40c292c")
        self.assertEqual(logic.fnv1a("foobar"), "bf9cf968")

    def test_iso_and_rfc2822_dates_become_utc(self):
        self.assertEqual(logic.valid_iso("2026-10-30T09:15:00Z"), "2026-10-30T09:15:00.000Z")
        self.assertEqual(logic.valid_iso("Fri, 30 Oct 2026 15:45:00 +0100"), "2026-10-30T14:45:00.000Z")
        self.assertEqual(logic.valid_iso("not a date"), "")

    def test_from_header_with_display_name(self):
        self.assertEqual(logic.parse_from_header('"Tom B" <tom@x.example>'), {"name": "Tom B", "email": "tom@x.example"})
        self.assertEqual(logic.parse_from_header("tom@x.example"), {"name": "", "email": "tom@x.example"})


class NormalizeTests(unittest.TestCase):
    def test_web_form_lead(self):
        lead = logic.normalize_lead(sample("01-hot-web-form.json"))
        self.assertTrue(lead["is_valid"])
        self.assertEqual(lead["email"], "priya.sharma@bluefin-dental.example")
        self.assertEqual(lead["source"], "web_form")
        self.assertEqual(lead["stated_budget"], "$6,000")
        self.assertEqual(lead["received_at"], "2026-10-30T09:15:00.000Z")
        self.assertRegex(lead["lead_id"], r"^L-[0-9a-f]{8}$")

    def test_email_lead(self):
        lead = logic.normalize_lead(sample("06-email-lead.json"))
        self.assertTrue(lead["is_valid"])
        self.assertEqual((lead["source"], lead["name"]), ("email", "Tom Becker"))
        self.assertEqual(lead["email"], "tom.becker@greenline-logistics.example")
        self.assertTrue(lead["message"].startswith("Quote for CRM integration | Hello,"))

    def test_invalid_leads_are_flagged(self):
        self.assertEqual(logic.normalize_lead(sample("07-invalid-email.json"))["invalid_reason"], "invalid_email")
        self.assertEqual(logic.normalize_lead(sample("08-honeypot-bot.json"))["invalid_reason"], "honeypot")
        short = logic.normalize_lead({"body": {"email": "a@b.example", "message": "hi"}})
        self.assertEqual(short["invalid_reason"], "message_too_short")

    def test_resubmission_gets_same_dedupe_key(self):
        first = logic.normalize_lead(sample("01-hot-web-form.json"))
        again = logic.normalize_lead(sample("09-duplicate-of-01.json"))
        self.assertEqual(first["dedupe_key"], again["dedupe_key"])
        self.assertEqual(first["lead_id"], again["lead_id"])

    def test_repeat_in_same_batch_is_marked(self):
        leads = logic.normalize_batch([sample("01-hot-web-form.json"), sample("09-duplicate-of-01.json"),
                                       sample("07-invalid-email.json"), sample("07-invalid-email.json")])
        self.assertTrue(leads[0]["is_valid"])
        self.assertEqual(leads[1]["invalid_reason"], "duplicate_in_batch")
        self.assertEqual([l["invalid_reason"] for l in leads[2:]], ["invalid_email", "invalid_email"])

    def test_different_message_gets_different_key(self):
        other = logic.normalize_lead(sample("02-warm-support.json"))
        self.assertNotEqual(other["lead_id"], logic.normalize_lead(sample("01-hot-web-form.json"))["lead_id"])

    def test_html_removed_from_name_and_message(self):
        lead = logic.normalize_lead(sample("10-html-and-injection.json"))
        self.assertEqual(lead["name"], "Alex Doe")
        self.assertNotIn("<", lead["message"])
        self.assertNotIn("alert(1)", lead["message"])

    def test_message_is_capped(self):
        lead = logic.normalize_lead({"body": {"email": "a@b.example", "message": "x" * 9000}})
        self.assertEqual(len(lead["message"]), logic.LIMITS["message"])


class RuleTests(unittest.TestCase):
    def test_budget_parsing(self):
        self.assertEqual(logic.parse_budget("$6,000", True), 6000)
        self.assertEqual(logic.parse_budget("2k - 5k", True), 5000)
        self.assertEqual(logic.parse_budget("about USD 1500", True), 1500)
        self.assertIsNone(logic.parse_budget("call me on 555 0100", False))
        self.assertEqual(logic.parse_budget("budget is around $12k", False), 12000)
        self.assertIsNone(logic.parse_budget("", True))

    def test_budget_band_edges(self):
        expected = {None: "unknown", 499: "under_500", 500: "500_2k", 1999: "500_2k",
                    2000: "2k_10k", 9999: "2k_10k", 10000: "10k_plus"}
        for amount, band in expected.items():
            self.assertEqual(logic.budget_band(amount), band, amount)

    def test_intent_rules_order(self):
        self.assertEqual(logic.rules_intent("We sell backlinks, need a quote?"), "spam")
        self.assertEqual(logic.rules_intent("My CV for your job opening"), "job_seeker")
        self.assertEqual(logic.rules_intent("The app we built is broken"), "support_existing")
        self.assertEqual(logic.rules_intent("Can you build a CRM integration?"), "new_project")
        self.assertEqual(logic.rules_intent("What are your opening hours?"), "other")

    def test_urgency_rules(self):
        self.assertEqual(logic.rules_urgency("Need it ASAP"), "high")
        self.assertEqual(logic.rules_urgency("no rush at all"), "low")
        self.assertEqual(logic.rules_urgency("Hello"), "medium")

    def test_score_and_tier(self):
        self.assertEqual(logic.score_labels("new_project", "10k_plus", "high", 70, 40), (100, "hot"))
        self.assertEqual(logic.score_labels("spam", "10k_plus", "high", 70, 40), (0, "cold"))
        self.assertEqual(logic.score_labels("support_existing", "500_2k", "high", 70, 40), (65, "warm"))
        self.assertEqual(logic.score_labels("new_project", "under_500", "high", 70, 40), (70, "hot"))
        self.assertEqual(logic.score_labels("other", "under_500", "high", 70, 40), (40, "warm"))
        self.assertEqual(logic.score_labels("other", "under_500", "medium", 70, 40), (30, "cold"))


class ParseLlmTests(unittest.TestCase):
    def test_reads_text_block_after_thinking_block(self):
        parsed = logic.parse_llm(llm_response("hot-new-project.json"))
        self.assertEqual(parsed["intent"], "new_project")

    def test_reads_fenced_json(self):
        self.assertEqual(logic.parse_llm(llm_response("fenced-json.json"))["intent"], "partnership")

    def test_rejects_prose_errors_and_bad_shapes(self):
        self.assertIsNone(logic.parse_llm(llm_response("not-json.json")))
        self.assertIsNone(logic.parse_llm(llm_response("api-error.json")))
        self.assertIsNone(logic.parse_llm({"content": "text"}))
        self.assertIsNone(logic.parse_llm({"content": [{"type": "text", "text": "[1, 2]"}]}))
        self.assertIsNone(logic.parse_llm(None))

    def test_finds_json_inside_prose(self):
        body = {"content": [{"type": "text", "text": 'Here you go: {"intent": "other"} thanks'}]}
        self.assertEqual(logic.parse_llm(body), {"intent": "other"})


class ClassifyTests(unittest.TestCase):
    def test_all_fixture_cases(self):
        for case in cases():
            with self.subTest(sample=case["sample"], llm=case["llm"]):
                lead = logic.normalize_lead(sample(case["sample"]))
                result = logic.classify_and_score(lead, llm_response(case["llm"]), settings())
                for key, value in case["expect"].items():
                    self.assertEqual(result[key], value, key)

    def test_written_amount_beats_llm_budget_guess(self):
        lead = logic.normalize_lead(sample("01-hot-web-form.json"))
        response = {"content": [{"type": "text", "text": '{"intent": "new_project", "budget_band": "10k_plus", "urgency": "high"}'}]}
        self.assertEqual(logic.classify_and_score(lead, response, settings())["budget_band"], "2k_10k")

    def test_rules_reply_uses_template_and_first_name(self):
        lead = logic.normalize_lead(sample("10-html-and-injection.json"))
        result = logic.classify_and_score(lead, llm_response("api-error.json"), settings())
        self.assertTrue(result["suggested_reply"].startswith("Hi Alex,"))
        self.assertIn("Your Name\nYour Company", result["suggested_reply"])

    def test_spam_never_gets_a_reply(self):
        lead = logic.normalize_lead(sample("04-spam-seo.json"))
        response = {"content": [{"type": "text", "text": '{"intent": "spam", "budget_band": "unknown", "urgency": "low", "suggested_reply": "Buy now"}'}]}
        self.assertEqual(logic.classify_and_score(lead, response, settings())["suggested_reply"], "")

    def test_thresholds_come_from_settings(self):
        lead = logic.normalize_lead(sample("02-warm-support.json"))
        strict = dict(settings(), warm_threshold=60)
        self.assertEqual(logic.classify_and_score(lead, llm_response("support-existing.json"), strict)["tier"], "cold")


if __name__ == "__main__":
    unittest.main()
