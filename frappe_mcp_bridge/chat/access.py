# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Who may chat, which chats they may see, and which models they may pick.

Every endpoint checks here again, so hiding the page is never the only thing standing
between a user and someone else's chat. The permission hooks at the bottom apply the
same rules to Frappe's own list views, reports and REST API.
"""

import frappe
from frappe import _

ROLE_USER = "AI Chat User"
ROLE_MANAGER = "AI Chat Manager"
CHAT_ROLES = {ROLE_USER, ROLE_MANAGER}
ADMINISTRATOR = "Administrator"
WORKSPACE = "AI Assistant"

# Doctypes whose rows each belong to one user through their `user` field.
OWNED_DOCTYPES = (
	"AI Chat Conversation",
	"AI Chat Message",
	"AI Chat Action",
	"AI Chat Budget Alert",
	"AI Chat Artifact",
	"AI Chat Feedback",
)

# What a manager may do with someone else's chat: look, never touch.
READ_PTYPES = {"read", "report", "export", "print"}


class ChatAccessDenied(frappe.PermissionError):
	pass


def get_chat_settings():
	return frappe.get_cached_doc("AI Chat Settings")


def visible_templates(doctype: str | None = None) -> list[dict]:
	"""Enabled AI Prompt Templates the user's roles allow, for doctype if given. AI Chat lists
	them in the composer; MCP clients get them as prompts."""
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


def sync_workspace(enabled) -> None:
	"""Show the AI Assistant workspace only while AI Chat is on. Workspace Managers still see
	it, marked hidden, as Frappe shows them every hidden workspace."""
	hidden = 0 if enabled else 1
	if not frappe.db.exists("Workspace", WORKSPACE):
		return
	if frappe.db.get_value("Workspace", WORKSPACE, "is_hidden") != hidden:
		# Left unmodified, so migrate keeps it; a newer workspace file resets it, which
		# sync_workspace_after_migrate puts right.
		frappe.db.set_value("Workspace", WORKSPACE, "is_hidden", hidden, update_modified=False)
		# Only what the sidebar is built from: clearing every cache would slow a busy site.
		frappe.clear_cache(doctype="Workspace")
		frappe.cache.delete_key("bootinfo")


def sync_workspace_after_migrate() -> None:
	sync_workspace(frappe.db.get_single_value("AI Chat Settings", "enabled"))


def user_override(settings, user: str):
	return next((row for row in settings.user_overrides or [] if row.user == user), None)


def chat_status(user: str | None = None) -> tuple[bool, str]:
	"""(allowed, reason). The reason is written for the user it refuses."""
	user = user or frappe.session.user
	if user == "Guest":
		return False, _("Sign in to use AI Chat.")

	settings = get_chat_settings()
	if not settings.enabled:
		return False, _("AI Chat is switched off in AI Chat Settings.")

	if not CHAT_ROLES & set(frappe.get_roles(user)):
		return False, _("You need the AI Chat User role to use AI Chat.")

	override = user_override(settings, user)
	if override and override.blocked:
		return False, _("AI Chat is blocked for you in AI Chat Settings.")

	return True, ""


def require_chat_access(user: str | None = None) -> None:
	allowed, reason = chat_status(user)
	if not allowed:
		raise ChatAccessDenied(reason)


def is_manager(user: str | None = None) -> bool:
	user = user or frappe.session.user
	return user == ADMINISTRATOR or ROLE_MANAGER in frappe.get_roles(user)


def get_conversation(name: str, *, for_write: bool = False):
	"""The chat, if this user may see it. Managers may read anyone's, but change only their own."""
	if not name or not frappe.db.exists("AI Chat Conversation", name):
		raise frappe.DoesNotExistError(_("That chat does not exist."))

	doc = frappe.get_doc("AI Chat Conversation", name)
	user = frappe.session.user

	if doc.user == user or (not for_write and is_manager(user)):
		return doc

	raise ChatAccessDenied(_("This chat belongs to someone else."))


# Models --------------------------------------------------------------------------------


def model_allowed(model, user: str | None = None) -> bool:
	if not model.enabled:
		return False

	if not frappe.db.get_value("AI Provider", model.provider, "enabled"):
		return False

	roles = {row.role for row in model.allowed_roles or []}
	return not roles or bool(roles & set(frappe.get_roles(user or frappe.session.user)))


def allowed_models(user: str | None = None) -> list:
	names = frappe.get_all("AI Model", filters={"enabled": 1}, pluck="name", order_by="model_label asc")
	models = [frappe.get_cached_doc("AI Model", name) for name in names]
	return [model for model in models if model_allowed(model, user)]


def require_model(name: str | None, user: str | None = None):
	if not name or not frappe.db.exists("AI Model", name):
		raise ChatAccessDenied(_("Pick a model to chat with."))

	model = frappe.get_cached_doc("AI Model", name)
	if not model_allowed(model, user):
		raise ChatAccessDenied(_("You may not use the model {0}.").format(model.model_label))

	return model


def may_share_company_data(settings, model) -> bool:
	"""Whether site data (tools, form context, attachments) may go to this model at all."""
	return (settings.erp_access or "Off") != "Off" and bool(model.may_read_erp_data)


def default_model(user: str | None = None):
	models = allowed_models(user)
	preferred = get_chat_settings().default_model
	return next((model for model in models if model.name == preferred), models[0] if models else None)


# Deletion ------------------------------------------------------------------------------


def only_administrator_deletes(doc) -> None:
	"""on_trash for every chat record: the audit trail is the Administrator's alone to remove."""
	if frappe.session.user != ADMINISTRATOR:
		frappe.throw(
			_("Only the Administrator can delete {0} records.").format(_(doc.doctype)), frappe.PermissionError
		)


# Frappe permission hooks ---------------------------------------------------------------


def _owned_condition(doctype: str, user: str | None) -> str:
	user = user or frappe.session.user
	if is_manager(user):
		return ""

	return f"`tab{doctype}`.`user` = {frappe.db.escape(user)}"


def conversation_conditions(user=None):
	return _owned_condition("AI Chat Conversation", user)


def message_conditions(user=None):
	return _owned_condition("AI Chat Message", user)


def action_conditions(user=None):
	return _owned_condition("AI Chat Action", user)


def budget_alert_conditions(user=None):
	return _owned_condition("AI Chat Budget Alert", user)


def artifact_conditions(user=None):
	return _owned_condition("AI Chat Artifact", user)


def feedback_conditions(user=None):
	return _owned_condition("AI Chat Feedback", user)


def has_owned_permission(doc, ptype=None, user=None, debug=False):
	"""Narrows the role permissions in the doctype JSON; never widens them."""
	user = user or frappe.session.user

	if ptype == "delete":
		return user == ADMINISTRATOR

	if user == ADMINISTRATOR or doc.get("user") == user:
		return True

	return is_manager(user) and (ptype or "read") in READ_PTYPES


def boot_session(bootinfo):
	"""Tells the desk whether to show the Ask AI button, and on which doctypes. Never breaks boot."""
	try:
		allowed, _reason = chat_status()
		bootinfo.ai_chat = {"can_use": allowed, "is_manager": allowed and is_manager()}
		if allowed:
			# Ask AI exists to send the open document, so it goes when site data may not.
			bootinfo.ai_chat["erp"] = (get_chat_settings().erp_access or "Off") != "Off"
			bootinfo.ai_chat.update(_doctype_lists())
	except Exception:
		bootinfo.ai_chat = {"can_use": False, "is_manager": False}


def _doctype_lists() -> dict:
	from frappe_mcp_bridge.frappe_mcp_bridge.doctype.mcp_bridge_settings.mcp_bridge_settings import (
		ALWAYS_BLOCKED_DOCTYPES,
		get_settings,
	)

	mcp = get_settings()
	blocked = set(ALWAYS_BLOCKED_DOCTYPES) | {row.document_type for row in mcp.blocked_doctypes or []}
	allowed = (
		sorted({row.document_type for row in mcp.allowed_doctypes or []}) if mcp.restrict_doctypes else None
	)
	return {"blocked_doctypes": sorted(blocked), "allowed_doctypes": allowed}
