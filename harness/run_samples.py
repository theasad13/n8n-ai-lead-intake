"""Offline demo: push every sample case through the lead logic and print a table.

    python harness/run_samples.py

Uses the saved fake Claude replies in harness/fixtures/llm_responses/, so it
makes no network calls and costs nothing. Add --js to run the real Code node
JavaScript from the workflow file in Node.js instead of the Python copy.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from helpers import SAMPLES, cases, config_values, llm_response, run_js, sample, settings  # noqa: E402

from lead_logic import logic  # noqa: E402


def main() -> int:
    use_js = "--js" in sys.argv
    print(f"engine: {'Node.js (workflow Code nodes)' if use_js else 'Python copy (lead_logic)'}\n")

    print("Intake and cleaning")
    for path in sorted(SAMPLES.glob("*.json")):
        lead = run_js("Normalize Lead", [sample(path.name)])[0] if use_js else logic.normalize_lead(sample(path.name))
        status = "valid" if lead["is_valid"] else f"dropped ({lead['invalid_reason']})"
        print(f"  {path.name:<28} {lead['lead_id']}  {lead['source']:<9} {status}")

    print("\nClassify and score (fake Claude replies)")
    header = f"  {'sample':<28} {'reply fixture':<22} {'labels by':<10} {'intent':<17} {'budget':<10} {'urgency':<7} score tier"
    print(header)
    for case in cases():
        lead = logic.normalize_lead(sample(case["sample"]))
        response = llm_response(case["llm"])
        if use_js:
            request = run_js("Build LLM Request", [lead], {"Config": [config_values()]})[0]
            result = run_js("Classify & Score", [response], {"Build LLM Request": [request]})[0]
        else:
            result = logic.classify_and_score(lead, response, settings())
        print(f"  {case['sample']:<28} {case['llm']:<22} {result['classifier']:<10} {result['intent']:<17} "
              f"{result['budget_band']:<10} {result['urgency']:<7} {result['score']:>5} {result['tier']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
