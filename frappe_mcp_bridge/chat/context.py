# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""The open document, as the AI is allowed to see it: read with the user's own
permissions, field-level ones included, through the bridge's doctype lists, with
sensitive fields masked."""

import json
import re

import frappe
from frappe import _

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


# Rows of a list sent when nothing is selected, and selected rows sent at most.
MAX_LIST_ROWS = 50


def check_view(view) -> dict:
	"""A list or report the chat was opened from, checked now so the drawer can say no at
	once. Reading it later goes through the bridge's own tools, as any other read."""
	if not isinstance(view, dict) or view.get("kind") not in ("list", "report"):
		frappe.throw(_("Open the chat from a list or a report."))

	if view["kind"] == "report":
		report = str(view.get("report") or "")
		if not frappe.db.exists("Report", report) or not frappe.has_permission("Report", "read", report):
			frappe.throw(_("You can't run the report {0}.").format(report), frappe.PermissionError)
		filters = view.get("filters") or {}
		return {"kind": "report", "report": report, "filters": filters if isinstance(filters, dict) else {}}

	doctype = str(view.get("doctype") or "")
	allowed, reason = get_settings().is_doctype_allowed(doctype)
	if not allowed:
		frappe.throw(reason, frappe.PermissionError)
	if not frappe.has_permission(doctype, "read"):
		frappe.throw(_("You can't read {0} records.").format(doctype), frappe.PermissionError)

	names = [str(name) for name in view.get("names") or []][:MAX_LIST_ROWS]
	filters = view.get("filters") or []
	return {"kind": "list", "doctype": doctype, "names": names, "filters": filters if not names else []}


def view_context(view: dict) -> str:
	"""The list or report as the AI may see it: read through the bridge's list_documents or
	run_report, so the gate, masking and the MCP Bridge Log apply as to any read."""
	from frappe_mcp_bridge.api import mcp as bridge

	if view["kind"] == "report":
		envelope = bridge.execute(
			tool="run_report",
			params={"report_name": view["report"], "filters": view["filters"], "limit": 100},
			client="AI Chat",
		)
		intro = f"I opened this chat from the report {view['report']}, with filters {json.dumps(view['filters'], default=str)}."
	else:
		names = view["names"]
		envelope = bridge.execute(
			tool="list_documents",
			params={
				"doctype": view["doctype"],
				"filters": {"name": ["in", names]} if names else view["filters"],
				"limit": MAX_LIST_ROWS,
			},
			client="AI Chat",
		)
		intro = (
			f"I opened this chat from the {view['doctype']} list with {len(names)} rows selected."
			if names
			else f"I opened this chat from the {view['doctype']} list, filtered by {json.dumps(view['filters'], default=str)}."
		)

	if not envelope.get("ok"):
		reason = (envelope.get("error") or {}).get("message") or "it could not be read"
		return f"{intro} It could not be read: {reason}"

	data = json.dumps(envelope.get("result"), default=str, indent=1, ensure_ascii=False)
	if len(data) > MAX_CONTEXT_CHARS:
		data = data[:MAX_CONTEXT_CHARS] + "\n... (cut short)"
	return f"{intro} Its data, with sensitive fields hidden:\n```json\n{data}\n```"


def render_prompt(prompt: str, doctype: str | None = None, name: str | None = None) -> str:
	"""Fill {{ doc.fieldname }} from the document. Anything else in braces stays as written."""
	values = masked_document(doctype, name) if doctype and name else {}

	def fill(match):
		value = values.get(match.group(1))
		return "" if value is None else str(value)

	return PLACEHOLDER.sub(fill, prompt or "")
