# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Takes secrets out of what a user types before it is stored or sent to a provider.

A pasted API key or card number would otherwise sit in the chat history forever and be
forwarded to a third party with every later turn of that chat.
"""

import re

# (label, pattern, replacement). Order matters: the specific key formats run before the
# generic "password: ..." rule so each secret is reported under its own name.
PATTERNS = [
	(
		"private key",
		re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
		"[private key removed]",
	),
	("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"), "[API key removed]"),
	("OpenAI API key", re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}"), "[API key removed]"),
	("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "[API key removed]"),
	("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "[access key removed]"),
	("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), "[token removed]"),
	("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), "[token removed]"),
	(
		"JSON web token",
		re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
		"[token removed]",
	),
	(
		"Frappe API key",
		re.compile(r"\btoken\s+[a-f0-9]{15}:[a-f0-9]{15}\b", re.I),
		"token [API key removed]",
	),
	(
		"password",
		re.compile(r"(?i)\b(password|passwd|pwd|secret|api[_ -]?key)(\s*[:=]\s*)(\S+)"),
		r"\1\2[removed]",
	),
]

CARD_CANDIDATE = re.compile(r"\b(?:\d[ -]?){14,15}\d\b")

# Card brands by length and prefix. Barcodes (13 and 14 digits) and other long ERP
# numbers are left alone even when they happen to pass the Luhn check.
CARD_BRANDS = re.compile(r"^(?:3[47]\d{13}|4\d{15}|5[1-5]\d{14}|2[2-7]\d{14}|6(?:011|5\d\d)\d{12})$")


def scrub_secrets(text: str) -> tuple[str, list[str]]:
	"""Return the cleaned text and the kinds of secret taken out of it."""
	found: list[str] = []
	if not text:
		return text, found

	for label, pattern, replacement in PATTERNS:
		text, count = pattern.subn(replacement, text)
		if count:
			found.append(label)

	text, cards = _scrub_cards(text)
	if cards:
		found.append("card number")

	return text, found


def _scrub_cards(text: str) -> tuple[str, int]:
	removed = 0

	def replace(match):
		nonlocal removed
		digits = re.sub(r"\D", "", match.group(0))
		if CARD_BRANDS.match(digits) and _luhn(digits):
			removed += 1
			return "[card number removed]"
		return match.group(0)

	return CARD_CANDIDATE.sub(replace, text), removed


def _luhn(digits: str) -> bool:
	total = 0
	for index, char in enumerate(reversed(digits)):
		value = int(char)
		if index % 2:
			value *= 2
			if value > 9:
				value -= 9
		total += value
	return total % 10 == 0
