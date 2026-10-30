// Classify & Score: read the LLM answer, fall back to keyword rules when it is
// missing or invalid, then compute a deterministic 0-100 score and tier.
// Mode: Run Once for Each Item. The LLM only labels; points and tiers come from RULES.
const RULES = __RULES__;
const request = $('Build LLM Request').item.json;
const lead = request.lead;
const settings = request.settings;
const response = $json;

function matchesAny(text, patterns) {
  return patterns.some((pattern) => new RegExp(pattern, 'i').test(text));
}

function rulesIntent(text) {
  for (const [intent, patterns] of RULES.intent_rules) {
    if (matchesAny(text, patterns)) return intent;
  }
  return 'other';
}

function rulesUrgency(text) {
  if (matchesAny(text, RULES.urgency_rules.high)) return 'high';
  if (matchesAny(text, RULES.urgency_rules.low)) return 'low';
  return 'medium';
}

// Largest amount in the text, in USD. "5k" means 5000. Bare numbers only count
// in the budget field; in the message they need a $ or USD marker.
function parseBudget(text, bareNumbersAllowed) {
  if (!text) return null;
  const pattern = bareNumbersAllowed
    ? /(?:\$|usd\s?)?(\d[\d,]*(?:\.\d+)?)(\s?k(?![a-z]))?/gi
    : /(?:\$|usd\s?)(\d[\d,]*(?:\.\d+)?)(\s?k(?![a-z]))?/gi;
  let best = null;
  for (const match of text.matchAll(pattern)) {
    let amount = parseFloat(match[1].replace(/,/g, ''));
    if (isNaN(amount)) continue;
    if (match[2]) amount *= 1000;
    if (best === null || amount > best) best = amount;
  }
  return best;
}

function budgetBand(amount) {
  if (amount === null) return 'unknown';
  for (const [limit, band] of RULES.budget_bands_usd) {
    if (amount < limit) return band;
  }
  return '10k_plus';
}

function firstName(name) {
  const first = (name || '').trim().split(/\s+/)[0];
  return first || 'there';
}

function fillTemplate(intent) {
  return RULES.reply_templates[intent]
    .replace('{first_name}', firstName(lead.name))
    .replace('{sender_name}', settings.sender_name)
    .replace('{company_name}', settings.company_name);
}

function parseLlm(body) {
  if (!body || body.error || !Array.isArray(body.content)) return null;
  const textBlock = body.content.find((block) => block.type === 'text');
  if (!textBlock || typeof textBlock.text !== 'string') return null;
  const text = textBlock.text.replace(/^\s*```(?:json)?\s*/i, '').replace(/\s*```\s*$/, '');
  const start = text.indexOf('{');
  const end = text.lastIndexOf('}');
  if (start === -1 || end <= start) return null;
  try {
    const parsed = JSON.parse(text.slice(start, end + 1));
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null;
  } catch (error) {
    return null;
  }
}

const text = lead.message;
const detectedAmount = parseBudget(lead.stated_budget, true) ?? parseBudget(text, false);
const fallback = {
  intent: rulesIntent(text),
  budget_band: budgetBand(detectedAmount),
  urgency: rulesUrgency(text),
};
const llm = parseLlm(response);

const labels = {};
let usedRules = 0;
for (const field of ['intent', 'budget_band', 'urgency']) {
  if (llm && RULES.enums[field].includes(llm[field])) {
    labels[field] = llm[field];
  } else {
    labels[field] = fallback[field];
    usedRules += 1;
  }
}

// An amount the lead actually wrote is a fact; it beats the model's guess.
if (detectedAmount !== null) labels.budget_band = budgetBand(detectedAmount);

const points = RULES.points;
let score = points.intent[labels.intent] + points.budget_band[labels.budget_band] + points.urgency[labels.urgency];
if (labels.intent === 'spam') score = 0;
score = Math.max(0, Math.min(100, score));

let tier = 'cold';
if (score >= settings.hot_threshold) tier = 'hot';
else if (score >= settings.warm_threshold) tier = 'warm';

const llmSummary = llm && typeof llm.summary === 'string' ? llm.summary.trim() : '';
const llmReply = llm && typeof llm.suggested_reply === 'string' ? llm.suggested_reply.trim() : '';
let suggestedReply = llmReply || fillTemplate(labels.intent);
if (labels.intent === 'spam') suggestedReply = '';

return {
  json: {
    lead_id: lead.lead_id,
    received_at: lead.received_at,
    source: lead.source,
    name: lead.name,
    email: lead.email,
    company: lead.company,
    phone: lead.phone,
    message: lead.message,
    stated_budget: lead.stated_budget,
    intent: labels.intent,
    budget_band: labels.budget_band,
    urgency: labels.urgency,
    score,
    tier,
    summary: (llmSummary || text.slice(0, 160)).slice(0, RULES.limits.summary),
    suggested_reply: suggestedReply.slice(0, RULES.limits.suggested_reply),
    classifier: !llm ? 'rules' : usedRules > 0 ? 'llm+rules' : 'llm',
    status: 'new',
  },
};
