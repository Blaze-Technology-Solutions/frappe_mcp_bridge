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

from frappe_mcp_bridge.chat import (
	access,
	artifacts,
	attachments,
	billing,
	context,
	orchestrator,
	scrub,
	tools,
)

MAX_MESSAGE_CHARS = 100_000
TITLE_CHARS = 60
# Messages loaded at a time; a long chat fetches older ones as the user scrolls back.
PAGE = 100


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
				"erp_tools": offered.erp,
				"artifacts": offered.artifacts,
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
		"templates": access.visible_templates(doctype),
		"usage": billing.usage_summary(user, settings),
		"erp_access": settings.erp_access,
		"attachments": {
			"allowed": bool(settings.allow_attachments),
			"max_mb": cint(settings.max_attachment_mb) or 10,
			"max_count": cint(settings.max_attachments) or 5,
		},
	}


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
	# A chat opened and never used isn't listed; remove_empty_chats clears it after a day.
	filters["message_count"] = (">", 0)
	if user and user != frappe.session.user:
		# Someone else's chats: managers only, and only to read.
		if not access.is_manager():
			raise access.ChatAccessDenied(_("Only AI Chat Managers can see other people's chats."))
		if user != "all":
			filters["user"] = user
	else:
		filters["user"] = frappe.session.user

	or_filters = None
	if search:
		# Titles, and what was said: the user's own words and the replies.
		like = f"%{search}%"
		said = frappe.get_all(
			"AI Chat Message",
			filters={
				**({"user": filters["user"]} if "user" in filters else {}),
				"role": ("in", ("User", "Assistant")),
				"content": ("like", like),
			},
			pluck="conversation",
			distinct=True,
			limit_page_length=500,
		)
		or_filters = {"title": ("like", like)}
		if said:
			or_filters["name"] = ("in", said)

	rows = frappe.get_all(
		"AI Chat Conversation",
		filters=filters,
		or_filters=or_filters,
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
	if search:
		_add_matches(rows, search)
	return rows


def _add_matches(rows: list[dict], search: str) -> None:
	"""Where a chat matched by what was said rather than its title, a few words around it."""
	needle = search.lower()
	untitled = [row.name for row in rows if needle not in (row.title or "").lower()]
	if not untitled:
		return

	found = {}
	for message in frappe.get_all(
		"AI Chat Message",
		filters={
			"conversation": ("in", untitled),
			"role": ("in", ("User", "Assistant")),
			"content": ("like", f"%{search}%"),
		},
		fields=["conversation", "content"],
		order_by="seq asc",
	):
		found.setdefault(message.conversation, message.content)

	for row in rows:
		text = " ".join((found.get(row.name) or "").split())
		at = text.lower().find(needle)
		if at >= 0:
			start = max(at - 40, 0)
			row.match = ("…" if start else "") + text[start : at + len(needle) + 60] + "…"


@frappe.whitelist()
def get_conversation(name: str) -> dict:
	access.require_chat_access()
	conversation = access.get_conversation(name)

	if conversation.status == "Running" and orchestrator.is_stale(conversation):
		orchestrator.mark_idle(conversation.name)
		conversation.reload()

	rows = orchestrator.load_rows(conversation.name, last=PAGE)
	return {
		"conversation": _conversation_for_ui(conversation),
		"messages": [orchestrator.message_for_ui(row) for row in rows],
		"has_more": _has_older(conversation.name, rows),
		"feedback": _feedback(conversation.name),
	}


@frappe.whitelist()
def older_messages(name: str, before_seq: int) -> dict:
	"""The page of messages before before_seq, for a long chat scrolled back."""
	access.require_chat_access()
	conversation = access.get_conversation(name)
	rows = frappe.get_all(
		"AI Chat Message",
		filters={"conversation": conversation.name, "seq": ("<", cint(before_seq))},
		fields=orchestrator.MESSAGE_FIELDS,
		order_by="seq desc",
		limit_page_length=PAGE,
	)[::-1]
	return {
		"messages": [orchestrator.message_for_ui(row) for row in rows],
		"has_more": _has_older(conversation.name, rows),
	}


def _has_older(conversation: str, rows: list) -> bool:
	return bool(rows) and bool(
		frappe.db.exists("AI Chat Message", {"conversation": conversation, "seq": ("<", rows[0].seq)})
	)


def _feedback(conversation: str) -> dict:
	"""The chat owner's rating of each reply they rated."""
	return dict(
		frappe.get_all(
			"AI Chat Feedback",
			filters={"conversation": conversation, "rating": ("in", ("Good", "Bad"))},
			fields=["message", "rating"],
			as_list=True,
		)
	)


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
		"feedback": _feedback(conversation.name),
	}


@frappe.whitelist(methods=["POST"])
def create_conversation(
	model: str | None = None,
	context_doctype: str | None = None,
	context_name: str | None = None,
	context_view=None,
) -> dict:
	"""A new chat, opened from a document, from a list or report (context_view), or bare."""
	access.require_chat_access()
	chosen = access.require_model(model) if model else access.default_model()
	if not chosen:
		frappe.throw(_("No AI model is available to you yet. Ask an admin to set one up."))

	view = context.check_view(frappe.parse_json(context_view)) if context_view else None
	if view:
		# Shown in the header: the list's doctype, or the report.
		context_doctype, context_name = (
			("Report", view["report"]) if view["kind"] == "report" else (view["doctype"], None)
		)
	elif bool(context_doctype) != bool(context_name):
		frappe.throw(_("Give both the document type and the document, or neither."))
	elif context_doctype:
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
			"context_view": json.dumps(view, default=str) if view else None,
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

	_switch_model(doc, model, settings)
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
	# The opened document and a carried-over chat are cleaned like the message itself.
	context_text, removed_from_context = scrub.scrub_secrets(_first_message_context(doc, chosen, settings))
	removed += [kind for kind in removed_from_context if kind not in removed]

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


def _switch_model(doc, model: str | None, settings) -> None:
	"""Carry the chat on with another model, if the user picked one and may."""
	if not model or model == doc.model:
		return
	switched = access.require_model(model)
	if not access.may_share_company_data(settings, switched) and orchestrator.holds_company_data(doc):
		raise access.ChatAccessDenied(
			_("This chat holds company data, so it can only carry on with a model allowed to read it.")
		)
	doc.model = model


@frappe.whitelist(methods=["POST"])
def regenerate(conversation: str, model: str | None = None) -> dict:
	"""A new answer to the last message, in place of a reply that used no tools, from the
	model picked now. The old reply stays in the chat and the bill, marked replaced, and is no
	longer sent to the model. Only the end of the chat changes, so everything before it goes
	to the model exactly as before."""
	access.require_chat_access()
	settings = access.get_chat_settings()
	doc = access.get_conversation(conversation, for_write=True)

	# Locked, so two quick clicks can't start two replies.
	doc.status, doc.run_started_at = frappe.db.sql(
		"select status, run_started_at from `tabAI Chat Conversation` where name = %s for update", doc.name
	)[0]
	if doc.status == "Running" and not orchestrator.is_stale(doc):
		frappe.throw(_("Wait for the current reply to finish, or press Stop."))

	rows = orchestrator.load_rows(doc.name)
	asked = max(
		(index for index, row in enumerate(rows) if row.role == "User" and not row.is_note), default=None
	)
	tail = rows[asked + 1 :] if asked is not None else []
	replies = [row for row in tail if row.role == "Assistant" and not row.exclude_from_context]
	if not replies or any(row.role == "Tool" or row.tool_calls for row in tail):
		frappe.throw(_("Only the last reply can be regenerated, and only one that used no tools."))

	_switch_model(doc, model, settings)
	chosen = access.require_model(doc.model)
	billing.check_can_send(frappe.session.user, settings)

	for row in replies:
		frappe.db.set_value(
			"AI Chat Message", row.name, {"exclude_from_context": 1, "replaced": 1}, update_modified=False
		)
	# A marker the model never sees, which the daily message limit counts like a message.
	notice = orchestrator.insert_message(
		doc,
		{
			"role": "Notice",
			"is_note": 1,
			"content": _("Regenerated with {0}.").format(chosen.model_label),
			"model": doc.model,
		},
	)
	frappe.db.set_value("AI Chat Conversation", doc.name, "model", doc.model, update_modified=False)
	orchestrator.start_reply(doc)

	return {
		"replaced": [row.name for row in replies],
		"message": orchestrator.message_for_ui(orchestrator._row(notice)),
		"conversation": _conversation_for_ui(doc, status="Running"),
	}


@frappe.whitelist(methods=["POST"])
def draft_email(
	doctype: str,
	name: str,
	subject: str | None = None,
	instruction: str | None = None,
	reply_to: str | None = None,
) -> dict:
	"""Draft with AI in the email dialog. Returns the draft; sending stays with the user."""
	from frappe_mcp_bridge.chat import drafts

	return drafts.draft(doctype, name, subject, instruction, reply_to)


@frappe.whitelist(methods=["POST"])
def set_feedback(message: str, rating: str = "", note: str | None = None) -> dict:
	"""The chat owner's thumbs up or down on a reply, with an optional note. Blank clears it."""
	access.require_chat_access()
	row = frappe.db.get_value("AI Chat Message", message, ["conversation", "role", "model"], as_dict=True)
	if not row or row.role != "Assistant":
		frappe.throw(_("Only AI replies can be rated."))
	# The owner only: a manager reading someone's chat rates nothing.
	access.get_conversation(row.conversation, for_write=True)
	if rating not in ("", "Good", "Bad"):
		frappe.throw(_("Rate a reply Good or Bad."))

	values = {"rating": rating, "note": ((note or "").strip()[:1000] or None) if rating else None}
	if frappe.db.exists("AI Chat Feedback", message):
		feedback = frappe.get_doc("AI Chat Feedback", message)
		feedback.update(values)
	else:
		feedback = frappe.get_doc(
			{
				"doctype": "AI Chat Feedback",
				"message": message,
				"conversation": row.conversation,
				"user": frappe.session.user,
				"model": row.model,
				**values,
			}
		)
	# Saved as a document, so Frappe cleans any markup in the note before managers see it.
	feedback.save(ignore_permissions=True)
	return {"message": message, "rating": rating}


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

	if access.may_share_company_data(settings, model):
		if doc.context_view:
			pieces.append(context.view_context(json.loads(doc.context_view)))
		elif doc.context_doctype and doc.context_name:
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
def decide_action(action: str, approve: int = 1, rows=None) -> dict:
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

	ticked = frappe.parse_json(rows) if rows else None
	decided, note = tools.decide(action, approve=bool(cint(approve)), rows=ticked)
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
def get_artifact(name: str) -> dict:
	"""One artifact version with its content, and the other versions of the same artifact.
	Readable by whoever may read its chat."""
	access.require_chat_access()
	conversation = frappe.db.get_value("AI Chat Artifact", name, "conversation")
	if not conversation:
		raise frappe.DoesNotExistError(_("That artifact does not exist."))
	access.get_conversation(conversation)

	artifact = artifacts.for_ui(name, with_content=True)
	artifact["versions"] = frappe.get_all(
		"AI Chat Artifact",
		filters={"conversation": conversation, "artifact_key": artifact.artifact_key},
		fields=["name", "version", "creation"],
		order_by="version asc",
	)
	return artifact


@frappe.whitelist()
def list_artifacts(conversation: str | None = None, user: str | None = None) -> list[dict]:
	"""The latest version of each artifact in one chat, or across all of the caller's own chats.
	Managers may list one other user's, or everyone's ("all"), to read."""
	access.require_chat_access()
	filters = {}
	if conversation:
		filters["conversation"] = access.get_conversation(conversation).name
	elif user and user != frappe.session.user:
		if not access.is_manager():
			raise access.ChatAccessDenied(_("Only AI Chat Managers can see other people's artifacts."))
		if user != "all":
			filters["user"] = user
	else:
		filters["user"] = frappe.session.user

	rows = frappe.get_all(
		"AI Chat Artifact",
		filters=filters,
		fields=[
			"name",
			"conversation",
			"user",
			"artifact_key",
			"version",
			"title",
			"artifact_type",
			"creation",
		],
		order_by="creation desc",
		limit_page_length=500,
	)
	latest = {}
	for row in rows:
		latest.setdefault((row.conversation, row.artifact_key), row)
	return list(latest.values())


@frappe.whitelist(methods=["POST"])
def save_artifact_as_note(name: str) -> dict:
	"""Keeps a copy of one of the caller's own documents or pages as a private Note. The Note is
	made with the caller's own permissions, and Frappe cleans its HTML again on the way in."""
	access.require_chat_access()
	artifact = artifacts.for_ui(name, with_content=True)
	if not artifact:
		raise frappe.DoesNotExistError(_("That artifact does not exist."))
	access.get_conversation(artifact.conversation)
	if artifact.user != frappe.session.user:
		raise access.ChatAccessDenied(_("Only the person who made an artifact can keep it as a Note."))
	if artifact.artifact_type not in ("document", "html"):
		frappe.throw(_("Only documents and pages can be kept as a Note."))

	content = artifact.content
	if artifact.artifact_type == "document":
		# Frappe's own cleaning keeps outside images, which would load each time the Note is
		# opened; the page cleaner takes them out, as the panel never shows them.
		content = artifacts.clean_html(frappe.utils.md_to_html(content))
	note = frappe.get_doc(
		{"doctype": "Note", "title": artifact.title[:140], "public": 0, "content": content}
	).insert()
	return {"name": note.name}


@frappe.whitelist()
def my_usage() -> dict:
	access.require_chat_access()
	return billing.usage_summary(frappe.session.user, access.get_chat_settings())


@frappe.whitelist()
def render_template(template: str, doctype: str | None = None, name: str | None = None) -> dict:
	access.require_chat_access()
	if template not in {row.name for row in access.visible_templates(doctype)}:
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
		"context_view": json.loads(doc.context_view) if doc.context_view else None,
		"message_count": doc.message_count or 0,
		"input_tokens": doc.input_tokens or 0,
		"cached_input_tokens": doc.cached_input_tokens or 0,
		"output_tokens": doc.output_tokens or 0,
		"cost_usd": doc.cost_usd or 0,
		"cost_display": doc.cost_display or 0,
		"display_currency": doc.display_currency,
		"read_only": doc.user != frappe.session.user,
	}
