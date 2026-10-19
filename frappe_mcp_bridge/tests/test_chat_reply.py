# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

import base64
import json

import frappe
from frappe.utils import add_to_date, now_datetime

from frappe_mcp_bridge.api import chat as api
from frappe_mcp_bridge.chat import access, attachments, orchestrator
from frappe_mcp_bridge.chat.providers.base import ProviderError
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
from frappe_mcp_bridge.tests.utils import as_user, make_todo, mcp_settings

USER = "ai-chat-reply@example.com"
OTHER = "ai-chat-reply-other@example.com"

# A real image, because Frappe opens uploaded images to strip their EXIF data.
ONE_PIXEL_PNG = (
	"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def call(tool, **args):
	return {"id": f"call_{tool}", "name": tool, "args": args}


class TestChatReply(ChatTestCase):
	def setUp(self):
		super().setUp()
		# System Manager is MCP's default allowed role, so this user may use ERP tools.
		make_user(USER, "AI Chat User", "System Manager")
		make_user(OTHER, "AI Chat User", "System Manager")
		self.model = make_model()

	def start(self, model=None, **kwargs):
		return api.create_conversation(model=model or self.model, **kwargs)["name"]

	def rows(self, conversation, role=None):
		filters = {"conversation": conversation}
		if role:
			filters["role"] = role
		return frappe.get_all("AI Chat Message", filters=filters, fields=["*"], order_by="seq asc")

	def test_a_plain_reply_is_streamed_stored_and_the_chat_freed(self):
		fake = FakeProvider(reply("Hello! How can I help?"))
		with chat_settings(), as_user(USER), contained() as events, provider(fake):
			name = self.start()
			api.send_message(name, "Hi there, what can you do for our team?")

		roles = [row.role for row in self.rows(name)]
		self.assertEqual(roles, ["User", "Assistant"])

		chat = frappe.get_doc("AI Chat Conversation", name)
		self.assertEqual(chat.status, "Active")
		self.assertEqual(chat.title, "Hi there, what can you do for our team?")
		self.assertEqual(chat.message_count, 2)

		kinds = [message["type"] for event, message in events if event == orchestrator.EVENT]
		self.assertIn("delta", kinds)
		self.assertEqual(kinds[-1], "done")

		request = fake.requests[0]
		self.assertTrue(request.messages[-1].content.startswith("(Sent "))
		self.assertTrue(request.messages[-1].content.endswith("Hi there, what can you do for our team?"))
		self.assertIn(USER, request.system)

	def test_messages_are_stored_and_replayed_exactly_as_written(self):
		# Frappe rewrites HTML in text fields on save; a chat about markup must survive it.
		question = 'Why does <div onclick="go()"> fail when a < b?'
		answer = 'Because:\n\n```html\n<div class="x" onclick="go()">hi</div>\n<script>run()</script>\n```'
		fake = FakeProvider(reply(answer), reply("Anything else?"))
		with chat_settings(), as_user(USER), contained(), provider(fake):
			name = self.start()
			api.send_message(name, question)
			api.send_message(name, "Thanks")

		rows = self.rows(name)
		self.assertTrue(rows[0].content.endswith(question))
		self.assertEqual(rows[1].content, answer)
		self.assertEqual(fake.requests[1].messages[1].content, answer)

	def test_read_tools_run_as_the_user_and_are_logged(self):
		todo = make_todo("check the warehouse")
		fake = FakeProvider(
			reply(tool_calls=[call("get_document", doctype="ToDo", name=todo)]),
			reply("It says: check the warehouse."),
		)
		with mcp_settings(log_requests=1), chat_settings(), as_user(USER), contained(), provider(fake):
			name = self.start()
			api.send_message(name, f"What does ToDo {todo} say?")

		tool_row = self.rows(name, "Tool")[0]
		self.assertFalse(tool_row.is_error, tool_row.content)
		self.assertIn("check the warehouse", tool_row.content)
		log = frappe.get_doc("MCP Bridge Log", tool_row.mcp_bridge_log)
		self.assertEqual((log.tool, log.user, log.client), ("get_document", USER, "AI Chat"))

		# The second model call saw the tool result.
		second = fake.requests[1].messages
		self.assertEqual(second[-1].role, "tool")
		self.assertEqual(second[-1].tool_call_id, "call_get_document")

	def test_models_without_erp_access_get_no_tools(self):
		model = make_model("Outside Model", may_read_erp_data=0)
		fake = FakeProvider(reply("I can't see your data."))
		with mcp_settings(), chat_settings(), as_user(USER), contained(), provider(fake):
			api.send_message(self.start(model), "List my ToDos")

		# Only the artifact tool, which reads nothing.
		self.assertEqual([tool.name for tool in fake.requests[0].tools], ["create_artifact"])
		self.assertIn("cannot read this site's data", fake.requests[0].system)

	def test_tools_follow_the_access_mode_and_the_mcp_gate(self):
		with mcp_settings(read_only_mode=0, allow_write=1), as_user(USER):
			model = frappe.get_doc("AI Model", self.model)

			with chat_settings(erp_access="Read only") as settings:
				names = orchestrator.tools.tool_access(settings, model, USER).names
			self.assertIn("get_document", names)
			self.assertNotIn("update_document", names)

			with chat_settings() as settings:
				names = orchestrator.tools.tool_access(settings, model, USER).names
			self.assertIn("update_document", names)
			# Never offered in chat, whatever MCP Bridge Settings allows.
			self.assertFalse(names & {"run_server_script", "run_patch", "create_backup"})

		with mcp_settings(enabled=0), chat_settings() as settings:
			self.assertFalse(orchestrator.tools.tool_access(settings, model, USER).erp)

	def write_and_wait(self, todo):
		fake = FakeProvider(
			reply(
				tool_calls=[
					call(
						"update_document", doctype="ToDo", name=todo, values={"description": "approved text"}
					)
				]
			),
			reply("That would change the description. Please approve it."),
		)
		with provider(fake):
			name = self.start()
			api.send_message(name, f"Change ToDo {todo}")
		return name, fake

	def test_writes_wait_for_approval_and_then_run_for_real(self):
		todo = make_todo("original text")
		with (
			mcp_settings(read_only_mode=0, allow_write=1, log_requests=1),
			chat_settings(),
			as_user(USER),
			contained(),
		):
			name, _fake = self.write_and_wait(todo)

			action = frappe.get_doc("AI Chat Action", {"conversation": name})
			self.assertEqual(action.status, "Pending")
			self.assertIn("approved text", action.preview)
			# The dry run rolled itself back.
			self.assertEqual(frappe.db.get_value("ToDo", todo, "description"), "original text")
			self.assertIn("NOT SAVED YET", self.rows(name, "Tool")[0].content)

			with provider(FakeProvider(reply("Done: the description is updated."))) as follow_up:
				result = api.decide_action(action.name, approve=1)

		self.assertEqual(result["status"], "Approved")
		self.assertEqual(frappe.db.get_value("ToDo", todo, "description"), "approved text")
		action.reload()
		self.assertEqual((action.status, action.decided_by), ("Approved", USER))
		self.assertTrue(action.result_log)

		# The model was told, in a note from the user, what was saved.
		note = follow_up.requests[0].messages[-1]
		self.assertEqual(note.role, "user")
		self.assertIn("It has been saved", note.content)

	def test_a_rejected_change_is_never_made(self):
		todo = make_todo("keep me")
		with mcp_settings(read_only_mode=0, allow_write=1), chat_settings(), as_user(USER), contained():
			name, _fake = self.write_and_wait(todo)
			action = frappe.db.get_value("AI Chat Action", {"conversation": name}, "name")
			result = api.decide_action(action, approve=0)

		self.assertEqual(result["status"], "Rejected")
		self.assertFalse(result["running"])
		self.assertEqual(frappe.db.get_value("ToDo", todo, "description"), "keep me")

	def test_only_the_chat_owner_can_approve_and_not_after_expiry(self):
		todo = make_todo("guarded")
		with mcp_settings(read_only_mode=0, allow_write=1), chat_settings(), contained():
			with as_user(USER):
				name, _fake = self.write_and_wait(todo)
			action = frappe.db.get_value("AI Chat Action", {"conversation": name}, "name")

			with as_user(OTHER):
				self.assertRaises(access.ChatAccessDenied, api.decide_action, action, approve=1)

			frappe.db.set_value(
				"AI Chat Action", action, "expires_at", add_to_date(now_datetime(), minutes=-1)
			)
			with as_user(USER):
				self.assertRaises(frappe.ValidationError, api.decide_action, action, approve=1)

		self.assertEqual(frappe.db.get_value("AI Chat Action", action, "status"), "Expired")
		self.assertEqual(frappe.db.get_value("ToDo", todo, "description"), "guarded")

	def test_secrets_are_removed_before_storing_and_sending(self):
		fake = FakeProvider(reply("Noted."))
		key = "sk-ant-api03-" + "a" * 40
		with chat_settings(), as_user(USER), contained(), provider(fake):
			name = self.start()
			result = api.send_message(name, f"My key is {key}, keep it safe")

		self.assertIn("Anthropic API key", result["removed"])
		stored = self.rows(name, "User")[0]
		self.assertNotIn(key, stored.content)
		self.assertNotIn(key, fake.requests[0].messages[-1].content)

	def test_secrets_in_attachments_and_the_opened_document_are_removed_before_sending(self):
		key = "sk-ant-api03-" + "b" * 40
		todo = make_todo("Server login, password: hunter2-xyz")
		fake = FakeProvider(reply("Noted."))
		with mcp_settings(), chat_settings() as settings, as_user(USER), contained(), provider(fake):
			name = self.start(context_doctype="ToDo", context_name=todo)
			upload = attachments.save_upload(
				name, "notes.txt", f"deploy key {key}\nitem,price".encode(), settings
			)
			result = api.send_message(name, "Read these", attachment_names=json.dumps([upload["name"]]))

		# The upload says what will be taken out, so the user knows before sending.
		self.assertEqual(upload["removed"], ["Anthropic API key"])
		self.assertIn("password", result["removed"])

		sent = fake.requests[0].messages[-1]
		self.assertIn(f"ToDo {todo}", sent.content)
		self.assertNotIn("hunter2", sent.content)
		self.assertNotIn(key, sent.parts[0]["data"])
		self.assertIn("[API key removed]", sent.parts[0]["data"])

		stored = self.rows(name, "User")[0]
		self.assertNotIn("hunter2", stored.context_text)
		self.assertIn("password", stored.redacted)
		# The file itself stays as the user uploaded it; only what is sent is cleaned.
		content = frappe.get_doc("File", upload["name"]).get_content()
		self.assertIn(key, content if isinstance(content, str) else content.decode())

	def test_markup_in_a_file_name_is_taken_out_before_saving(self):
		# Frappe would rewrite it on save, turning "x.txt" into "x.txt</b>", which no model can read.
		with chat_settings() as settings, as_user(USER), contained():
			name = self.start()
			upload = attachments.save_upload(name, "notes <b>x</_b>.txt", b"a,b", settings)
			stored = frappe.db.get_value("File", upload["name"], "file_name")
			model = frappe.get_doc("AI Model", self.model)
			kept = attachments.validate(name, [upload["name"]], settings, model)

		self.assertNotIn("<", stored)
		self.assertTrue(stored.endswith(".txt"), stored)
		self.assertEqual(kept[0]["kind"], "text")

	def test_chats_never_used_are_hidden_and_cleared_after_a_day(self):
		fake = FakeProvider(reply("Hello."))
		with chat_settings() as settings, as_user(USER), contained(), provider(fake):
			empty, used, fresh = self.start(), self.start(), self.start()
			# A file attached and the tab closed: the chat never got a message.
			upload = attachments.save_upload(empty, "draft.csv", b"a,b", settings)
			api.send_message(used, "Hi")
			listed = [row.name for row in api.list_conversations()]
		self.assertEqual(listed, [used])

		day_old = add_to_date(now_datetime(), days=-2)
		for name in (empty, used):
			frappe.db.set_value("AI Chat Conversation", name, "creation", day_old, update_modified=False)

		orchestrator.remove_empty_chats()
		self.assertFalse(frappe.db.exists("AI Chat Conversation", empty))
		self.assertFalse(frappe.db.exists("File", upload["name"]))
		self.assertTrue(frappe.db.exists("AI Chat Conversation", used))
		self.assertTrue(frappe.db.exists("AI Chat Conversation", fresh))

	def test_a_failing_provider_falls_back_to_the_next_model(self):
		backup = make_model("Backup Model")
		frappe.db.set_value("AI Model", self.model, "fallback_model", backup)
		frappe.clear_document_cache("AI Model", self.model)

		fake = FakeProvider(ProviderError("Provider returned 529: overloaded"), reply("Answer from backup."))
		with chat_settings(), as_user(USER), contained(), provider(fake):
			name = self.start()
			api.send_message(name, "Hello?")

		assistant = self.rows(name, "Assistant")[0]
		self.assertEqual(assistant.model, backup)
		self.assertIn("Trying Backup Model", self.rows(name, "Notice")[0].content)

	def test_a_failure_with_no_fallback_leaves_a_notice_and_frees_the_chat(self):
		fake = FakeProvider(ProviderError("Provider returned 401: invalid key"))
		with chat_settings(), as_user(USER), contained(), provider(fake):
			name = self.start()
			api.send_message(name, "Hello?")

		notice = self.rows(name, "Notice")[0]
		self.assertTrue(notice.is_error)
		self.assertIn("invalid key", notice.content)
		self.assertEqual(frappe.db.get_value("AI Chat Conversation", name, "status"), "Active")

	def test_tool_rounds_stop_at_the_limit(self):
		todo = make_todo()
		lookup = reply(tool_calls=[call("get_document", doctype="ToDo", name=todo)])
		fake = FakeProvider(lookup, lookup)
		with mcp_settings(), chat_settings(max_tool_rounds=2), as_user(USER), contained(), provider(fake):
			name = self.start()
			api.send_message(name, "Keep looking")

		self.assertEqual(len(fake.requests), 2)
		self.assertIn("Stopped after 2 rounds", self.rows(name, "Notice")[0].content)

	def test_the_opened_document_goes_with_the_first_message_masked(self):
		todo = make_todo("secret plans")
		fake = FakeProvider(reply("It is about secret plans."), reply("Still here."))
		with (
			mcp_settings(masked=[("ToDo", "description")]),
			chat_settings(),
			as_user(USER),
			contained(),
			provider(fake),
		):
			name = self.start(context_doctype="ToDo", context_name=todo)
			api.send_message(name, "Summarise this")
			api.send_message(name, "And again")

		first = fake.requests[0].messages[0].content
		self.assertIn(f"ToDo {todo}", first)
		self.assertIn("•••", first)
		self.assertNotIn("secret plans", first)
		# Context rides on the first message only, and later turns resend it unchanged.
		self.assertEqual(fake.requests[1].messages[0].content, first)
		self.assertEqual(len([m for m in fake.requests[1].messages if "ToDo " + todo in m.content]), 1)

	def test_an_interrupted_tool_call_is_answered_before_the_next_turn(self):
		rows = [
			frappe._dict(role="User", content="look it up"),
			frappe._dict(
				role="Assistant",
				content="",
				tool_calls=json.dumps([{"id": "call_1", "name": "get_document", "args": {}}]),
			),
			frappe._dict(role="User", content="hello?"),
		]
		model = frappe.get_doc("AI Model", self.model)
		messages = orchestrator.build_messages(rows, model, {})

		self.assertEqual([m.role for m in messages], ["user", "assistant", "tool", "user"])
		self.assertEqual(messages[2].tool_call_id, "call_1")
		self.assertTrue(messages[2].is_error)

	def test_attachments_must_belong_to_the_chat_and_suit_the_model(self):
		with chat_settings() as settings, as_user(USER), contained():
			name = self.start()
			note = attachments.save_upload(name, "prices.csv", b"item,price\nwidget,4", settings)
			image = attachments.save_upload(name, "photo.png", base64.b64decode(ONE_PIXEL_PNG), settings)
			self.assertTrue(frappe.db.get_value("File", note["name"], "is_private"))
			self.assertRaises(
				frappe.ValidationError, attachments.save_upload, name, "tool.exe", b"MZ", settings
			)

			model = frappe.get_doc("AI Model", self.model)
			stored = attachments.validate(name, [note["name"]], settings, model)
			self.assertEqual(stored[0]["kind"], "text")
			# This model can't read images.
			self.assertRaises(
				frappe.ValidationError, attachments.validate, name, [image["name"]], settings, model
			)

			parts, notes = attachments.parts_for(json.dumps(stored), model, {})
			self.assertIn("widget,4", parts[0]["data"])
			self.assertFalse(notes)

			fake = FakeProvider(reply("Widgets cost 4."))
			with provider(fake):
				api.send_message(name, "What do widgets cost?", attachment_names=json.dumps([note["name"]]))
			self.assertEqual(fake.requests[0].messages[-1].parts[0]["type"], "text")

		with chat_settings() as settings, as_user(OTHER):
			# Someone else's upload can't be sent from another chat, nor read.
			self.assertRaises(
				frappe.ValidationError, attachments.validate, name, [note["name"]], settings, model
			)
			self.assertFalse(frappe.get_doc("File", note["name"]).has_permission("read"))

	def test_stop_frees_a_chat_whose_job_is_gone(self):
		with chat_settings(), as_user(USER), contained():
			name = self.start()
			frappe.db.set_value("AI Chat Conversation", name, "status", "Running")
			api.stop(name)

		self.assertEqual(frappe.db.get_value("AI Chat Conversation", name, "status"), "Active")
		self.assertTrue(frappe.cache.get_value(orchestrator.stop_key(name)))
		frappe.cache.delete_value(orchestrator.stop_key(name))
