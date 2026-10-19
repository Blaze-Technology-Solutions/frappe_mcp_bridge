# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Site data for the browser tests in e2e/: a chat user, a model served by e2e/fake_openai.py,
and settings that let the chat read and propose changes. Run with bench execute:

	bench --site <site> execute frappe_mcp_bridge.tests.e2e_fixtures.setup --kwargs "{'password': '…'}"
	bench --site <site> execute frappe_mcp_bridge.tests.e2e_fixtures.teardown

It creates a System Manager who signs in with the password given, so it refuses to run on a
site that allows neither tests nor developer mode. Teardown puts the settings back as they were.
"""

import json

import frappe
from frappe.utils.password import update_password

USER = "ai-chat-e2e@example.com"
PROVIDER = "E2E Fake"
MODEL = "E2E Fake Model"
REPORT = "E2E ToDos"
SAVED = "frappe_mcp_bridge:e2e:saved_settings"

CHAT_FIELDS = ("enabled", "default_model", "erp_access", "allow_artifacts", "allow_attachments")
MCP_FIELDS = ("enabled", "read_only_mode", "allow_read", "allow_write", "writes_allowed_until")


def setup(password: str, fake_url: str = "http://127.0.0.1:18080/v1") -> dict:
	_refuse_on_live_sites()
	frappe.set_user("Administrator")

	chat = frappe.get_single("AI Chat Settings")
	mcp = frappe.get_single("MCP Bridge Settings")
	# Kept in the database, not the cache: saving the settings clears caches.
	if not frappe.db.get_global(SAVED):
		saved = {
			"chat": {field: chat.get(field) for field in CHAT_FIELDS},
			"mcp": {field: mcp.get(field) for field in MCP_FIELDS},
			"since": str(frappe.utils.now_datetime()),
		}
		frappe.db.set_global(SAVED, json.dumps(saved, default=str))

	mcp.update(
		{"enabled": 1, "read_only_mode": 0, "allow_read": 1, "allow_write": 1, "writes_allowed_until": None}
	)
	mcp.flags.ignore_permissions = True
	mcp.save()

	if not frappe.db.exists("User", USER):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": USER,
				"first_name": "E2E",
				"send_welcome_email": 0,
				# System Manager: MCP's default allowed role, so the chat may read and propose changes.
				"roles": [{"role": "AI Chat User"}, {"role": "System Manager"}],
			}
		).insert(ignore_permissions=True)
	update_password(USER, password)

	if not frappe.db.exists("AI Provider", PROVIDER):
		frappe.get_doc(
			{
				"doctype": "AI Provider",
				"provider_name": PROVIDER,
				"provider_type": "Custom (OpenAI-compatible)",
				"base_url": fake_url,
			}
		).insert(ignore_permissions=True)
	if not frappe.db.exists("AI Model", MODEL):
		frappe.get_doc(
			{
				"doctype": "AI Model",
				"model_label": MODEL,
				"provider": PROVIDER,
				"model_id": "e2e-fake-model",
				"enabled": 1,
				"input_price": 3,
				"output_price": 15,
				"may_read_erp_data": 1,
				"supports_tools": 1,
			}
		).insert(ignore_permissions=True)

	# A report to open the chat from: the E2E user's own ToDos.
	if not frappe.db.exists("Report", REPORT):
		frappe.get_doc(
			{
				"doctype": "Report",
				"report_name": REPORT,
				"ref_doctype": "ToDo",
				"report_type": "Query Report",
				"is_standard": "No",
				"query": f"select name, description from `tabToDo` where owner = '{USER}'",
				"roles": [{"role": "System Manager"}],
			}
		).insert(ignore_permissions=True)

	chat.update(
		{
			"enabled": 1,
			"default_model": MODEL,
			"erp_access": "Read and write with approval",
			"allow_artifacts": 1,
			"allow_attachments": 1,
		}
	)
	chat.flags.ignore_permissions = True
	chat.save()

	frappe.db.commit()
	return {"user": USER, "model": MODEL}


def teardown() -> dict:
	_refuse_on_live_sites()
	frappe.set_user("Administrator")
	started = frappe.utils.now_datetime()
	removed = {}

	# The Administrator is the only one who may delete chats; their messages, approvals,
	# artifacts and files go with them.
	chats = frappe.get_all("AI Chat Conversation", filters={"user": USER}, pluck="name")
	for name in chats:
		frappe.delete_doc("AI Chat Conversation", name, force=True, delete_permanently=True)
	removed["chats"] = len(chats)

	for doctype, filters in (
		("MCP Bridge Log", {"user": USER}),
		("ToDo", {"owner": USER}),
		("Note", {"owner": USER}),
	):
		names = frappe.get_all(doctype, filters=filters, pluck="name")
		for name in names:
			frappe.delete_doc(doctype, name, force=True, delete_permanently=True)
		removed[doctype] = len(names)

	saved = frappe.db.get_global(SAVED)
	if saved:
		saved = json.loads(saved)
		for doctype, values in (("AI Chat Settings", saved["chat"]), ("MCP Bridge Settings", saved["mcp"])):
			doc = frappe.get_single(doctype)
			doc.update(values)
			doc.flags.ignore_permissions = True
			# The saved default model may be the one removed below; clear it first.
			if doctype == "AI Chat Settings" and doc.default_model == MODEL:
				doc.default_model = None
			doc.save()
		# The settings' own change history from setup to here goes too, this restore included.
		frappe.db.delete(
			"Version",
			{
				"ref_doctype": ("in", ["AI Chat Settings", "MCP Bridge Settings"]),
				"creation": (">=", saved["since"]),
			},
		)
		frappe.db.set_global(SAVED, None)

	for doctype, name in (("Report", REPORT), ("AI Model", MODEL), ("AI Provider", PROVIDER), ("User", USER)):
		if frappe.db.exists(doctype, name):
			frappe.delete_doc(doctype, name, force=True, delete_permanently=True)

	_remove_traces(started)
	frappe.db.commit()
	return removed


def _remove_traces(started) -> None:
	"""What the run left in Frappe's own records: copies of the chat records deleted with
	their chats, the comments deletions add, the user's sign-ins and edits."""
	frappe.db.sql(
		"""delete from `tabDeleted Document` where creation >= %s and data like %s""",
		(started, f"%{USER}%"),
	)
	frappe.db.delete(
		"Comment",
		{
			"creation": (">=", started),
			"comment_type": "Deleted",
			"reference_doctype": ("in", ["AI Chat Conversation", "ToDo", "Note", "Report", "User"]),
		},
	)
	frappe.db.delete("Comment", {"owner": USER})
	frappe.db.delete("Activity Log", {"user": USER})
	frappe.db.delete("Version", {"owner": USER})


def _refuse_on_live_sites() -> None:
	if not (frappe.conf.allow_tests or frappe.conf.developer_mode):
		frappe.throw("The browser test fixtures run only on sites with allow_tests or developer_mode set.")
