# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""The open document, as the AI is allowed to see it: read with the user's own
permissions, field-level ones included, through the bridge's doctype lists, with
sensitive fields masked."""

import json
import re

import frappe

from frappe_mcp_bridge.frappe_mcp_bridge.doctype.mcp_bridge_settings.mcp_bridge_settings import get_settings
from frappe_mcp_bridge.mcp import masking, registry

MAX_CONTEXT_CHARS = 15_000

# Bookkeeping that would only spend tokens.
NOISE_KEYS = {"__onload", "__last_sync_on", "_user_tags", "_comments", "_assign", "_liked_by", "_seen"}

# The only placeholder templates support. Templates are filled by substitution, not
# rendered: Jinja would hand their authors frappe.db and friends without permission checks.
PLACEHOLDER = re.compile(r"\{\{\s*doc\.([A-Za-z0-9_]+)\s*\}\}")


def masked_document(doctype: str, name: str) -> dict:
	allowed, reason = get_settings().is_doctype_allowed(doctype)
	if not allowed:
		frappe.throw(reason, frappe.PermissionError)

	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")
	# Fields above the user's permission level are blanked, as frappe.client.get does.
	doc.apply_fieldlevel_read_permissions()

	data = {key: value for key, value in doc.as_dict().items() if key not in NOISE_KEYS}
	# Masked exactly as get_document would mask it for the MCP client.
	return masking.mask(get_settings(), registry.get("get_document"), {"doctype": doctype}, data)


def document_context(doctype: str, name: str) -> str:
	data = json.dumps(masked_document(doctype, name), default=str, indent=1, ensure_ascii=False)
	if len(data) > MAX_CONTEXT_CHARS:
		data = data[:MAX_CONTEXT_CHARS] + "\n... (cut short)"

	return (
		f"I opened this chat from {doctype} {name}. Its current data, with sensitive fields hidden:\n"
		f"```json\n{data}\n```"
	)


def render_prompt(prompt: str, doctype: str | None = None, name: str | None = None) -> str:
	"""Fill {{ doc.fieldname }} from the document. Anything else in braces stays as written."""
	values = masked_document(doctype, name) if doctype and name else {}

	def fill(match):
		value = values.get(match.group(1))
		return "" if value is None else str(value)

	return PLACEHOLDER.sub(fill, prompt or "")
