# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""AI Prompt Templates offered to MCP clients as prompts, so Claude Code and the claude.ai
connector get the same ready-made prompts as AI Chat.

A template is listed to the users its roles allow, through the same gate as a read: MCP
switched on, the sign-in method, the allowed roles, the IP list and the rate limit. A
template written for one doctype takes the document's ID as an argument and is filled from
it as Ask AI fills it: with the user's own permissions, sensitive fields masked, and the read
recorded in MCP Bridge Log.
"""

import time

import frappe
from frappe import _

from frappe_mcp_bridge.chat import access, context
from frappe_mcp_bridge.frappe_mcp_bridge.doctype.mcp_bridge_settings.mcp_bridge_settings import get_settings
from frappe_mcp_bridge.mcp import gate, logger


def list_prompts() -> list[dict]:
	_check_gate()
	prompts = []
	for row in access.visible_templates():
		template = frappe.get_cached_doc("AI Prompt Template", row.name)
		entry = {
			"name": row.name,
			"title": row.title,
			"description": _("A ready-made prompt from AI Chat")
			+ (_(", for one {0}").format(row.document_type) if row.document_type else ""),
		}
		if _needs_document(template):
			entry["arguments"] = [
				{
					"name": "document",
					"description": _("The ID of the {0} to fill the prompt from.").format(row.document_type),
					"required": True,
				}
			]
		prompts.append(entry)
	return prompts


def get_prompt(name: str, arguments: dict | None) -> dict:
	"""The prompt's text, filled from the document named in the arguments where it needs one.
	Raises ValueError for a name or arguments the caller got wrong."""
	_check_gate()
	if name not in {row.name for row in access.visible_templates()}:
		raise ValueError(_("There is no prompt called {0} for you.").format(name))

	template = frappe.get_cached_doc("AI Prompt Template", name)
	if not _needs_document(template):
		return _messages(template.title, template.prompt)

	document = str((arguments or {}).get("document") or "").strip()
	if not document:
		raise ValueError(
			_("{0} needs the document argument: the ID of a {1}.").format(name, template.document_type)
		)

	started = time.monotonic()
	try:
		text = context.render_prompt(template.prompt, template.document_type, document)
	except (frappe.PermissionError, frappe.DoesNotExistError) as error:
		_log(template, document, "Blocked", started, str(error))
		raise
	_log(template, document, "Success", started)
	return _messages(template.title, text)


def _needs_document(template) -> bool:
	return bool(template.document_type and context.PLACEHOLDER.search(template.prompt or ""))


def _messages(title: str, text: str) -> dict:
	return {"description": title, "messages": [{"role": "user", "content": {"type": "text", "text": text}}]}


def _check_gate() -> None:
	settings = get_settings()
	allowed, reason = settings.is_capability_allowed("read")
	if not allowed:
		raise gate.MCPBlocked(reason)
	gate.check_sign_in(settings)
	gate.check_role(settings)
	gate.check_ip(settings)
	gate.check_rate_limit(settings)


def _log(template, document: str, status: str, started: float, error: str | None = None) -> None:
	logger.record(
		tool="prompts/get",
		capability="read",
		status=status,
		params={"prompt": template.name, "document": document},
		error=error,
		duration=time.monotonic() - started,
		client="MCP prompt",
		reference_doctype=template.document_type,
		reference_name=document,
	)
