// Normalize Lead: turn a web-form webhook or an IMAP email into one clean lead record.
// Mode: Run Once for All Items. Pure JavaScript, no external modules.
const RULES = __RULES__;
const LIMITS = RULES.limits;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

function decodeEntities(text) {
  return text
    .replace(/&nbsp;/gi, ' ')
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'")
    .replace(/&amp;/gi, '&');
}

function clean(value, maxLength) {
  if (value === undefined || value === null) return '';
  let text = String(value)
    .replace(/<(script|style)[^>]*>[\s\S]*?<\/\1>/gi, ' ')
    .replace(/<[^>]*>/g, ' ');
  text = decodeEntities(text).replace(/\s+/g, ' ').trim();
  return maxLength ? text.slice(0, maxLength) : text;
}

// FNV-1a 32-bit over UTF-16 code units. Same algorithm as lead_logic.logic.fnv1a.
function fnv1a(text) {
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(8, '0');
}

function parseFromHeader(from) {
  // IMAP gives "Jane Doe <jane@example.com>" or an object with value[0].address.
  if (from && typeof from === 'object' && Array.isArray(from.value) && from.value.length) {
    return { name: clean(from.value[0].name), email: clean(from.value[0].address) };
  }
  // Not clean(): that would strip "<jane@example.com>" as if it were an HTML tag.
  const text = String(from || '').replace(/\s+/g, ' ').trim();
  const match = text.match(/^(.*?)<([^>]+)>$/);
  if (match) return { name: match[1].replace(/["']/g, '').trim(), email: match[2].trim() };
  return { name: '', email: text };
}

function validIso(value) {
  if (!value) return '';
  const date = new Date(value);
  return isNaN(date.getTime()) ? '' : date.toISOString();
}

function fromWebForm(body) {
  return {
    source: clean(body.source, 40) || 'web_form',
    name: clean(body.name || body.full_name, LIMITS.name),
    email: clean(body.email).toLowerCase(),
    company: clean(body.company, LIMITS.company),
    phone: clean(body.phone).replace(/[^0-9+\-() ]/g, '').trim().slice(0, LIMITS.phone),
    message: clean(body.message, LIMITS.message),
    stated_budget: clean(body.budget, 60),
    received_at: validIso(body.submitted_at),
    honeypot: clean(body.website),
  };
}

function fromEmail(mail) {
  const sender = parseFromHeader(mail.from);
  const bodyText = clean(mail.textPlain || mail.text || mail.textHtml || mail.html);
  const subject = clean(mail.subject);
  return {
    source: 'email',
    name: clean(sender.name, LIMITS.name),
    email: sender.email.toLowerCase(),
    company: '',
    phone: '',
    message: [subject, bodyText].filter(Boolean).join(' | ').slice(0, LIMITS.message),
    stated_budget: '',
    received_at: validIso(mail.date),
    honeypot: '',
  };
}

function normalize(raw) {
  const isWebForm = raw && typeof raw.body === 'object' && raw.body !== null;
  const lead = isWebForm ? fromWebForm(raw.body) : fromEmail(raw || {});

  let invalidReason = '';
  if (lead.honeypot) invalidReason = 'honeypot';
  else if (!EMAIL_RE.test(lead.email)) invalidReason = 'invalid_email';
  else if (lead.message.length < 10) invalidReason = 'message_too_short';

  const messageKey = lead.message.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  const dedupeKey = lead.email + '|' + fnv1a(messageKey);

  return {
    lead_id: 'L-' + fnv1a(dedupeKey),
    dedupe_key: dedupeKey,
    source: lead.source,
    received_at: lead.received_at || new Date().toISOString(),
    name: lead.name,
    email: lead.email,
    company: lead.company,
    phone: lead.phone,
    message: lead.message,
    stated_budget: lead.stated_budget,
    is_valid: invalidReason === '',
    invalid_reason: invalidReason,
  };
}

// Dedupe Lead only remembers keys from earlier executions, so repeats inside
// one batch (e.g. one IMAP poll) are caught here.
const seen = new Set();
return $input.all().map((item) => {
  const lead = normalize(item.json);
  if (lead.is_valid && seen.has(lead.dedupe_key)) {
    lead.is_valid = false;
    lead.invalid_reason = 'duplicate_in_batch';
  }
  if (lead.is_valid) seen.add(lead.dedupe_key);
  return { json: lead };
});
