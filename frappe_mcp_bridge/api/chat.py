# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Endpoints behind the AI Chat page and the Ask AI drawer.

Each one checks access itself: the role, the settings switch and, for a chat, that it
belongs to the caller. Managers may read anyone's chat but change only their own.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from frappe_mcp_bridge.chat import access, attachments, billing, context, orchestrator, scrub, tools

MAX_MESSAGE_CHARS = 100_000
TITLE_CHARS = 60


@frappe.whitelist()
def bootstrap(doctype: str | None = None) -> dict:
	access.require_chat_access()
	settings = access.get_chat_settings()
	user = frappe.session.user
	default = access.default_model()

	models = []
	for model in access.allowed_models():
		offered = tools.tool_access(settings, model, user)
		models.append(
			{
				"name": model.name,
				"label": model.model_label,
				"provider_type": model.provider_type,
				"description": model.description,
				"vision": bool(model.supports_vision),
				"pdf": bool(model.supports_pdf),
				"may_read_erp_data": access.may_share_company_data(settings, model),
				"erp_tools": bool(offered.tools),
				"erp_note": offered.reason,
				"can_write": offered.can_write,
			}
		)

	return {
		"user": user,
		"full_name": frappe.utils.get_fullname(user),
		"is_manager": access.is_manager(),
		"models": models,
		"default_model": default.name if default else None,
		"templates": _templates(doctype),
		"usage": billing.usage_summary(user, settings),
		"erp_access": settings.erp_access,
		"attachments": {
			"allowed": bool(settings.allow_attachments),
			"max_mb": cint(settings.max_attachment_mb) or 10,
			"max_count": cint(settings.max_attachments) or 5,
		},
	}


def _templates(doctype: str | None) -> list[dict]:
	roles = set(frappe.get_roles())
	rows = frappe.get_all(
		"AI Prompt Template",
		filters={"enabled": 1},
		fields=["name", "title", "document_type", "model"],
		order_by="title asc",
	)

	visible = []
	for row in rows:
		allowed = set(
			frappe.get_all(
				"MCP Bridge Role",
				filters={"parent": row.name, "parenttype": "AI Prompt Template"},
				pluck="role",
			)
		)
		if allowed and not allowed & roles:
			continue
		if row.document_type and doctype and row.document_type != doctype:
			continue
		visible.append(row)

	return visible


@frappe.whitelist()
def list_conversations(
	search: str | None = None,
	archived: int = 0,
	user: str | None = None,
	start: int = 0,
	page_length: int = 40,
) -> list[dict]:
	access.require_chat_access()

	filters = {"status": "Archived"} if cint(archived) else {"status": ("!=", "Archived")}
	if user and user != frappe.session.user:
		# Someone else's chats: managers only, and only to read.
		if not access.is_manager():
			raise access.ChatAccessDenied(_("Only AI Chat Managers can see other people's chats."))
		if user != "all":
			filters["user"] = user
	else:
		filters["user"] = frappe.session.user

	if search:
		filters["title"] = ("like", f"%{search}%")

	return frappe.get_all(
		"AI Chat Conversation",
		filters=filters,
		fields=[
			"name",
			"title",
			"user",
			"model",
			"status",
			"last_activity",
			"message_count",
			"cost_usd",
			"cost_display",
			"display_currency",
			"context_doctype",
			"context_name",
		],
		order_by="last_activity desc",
		start=cint(start),
		page_length=min(cint(page_length) or 40, 200),
	)


@frappe.whitelist()
def get_conversation(name: str) -> dict:
	access.require_chat_access()
	conversation = access.get_conversation(name)

	if conversation.status == "Running" and orchestrator.is_stale(conversation):
		orchestrator.mark_idle(conversation.name)
		conversation.reload()

	return {
		"conversation": _conversation_for_ui(conversation),
		"messages": [orchestrator.message_for_ui(row) for row in orchestrator.load_rows(conversation.name)],
	}


@frappe.whitelist()
def poll(name: str, after_seq: int = 0) -> dict:
	"""For when the realtime socket is down: what has arrived since after_seq."""
	access.require_chat_access()
	conversation = access.get_conversation(name)
	rows = frappe.get_all(
		"AI Chat Message",
		filters={"conversation": conversation.name, "seq": (">", cint(after_seq))},
		fields=orchestrator.MESSAGE_FIELDS,
		order_by="seq asc",
	)

	return {
		"conversation": _conversation_for_ui(conversation),
		"messages": [orchestrator.message_for_ui(row) for row in rows],
	}


@frappe.whitelist(methods=["POST"])
def create_conversation(
	model: str | None = None, context_doctype: str | None = None, context_name: str | None = None
) -> dict:
	access.require_chat_access()
	chosen = access.require_model(model) if model else access.default_model()
	if not chosen:
		frappe.throw(_("No AI model is available to you yet. Ask an admin to set one up."))

	if bool(context_doctype) != bool(context_name):
		frappe.throw(_("Give both the document type and the document, or neither."))

	if context_doctype:
		# Checked now rather than at the first message, so the drawer can say so at once.
		context.masked_document(context_doctype, context_name)

	conversation = frappe.get_doc(
		{
			"doctype": "AI Chat Conversation",
			"title": _("New chat"),
			"user": frappe.session.user,
			"model": chosen.name,
			"status": "Active",
			"context_doctype": context_doctype,
			"context_name": context_name,
			"last_activity": now_datetime(),
			"display_currency": billing.display_currency(access.get_chat_settings())[0],
		}
	)
	conversation.insert(ignore_permissions=True)
	return _conversation_for_ui(conversation)


@frappe.whitelist(methods=["POST"])
def send_message(conversation: str, text: str = "", attachment_names=None, model: str | None = None) -> dict:
	access.require_chat_access()
	settings = access.get_chat_settings()
	user = frappe.session.user
	doc = access.get_conversation(conversation, for_write=True)

	# Lock the row, so two quick clicks cannot start two replies.
	status = frappe.db.sql(
		"select status from `tabAI Chat Conversation` where name = %s for update", doc.name
	)[0][0]
	if status == "Running":
		doc.reload()
		if not orchestrator.is_stale(doc):
			frappe.throw(_("Wait for the current reply to finish, or press Stop."))

	text = (text or "").strip()
	names = frappe.parse_json(attachment_names) if attachment_names else []
	if not text and not names:
		frappe.throw(_("Type a message first."))

	if len(text) > MAX_MESSAGE_CHARS:
		frappe.throw(_("That message is too long. Keep it under {0} characters.").format(MAX_MESSAGE_CHARS))

	if model and model != doc.model:
		switched = access.require_model(model)
		if not access.may_share_company_data(settings, switched) and orchestrator.holds_company_data(doc):
			raise access.ChatAccessDenied(
				_("This chat holds company data, so it can only carry on with a model allowed to read it.")
			)
		doc.model = model

	chosen = access.require_model(doc.model)
	if orchestrator.near_limit(doc.name, chosen):
		raise orchestrator.ContextFull(
			_("This chat is nearly as long as {0} can read. Continue in a new chat to carry on.").format(
				chosen.model_label
			)
		)
	billing.check_can_send(user, settings)

	clean, removed = scrub.scrub_secrets(text)
	stored_files = attachments.validate(doc.name, names, settings, chosen)
	context_text = _first_message_context(doc, chosen, settings)

	message = orchestrator.insert_message(
		doc,
		{
			"role": "User",
			"content": clean,
			"context_text": context_text,
			"attachments": json.dumps(stored_files) if stored_files else None,
			"redacted": ", ".join(removed) or None,
			"model": doc.model,
			**_request_meta(),
		},
	)

	updates = {"model": doc.model}
	if doc.title in (None, "", _("New chat"), "New chat"):
		updates["title"] = _title_from(clean or stored_files[0]["file_name"])
	if doc.status == "Archived":
		updates["status"] = "Active"
	frappe.db.set_value("AI Chat Conversation", doc.name, updates, update_modified=False)
	doc.update(updates)

	orchestrator.start_reply(doc)

	return {
		"message": orchestrator.message_for_ui(orchestrator._row(message)),
		"removed": removed,
		"conversation": _conversation_for_ui(doc, status="Running"),
	}


def _request_meta() -> dict:
	"""Where a message came from, kept with it for the audit trail."""
	request = getattr(frappe.local, "request", None)
	return {
		"ip_address": getattr(frappe.local, "request_ip", None),
		"user_agent": (request.headers.get("User-Agent", "") if request else "")[:500] or None,
	}


def _first_message_context(doc, model, settings) -> str | None:
	"""What rides on the chat's first message only: the earlier chat it continues, and the
	document it was opened from when site data may go to this model."""
	if frappe.db.exists("AI Chat Message", {"conversation": doc.name, "role": "User"}):
		return None

	pieces = []
	if doc.carry_over:
		pieces.append(f"This chat continues an earlier one. Its most recent part:\n\n{doc.carry_over}")

	if doc.context_doctype and doc.context_name and access.may_share_company_data(settings, model):
		pieces.append(context.document_context(doc.context_doctype, doc.context_name))

	return "\n\n".join(pieces) or None


def _title_from(text: str) -> str:
	line = _plain_title(text)
	return line if len(line) <= TITLE_CHARS else line[: TITLE_CHARS - 1].rsplit(" ", 1)[0] + "…"


def _plain_title(text: str) -> str:
	"""Titles are plain text. Desk puts page titles into the page as HTML after a regex
	strip, so angle brackets never reach one."""
	return " ".join((text or "").replace("<", " ").replace(">", " ").split())


@frappe.whitelist(methods=["POST"])
def upload_attachment(conversation: str) -> dict:
	"""Multipart upload of one file, field name `file`, to a chat of the caller's own."""
	access.require_chat_access()
	doc = access.get_conversation(conversation, for_write=True)

	upload = frappe.request.files.get("file") if frappe.request else None
	if not upload:
		frappe.throw(_("No file was uploaded."))

	return attachments.save_upload(
		doc.name, upload.filename, upload.stream.read(), access.get_chat_settings()
	)


@frappe.whitelist(methods=["POST"])
def stop(conversation: str) -> dict:
	access.require_chat_access()
	doc = access.get_conversation(conversation, for_write=True)
	orchestrator.request_stop(doc)
	return {"ok": True}


@frappe.whitelist(methods=["POST"])
def rename_conversation(conversation: str, title: str) -> dict:
	access.require_chat_access()
	doc = access.get_conversation(conversation, for_write=True)
	if doc.status == "Running":
		# The reply job is adding to the totals; a save now would write stale ones back.
		frappe.throw(_("Wait for the reply to finish first."))

	title = _plain_title(title)[:140]
	if not title:
		frappe.throw(_("Give the chat a title."))

	doc.title = title
	doc.save(ignore_permissions=True)
	return _conversation_for_ui(doc)


@frappe.whitelist(methods=["POST"])
def set_archived(conversation: str, archived: int = 1) -> dict:
	access.require_chat_access()
	doc = access.get_conversation(conversation, for_write=True)
	if doc.status == "Running":
		frappe.throw(_("Wait for the reply to finish first."))

	doc.status = "Archived" if cint(archived) else "Active"
	doc.save(ignore_permissions=True)
	return _conversation_for_ui(doc)


@frappe.whitelist(methods=["POST"])
def decide_action(action: str, approve: int = 1) -> dict:
	access.require_chat_access()
	conversation_name = frappe.db.get_value("AI Chat Action", action, "conversation")
	if not conversation_name:
		raise frappe.DoesNotExistError(_("That approval does not exist."))

	doc = access.get_conversation(conversation_name, for_write=True)

	# Lock the chat, then the approval, and read both as they stand now: a double click,
	# or two cards approved at once, must neither run a change twice nor start two replies.
	# Locking reads see the latest rows; plain reads here would see this request's snapshot.
	doc.status, doc.run_started_at = frappe.db.sql(
		"select status, run_started_at from `tabAI Chat Conversation` where name = %s for update", doc.name
	)[0]
	if doc.status == "Running" and not orchestrator.is_stale(doc):
		frappe.throw(_("Wait for the current reply to finish first."))

	status = frappe.db.sql("select status from `tabAI Chat Action` where name = %s for update", action)[0][0]
	if status != "Pending":
		frappe.throw(_("{0} is already {1}.").format(action, _(status).lower()))

	decided, note = tools.decide(action, approve=bool(cint(approve)))
	message = orchestrator.insert_message(
		doc, {"role": "User", "is_note": 1, "content": note, "model": doc.model, **_request_meta()}
	)

	if decided.status in ("Approved", "Failed"):
		# Let the model confirm what happened, or explain the failure.
		orchestrator.start_reply(doc)

	return {
		"action": decided.name,
		"status": decided.status,
		"message": orchestrator.message_for_ui(orchestrator._row(message)),
		"running": decided.status in ("Approved", "Failed"),
	}


@frappe.whitelist(methods=["POST"])
def continue_in_new_chat(conversation: str) -> dict:
	"""A fresh chat carrying the end of a full one, for when the model can read no more."""
	access.require_chat_access()
	old = access.get_conversation(conversation, for_write=True)
	access.require_model(old.model)

	new = frappe.get_doc(
		{
			"doctype": "AI Chat Conversation",
			"title": _plain_title(_("{0} (continued)").format(old.title or _("Chat")))[:140],
			"user": frappe.session.user,
			"model": old.model,
			"status": "Active",
			"context_doctype": old.context_doctype,
			"context_name": old.context_name,
			"continued_from": old.name,
			"carry_over": orchestrator.carry_over_text(old.name),
			"last_activity": now_datetime(),
			"display_currency": billing.display_currency(access.get_chat_settings())[0],
		}
	)
	new.insert(ignore_permissions=True)
	return _conversation_for_ui(new)


@frappe.whitelist()
def my_usage() -> dict:
	access.require_chat_access()
	return billing.usage_summary(frappe.session.user, access.get_chat_settings())


@frappe.whitelist()
def render_template(template: str, doctype: str | None = None, name: str | None = None) -> dict:
	access.require_chat_access()
	if template not in {row.name for row in _templates(doctype)}:
		raise access.ChatAccessDenied(_("That template is not available to you."))

	doc = frappe.get_cached_doc("AI Prompt Template", template)
	return {"text": context.render_prompt(doc.prompt, doctype, name), "model": doc.model}


def _conversation_for_ui(doc, status: str | None = None) -> dict:
	window = int(frappe.db.get_value("AI Model", doc.model, "context_window") or 0) if doc.model else 0
	used = orchestrator.context_used(doc.name)
	return {
		"context_used": used,
		"context_window": window,
		"near_limit": bool(window) and used >= orchestrator.NEAR_LIMIT * window,
		"continued_from": doc.continued_from,
		"name": doc.name,
		"title": doc.title,
		"user": doc.user,
		"user_name": frappe.utils.get_fullname(doc.user),
		"model": doc.model,
		"status": status or doc.status,
		"context_doctype": doc.context_doctype,
		"context_name": doc.context_name,
		"message_count": doc.message_count or 0,
		"input_tokens": doc.input_tokens or 0,
		"cached_input_tokens": doc.cached_input_tokens or 0,
		"output_tokens": doc.output_tokens or 0,
		"cost_usd": doc.cost_usd or 0,
		"cost_display": doc.cost_display or 0,
		"display_currency": doc.display_currency,
		"read_only": doc.user != frappe.session.user,
	}
