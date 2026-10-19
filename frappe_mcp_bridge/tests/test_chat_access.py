# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

import frappe

from frappe_mcp_bridge.api import chat as api
from frappe_mcp_bridge.chat import access
from frappe_mcp_bridge.tests.chat_utils import (
	ChatTestCase,
	FakeProvider,
	chat_settings,
	contained,
	make_model,
	make_user,
	provider,
	reply,
)
from frappe_mcp_bridge.tests.utils import as_user

ALICE = "ai-chat-alice@example.com"
BOB = "ai-chat-bob@example.com"
MANAGER = "ai-chat-manager@example.com"
SYSADMIN = "ai-chat-sysadmin@example.com"
OUTSIDER = "ai-chat-outsider@example.com"


class TestChatAccess(ChatTestCase):
	def setUp(self):
		super().setUp()
		make_user(ALICE, "AI Chat User")
		make_user(BOB, "AI Chat User")
		make_user(MANAGER, "AI Chat Manager")
		make_user(SYSADMIN, "System Manager")
		make_user(OUTSIDER, "AI Chat Test Outsider")
		self.model = make_model()

	def chat_as(self, user, text="hello"):
		with as_user(user), contained(), provider(FakeProvider(reply("hi there"))):
			conversation = api.create_conversation(model=self.model)
			api.send_message(conversation["name"], text)
		return conversation["name"]

	def test_status_needs_the_switch_the_role_and_no_block(self):
		with chat_settings(enabled=0):
			self.assertFalse(access.chat_status(ALICE)[0])

		with chat_settings():
			self.assertTrue(access.chat_status(ALICE)[0])
			self.assertTrue(access.chat_status(MANAGER)[0])
			self.assertFalse(access.chat_status(OUTSIDER)[0])
			# System Manager alone is not enough: chat is opt-in per user.
			self.assertFalse(access.chat_status(SYSADMIN)[0])
			self.assertFalse(access.chat_status("Guest")[0])

		with chat_settings(user_overrides=[{"user": ALICE, "blocked": 1}]):
			allowed, reason = access.chat_status(ALICE)
			self.assertFalse(allowed)
			self.assertIn("blocked", reason)

	def test_the_workspace_shows_only_while_ai_chat_is_on(self):
		if not frappe.db.exists("Workspace", access.WORKSPACE):
			self.skipTest("the AI Assistant workspace is not installed")

		access.sync_workspace(False)
		self.assertEqual(frappe.db.get_value("Workspace", access.WORKSPACE, "is_hidden"), 1)
		access.sync_workspace(True)
		self.assertEqual(frappe.db.get_value("Workspace", access.WORKSPACE, "is_hidden"), 0)

	def test_every_endpoint_refuses_a_user_without_the_role(self):
		with chat_settings():
			name = self.chat_as(ALICE)

			with as_user(OUTSIDER):
				for call in (
					lambda: api.bootstrap(),
					lambda: api.list_conversations(),
					lambda: api.create_conversation(model=self.model),
					lambda: api.get_conversation(name),
					lambda: api.send_message(name, "hi"),
					lambda: api.my_usage(),
				):
					self.assertRaises(access.ChatAccessDenied, call)

	def test_models_follow_their_allowed_roles(self):
		restricted = make_model("Restricted Model", allowed_roles=["AI Chat Test Restricted"])

		with chat_settings(), as_user(ALICE):
			names = [model["name"] for model in api.bootstrap()["models"]]
			self.assertIn(self.model, names)
			self.assertNotIn(restricted, names)
			self.assertRaises(access.ChatAccessDenied, api.create_conversation, model=restricted)

	def test_users_see_only_their_own_chats(self):
		with chat_settings():
			name = self.chat_as(ALICE)

			with as_user(BOB):
				self.assertRaises(access.ChatAccessDenied, api.get_conversation, name)
				self.assertNotIn(name, [row.name for row in api.list_conversations()])
				# Frappe's own list API is narrowed too, not only the chat endpoints.
				self.assertNotIn(name, frappe.get_list("AI Chat Conversation", pluck="name"))
				self.assertFalse(frappe.get_list("AI Chat Message", filters={"conversation": name}))
				self.assertRaises(access.ChatAccessDenied, api.list_conversations, user=ALICE)

			with as_user(ALICE):
				self.assertIn(name, frappe.get_list("AI Chat Conversation", pluck="name"))

	def test_managers_read_everything_but_change_nothing(self):
		with chat_settings():
			name = self.chat_as(ALICE)

			with as_user(MANAGER):
				data = api.get_conversation(name)
				self.assertTrue(data["conversation"]["read_only"])
				self.assertIn(name, [row.name for row in api.list_conversations(user="all")])
				self.assertRaises(access.ChatAccessDenied, api.send_message, name, "sneaky")
				self.assertRaises(access.ChatAccessDenied, api.rename_conversation, name, "mine now")

				doc = frappe.get_doc("AI Chat Conversation", name)
				self.assertTrue(doc.has_permission("read"))
				self.assertFalse(doc.has_permission("write"))

	def test_only_the_administrator_can_delete_a_chat(self):
		with chat_settings():
			name = self.chat_as(ALICE)

		messages = frappe.get_all("AI Chat Message", filters={"conversation": name}, pluck="name")
		self.assertTrue(messages)

		for user in (ALICE, MANAGER, SYSADMIN):
			with as_user(user):
				self.assertRaises(frappe.PermissionError, frappe.delete_doc, "AI Chat Conversation", name)
				# Even with permission checks skipped, the controller still refuses.
				self.assertRaises(
					frappe.PermissionError,
					frappe.delete_doc,
					"AI Chat Conversation",
					name,
					ignore_permissions=True,
				)

		frappe.delete_doc("AI Chat Conversation", name)
		self.assertFalse(frappe.db.exists("AI Chat Conversation", name))
		self.assertFalse(frappe.db.exists("AI Chat Message", {"conversation": name}))
		# Every removed record stays restorable.
		self.assertTrue(
			frappe.db.exists(
				"Deleted Document", {"deleted_doctype": "AI Chat Message", "deleted_name": messages[0]}
			)
		)

	def test_messages_can_never_be_edited(self):
		with chat_settings():
			name = self.chat_as(ALICE)

		message = frappe.get_doc("AI Chat Message", {"conversation": name, "role": "User"})
		message.content = "something I never said"
		self.assertRaises(frappe.PermissionError, message.save, ignore_permissions=True)

	def test_ai_chat_records_are_out_of_reach_of_mcp_tools(self):
		from frappe_mcp_bridge.frappe_mcp_bridge.doctype.mcp_bridge_settings.mcp_bridge_settings import (
			ALWAYS_BLOCKED_DOCTYPES,
		)

		for doctype in ("AI Provider", "AI Chat Settings", "AI Chat Conversation", "AI Chat Message"):
			self.assertIn(doctype, ALWAYS_BLOCKED_DOCTYPES)
