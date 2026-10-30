"""Python copy of the logic inside the n8n Code nodes.

The workflow runs the JavaScript in src/code_nodes/. This module does the same
work so it can be unit-tested with the standard library, and
tests/test_js_parity.py runs both on the same inputs and checks they agree.
Both read their rules and weights from src/rules.json.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

RULES_PATH = Path(__file__).resolve().parent.parent / "src" / "rules.json"
RULES: dict[str, Any] = json.loads(RULES_PATH.read_text(encoding="utf-8"))
LIMITS = RULES["limits"]
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")

_ENTITIES = [("&nbsp;", " "), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"), ("&amp;", "&")]


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def clean(value: Any, max_length: int | None = None) -> str:
    """Strip HTML, decode common entities and collapse whitespace."""
    text = _to_text(value)
    text = re.sub(r"<(script|style)[^>]*>[\s\S]*?</\1>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]*>", " ", text)
    for entity, char in _ENTITIES:
        text = re.sub(re.escape(entity), char, text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_length] if max_length else text


def fnv1a(text: str) -> str:
    """FNV-1a 32-bit over UTF-16 code units, matching the JavaScript version."""
    data = text.encode("utf-16-le")
    value = 0x811C9DC5
    for i in range(0, len(data), 2):
        value ^= data[i] | (data[i + 1] << 8)
        value = (value * 0x01000193) & 0xFFFFFFFF
    return f"{value:08x}"


def valid_iso(value: Any) -> str:
    """Return an ISO-8601 UTC timestamp like JavaScript's toISOString(), or ''."""
    if not value:
        return ""
    text = str(value).strip()
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    return parsed.strftime("%Y-%m-%dT%H:%M:%S.") + f"{parsed.microsecond // 1000:03d}Z"


def parse_from_header(sender: Any) -> dict[str, str]:
    if isinstance(sender, dict) and isinstance(sender.get("value"), list) and sender["value"]:
        first = sender["value"][0]
        return {"name": clean(first.get("name")), "email": clean(first.get("address"))}
    # Not clean(): that would strip "<jane@example.com>" as if it were an HTML tag.
    text = re.sub(r"\s+", " ", _to_text(sender)).strip()
    match = re.match(r"^(.*?)<([^>]+)>$", text)
    if match:
        return {"name": re.sub(r"[\"']", "", match.group(1)).strip(), "email": match.group(2).strip()}
    return {"name": "", "email": text}


def _from_web_form(body: dict) -> dict[str, str]:
    phone = re.sub(r"[^0-9+\-() ]", "", clean(body.get("phone"))).strip()[: LIMITS["phone"]]
    return {
        "source": clean(body.get("source"), 40) or "web_form",
        "name": clean(body.get("name") or body.get("full_name"), LIMITS["name"]),
        "email": clean(body.get("email")).lower(),
        "company": clean(body.get("company"), LIMITS["company"]),
        "phone": phone,
        "message": clean(body.get("message"), LIMITS["message"]),
        "stated_budget": clean(body.get("budget"), 60),
        "received_at": valid_iso(body.get("submitted_at")),
        "honeypot": clean(body.get("website")),
    }


def _from_email(mail: dict) -> dict[str, str]:
    sender = parse_from_header(mail.get("from"))
    body_text = clean(mail.get("textPlain") or mail.get("text") or mail.get("textHtml") or mail.get("html"))
    subject = clean(mail.get("subject"))
    return {
        "source": "email",
        "name": clean(sender["name"], LIMITS["name"]),
        "email": sender["email"].lower(),
        "company": "",
        "phone": "",
        "message": " | ".join(part for part in (subject, body_text) if part)[: LIMITS["message"]],
        "stated_budget": "",
        "received_at": valid_iso(mail.get("date")),
        "honeypot": "",
    }


def normalize_lead(raw: dict, now: str | None = None) -> dict[str, Any]:
    """Same output as the 'Normalize Lead' Code node for one input item."""
    raw = raw or {}
    is_web_form = isinstance(raw.get("body"), dict)
    lead = _from_web_form(raw["body"]) if is_web_form else _from_email(raw)

    invalid_reason = ""
    if lead["honeypot"]:
        invalid_reason = "honeypot"
    elif not EMAIL_RE.match(lead["email"]):
        invalid_reason = "invalid_email"
    elif len(lead["message"]) < 10:
        invalid_reason = "message_too_short"

    message_key = re.sub(r"[^a-z0-9]+", " ", lead["message"].lower()).strip()
    dedupe_key = lead["email"] + "|" + fnv1a(message_key)
    received_at = lead["received_at"] or now or valid_iso(datetime.now(timezone.utc).isoformat())

    return {
        "lead_id": "L-" + fnv1a(dedupe_key),
        "dedupe_key": dedupe_key,
        "source": lead["source"],
        "received_at": received_at,
        "name": lead["name"],
        "email": lead["email"],
        "company": lead["company"],
        "phone": lead["phone"],
        "message": lead["message"],
        "stated_budget": lead["stated_budget"],
        "is_valid": invalid_reason == "",
        "invalid_reason": invalid_reason,
    }



def normalize_batch(raws: list[dict], now: str | None = None) -> list[dict[str, Any]]:
    """Same output as the 'Normalize Lead' Code node for a whole batch of items.

    Dedupe Lead only remembers keys from earlier executions, so repeats inside
    one batch are marked invalid here.
    """
    seen: set[str] = set()
    leads = []
    for raw in raws:
        lead = normalize_lead(raw, now)
        if lead["is_valid"] and lead["dedupe_key"] in seen:
            lead["is_valid"] = False
            lead["invalid_reason"] = "duplicate_in_batch"
        if lead["is_valid"]:
            seen.add(lead["dedupe_key"])
        leads.append(lead)
    return leads

def _matches_any(text: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, text, flags=re.I) for pattern in patterns)


def rules_intent(text: str) -> str:
    for intent, patterns in RULES["intent_rules"]:
        if _matches_any(text, patterns):
            return intent
    return "other"


def rules_urgency(text: str) -> str:
    if _matches_any(text, RULES["urgency_rules"]["high"]):
        return "high"
    if _matches_any(text, RULES["urgency_rules"]["low"]):
        return "low"
    return "medium"


_BUDGET_ANY = re.compile(r"(?:\$|usd\s?)?(\d[\d,]*(?:\.\d+)?)(\s?k(?![a-z]))?", re.I)
_BUDGET_MARKED = re.compile(r"(?:\$|usd\s?)(\d[\d,]*(?:\.\d+)?)(\s?k(?![a-z]))?", re.I)


def parse_budget(text: str, bare_numbers_allowed: bool) -> float | None:
    """Largest USD amount in the text. Bare numbers count only when allowed."""
    if not text:
        return None
    pattern = _BUDGET_ANY if bare_numbers_allowed else _BUDGET_MARKED
    best = None
    for match in pattern.finditer(text):
        try:
            amount = float(match.group(1).replace(",", ""))
        except ValueError:
            continue
        if match.group(2):
            amount *= 1000
        if best is None or amount > best:
            best = amount
    return best


def budget_band(amount: float | None) -> str:
    if amount is None:
        return "unknown"
    for limit, band in RULES["budget_bands_usd"]:
        if amount < limit:
            return band
    return "10k_plus"


def score_labels(intent: str, band: str, urgency: str, hot: float, warm: float) -> tuple[int, str]:
    points = RULES["points"]
    score = points["intent"][intent] + points["budget_band"][band] + points["urgency"][urgency]
    if intent == "spam":
        score = 0
    score = max(0, min(100, score))
    tier = "hot" if score >= hot else "warm" if score >= warm else "cold"
    return score, tier


def parse_llm(body: Any) -> dict | None:
    """Pull the JSON object out of a Claude Messages API response, or None."""
    if not isinstance(body, dict) or body.get("error") or not isinstance(body.get("content"), list):
        return None
    block = next((b for b in body["content"] if isinstance(b, dict) and b.get("type") == "text"), None)
    if not block or not isinstance(block.get("text"), str):
        return None
    text = re.sub(r"^\s*```(?:json)?\s*", "", block["text"], flags=re.I)
    text = re.sub(r"\s*```\s*$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _first_name(name: str) -> str:
    parts = (name or "").strip().split()
    return parts[0] if parts else "there"


def _fill_template(intent: str, lead: dict, settings: dict) -> str:
    return (
        RULES["reply_templates"][intent]
        .replace("{first_name}", _first_name(lead["name"]), 1)
        .replace("{sender_name}", settings["sender_name"], 1)
        .replace("{company_name}", settings["company_name"], 1)
    )


def classify_and_score(lead: dict, response: Any, settings: dict) -> dict[str, Any]:
    """Same output as the 'Classify & Score' Code node for one lead."""
    text = lead["message"]
    detected = parse_budget(lead["stated_budget"], True)
    if detected is None:
        detected = parse_budget(text, False)
    fallback = {"intent": rules_intent(text), "budget_band": budget_band(detected), "urgency": rules_urgency(text)}
    llm = parse_llm(response)

    labels: dict[str, str] = {}
    used_rules = 0
    for field in ("intent", "budget_band", "urgency"):
        if llm and llm.get(field) in RULES["enums"][field]:
            labels[field] = llm[field]
        else:
            labels[field] = fallback[field]
            used_rules += 1
    if detected is not None:
        labels["budget_band"] = budget_band(detected)

    score, tier = score_labels(labels["intent"], labels["budget_band"], labels["urgency"],
                               float(settings["hot_threshold"]), float(settings["warm_threshold"]))

    llm_summary = llm["summary"].strip() if llm and isinstance(llm.get("summary"), str) else ""
    llm_reply = llm["suggested_reply"].strip() if llm and isinstance(llm.get("suggested_reply"), str) else ""
    reply = llm_reply or _fill_template(labels["intent"], lead, settings)
    if labels["intent"] == "spam":
        reply = ""

    return {
        "lead_id": lead["lead_id"],
        "received_at": lead["received_at"],
        "source": lead["source"],
        "name": lead["name"],
        "email": lead["email"],
        "company": lead["company"],
        "phone": lead["phone"],
        "message": text,
        "stated_budget": lead["stated_budget"],
        "intent": labels["intent"],
        "budget_band": labels["budget_band"],
        "urgency": labels["urgency"],
        "score": score,
        "tier": tier,
        "summary": (llm_summary or text[:160])[: LIMITS["summary"]],
        "suggested_reply": reply[: LIMITS["suggested_reply"]],
        "classifier": "rules" if not llm else "llm+rules" if used_rules else "llm",
        "status": "new",
    }
