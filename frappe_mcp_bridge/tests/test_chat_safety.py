# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""What keeps company data, approvals and bills safe as chats run for days and models change."""

from unittest.mock import patch

import frappe

from frappe_mcp_bridge.api import chat as api
from frappe_mcp_bridge.api import mcp as mcp_api
from frappe_mcp_bridge.chat import access, billing, context, orchestrator, tools
from frappe_mcp_bridge.chat.providers.base import ChatResult, StopRequested, Usage
from frappe_mcp_bridge.mcp import registry
from frappe_mcp_bridge.tests.chat_utils import (
	ChatTestCase,
	FakeProvider,
	chat_settings,
	contained,
	make_model,
	make_role,
	make_user,
	provider,
	reply,
)
from frappe_mcp_bridge.tests.utils import as_user, make_todo, mcp_settings

USER = "ai-chat-safety@example.com"
CLERK = "ai-chat-clerk@example.com"


def call(tool, **args):
	return {"id": f"call_{tool}", "name": tool, "args": args}


class TestChatSafety(ChatTestCase):
	def setUp(self):
		super().setUp()
		make_user(USER, "AI Chat User", "System Manager")
		self.model = make_model()

	def start(self, model=None, **kwargs):
		return api.create_conversation(model=model or self.model, **kwargs)["name"]

	def rows(self, conversation, role=None):
		filters = {"conversation": conversation} | ({"role": role} if role else {})
		return frappe.get_all("AI Chat Message", filters=filters, fields=["*"], order_by="seq asc")

	# Dry runs ---------------------------------------------------------------------------

	def test_a_dry_run_leaves_no_after_commit_work_behind(self):
		def handler(dry_run=False):
			frappe.db.after_commit.add(lambda: None)
			frappe.db.before_commit.add(lambda: None)
			return {"ok": True}

		definition = registry.Tool(name="probe", capability="write", handler=handler, writes=True)
		before = (len(frappe.db.after_commit._functions), len(frappe.db.before_commit._functions))
		mcp_api._run(definition, {"dry_run": True}, True)
		after = (len(frappe.db.after_commit._functions), len(frappe.db.before_commit._functions))
		self.assertEqual(before, after)

	def test_deletes_are_previewed_without_running(self):
		# The user's own ToDo, which Frappe lets its owner delete.
		with as_user(USER):
			todo = make_todo("do not delete me yet")
		fake = FakeProvider(
			reply(tool_calls=[call("delete_document", doctype="ToDo", name=todo)]),
			reply("That would delete it. Approve?"),
		)
		with (
			mcp_settings(read_only_mode=0, allow_delete=1),
			chat_settings(),
			as_user(USER),
			contained(),
			provider(fake),
			patch("frappe.delete_doc", side_effect=AssertionError("ran during preview")),
		):
			name = self.start()
			api.send_message(name, f"Delete ToDo {todo}")

		action = frappe.get_doc("AI Chat Action", {"conversation": name})
		self.assertEqual(action.status, "Pending")
		self.assertIn("would be deleted", action.preview)
		self.assertTrue(frappe.db.exists("ToDo", todo))

		with mcp_settings(read_only_mode=0, allow_delete=1), chat_settings(), as_user(USER), contained():
			with provider(FakeProvider(reply("Deleted."))):
				api.decide_action(action.name, approve=1)
		self.assertFalse(frappe.db.exists("ToDo", todo))

	def test_developer_mode_exports_are_previewed_without_running(self):
		with patch("frappe_mcp_bridge.chat.tools.developer_mode", return_value=True):
			self.assertFalse(tools.can_dry_run("update_document", {"doctype": "Workspace"}))
			self.assertTrue(tools.can_dry_run("update_document", {"doctype": "ToDo"}))
		with patch("frappe_mcp_bridge.chat.tools.developer_mode", return_value=False):
			self.assertTrue(tools.can_dry_run("update_document", {"doctype": "Workspace"}))
		self.assertFalse(tools.can_dry_run("delete_document", {"doctype": "ToDo"}))

	# Which tools chat offers ---------------------------------------------------------------

	def test_chat_never_offers_logs_or_raw_sql(self):
		with (
			mcp_settings(read_only_mode=0, allow_write=1, allow_sql_query=1, allow_delete=1),
			chat_settings() as settings,
		):
			names = tools.tool_access(settings, frappe.get_doc("AI Model", self.model), USER).names

		self.assertTrue({"get_document", "update_document", "delete_document"} <= names)
		self.assertFalse(
			names & {"get_mcp_logs", "get_error_logs", "run_sql", "list_patches", "get_patch_log"}
		)

	def test_every_offered_schema_is_complete(self):
		with mcp_settings(read_only_mode=0, allow_write=1, allow_data_import=1), chat_settings() as settings:
			offered = tools.tool_access(settings, frappe.get_doc("AI Model", self.model), USER).tools

		def arrays_without_items(schema, path):
			found = []
			if isinstance(schema, dict):
				if schema.get("type") == "array" and "items" not in schema:
					found.append(path)
				for key, value in schema.items():
					found += arrays_without_items(value, f"{path}.{key}")
			return found

		for tool in offered:
			self.assertEqual(arrays_without_items(tool.input_schema, tool.name), [])

	# Setup frozen per chat ----------------------------------------------------------------

	def test_system_prompt_and_tools_are_frozen_for_the_whole_chat(self):
		todo = make_todo()
		fake = FakeProvider(
			reply("first"), reply(tool_calls=[call("get_document", doctype="ToDo", name=todo)]), reply("ok")
		)
		with chat_settings(), as_user(USER), contained(), provider(fake):
			with mcp_settings():
				name = self.start()
				api.send_message(name, "Hello")
			# MCP is switched off before the next turn: the request must not change shape.
			with mcp_settings(enabled=0):
				api.send_message(name, "Read that ToDo")

		first, second = fake.requests[0], fake.requests[1]
		self.assertEqual(first.system, second.system)
		self.assertEqual([t.name for t in first.tools], [t.name for t in second.tools])
		self.assertTrue(first.tools)
		# ...but the tool itself is refused now.
		self.assertIn("no longer", self.rows(name, "Tool")[0].content)
		# Each message says when it was sent; the system prompt carries no date.
		self.assertIn("Sent ", first.messages[0].content)
		self.assertNotIn(frappe.utils.today()[:4] + "-", first.system)

	# Company data and models --------------------------------------------------------------

	def test_a_chat_holding_company_data_stays_with_models_allowed_to_read_it(self):
		outside = make_model("Outside Model", may_read_erp_data=0)
		todo = make_todo("secret")
		fake = FakeProvider(
			reply(tool_calls=[call("get_document", doctype="ToDo", name=todo)]), reply("done")
		)
		with mcp_settings(), chat_settings(), as_user(USER), contained(), provider(fake):
			name = self.start()
			api.send_message(name, "Read it")
			self.assertRaises(
				access.ChatAccessDenied, api.send_message, name, "Now tell another model", model=outside
			)

	def test_a_fallback_without_data_access_is_skipped(self):
		outside = make_model("Outside Fallback", may_read_erp_data=0)
		doc = frappe.get_doc("AI Model", self.model)
		doc.fallback_model = outside
		doc.save()
		todo = make_todo()
		from frappe_mcp_bridge.chat.providers.base import ProviderError

		fake = FakeProvider(
			reply(tool_calls=[call("get_document", doctype="ToDo", name=todo)]),
			ProviderError("down"),
			reply("An answer the fallback must never give."),
		)
		with mcp_settings(), chat_settings(), as_user(USER), contained(), provider(fake):
			name = self.start()
			api.send_message(name, "Read it")

		self.assertEqual(len(fake.requests), 2)
		self.assertFalse(any(row.model == outside for row in self.rows(name, "Assistant")))
		self.assertIn("down", self.rows(name, "Notice")[-1].content)

	def test_erp_access_off_also_stops_form_context_and_attachments(self):
		todo = make_todo("private plans")
		fake = FakeProvider(reply("ok"))
		with (
			mcp_settings(),
			chat_settings(erp_access="Off") as settings,
			as_user(USER),
			contained(),
			provider(fake),
		):
			name = self.start(context_doctype="ToDo", context_name=todo)
			api.send_message(name, "Summarise")
			self.assertRaises(
				frappe.ValidationError,
				api.attachments.save_upload,
				name,
				"a.csv",
				b"x",
				settings,
			)

		self.assertNotIn("private plans", fake.requests[0].messages[0].content)

	# Approvals ----------------------------------------------------------------------------

	def write_and_wait(self, todo):
		fake = FakeProvider(
			reply(
				tool_calls=[call("update_document", doctype="ToDo", name=todo, values={"description": "new"})]
			),
			reply("Approve?"),
		)
		with provider(fake):
			name = self.start()
			api.send_message(name, "Change it")
		return name, frappe.db.get_value("AI Chat Action", {"conversation": name}, "name")

	def test_an_approval_cannot_run_twice(self):
		todo = make_todo("old")
		with mcp_settings(read_only_mode=0, allow_write=1), chat_settings(), as_user(USER), contained():
			_name, action = self.write_and_wait(todo)
			with provider(FakeProvider(reply("Saved."))):
				api.decide_action(action, approve=1)
			self.assertRaises(frappe.ValidationError, api.decide_action, action, approve=1)

	def test_switching_chat_to_read_only_stops_pending_changes(self):
		todo = make_todo("old")
		with mcp_settings(read_only_mode=0, allow_write=1), as_user(USER), contained():
			with chat_settings():
				_name, action = self.write_and_wait(todo)
			with chat_settings(erp_access="Read only"):
				self.assertRaises(frappe.ValidationError, api.decide_action, action, approve=1)
		self.assertEqual(frappe.db.get_value("ToDo", todo, "description"), "old")

	def test_the_approved_result_reaches_the_model_marked_as_site_data(self):
		todo = make_todo("old")
		with mcp_settings(read_only_mode=0, allow_write=1), chat_settings(), as_user(USER), contained():
			name, action = self.write_and_wait(todo)
			with provider(FakeProvider(reply("Saved."))):
				api.decide_action(action, approve=1)

		note = self.rows(name, "User")[-1]
		self.assertTrue(note.is_note)
		self.assertIn("data returned by the site", note.content)

	# Budgets ------------------------------------------------------------------------------

	def test_the_budget_alert_goes_out_even_when_the_limit_stops_a_reply(self):
		model = make_model("Costly Model", input_price=1000, output_price=1000)
		todo = make_todo()
		fake = FakeProvider(
			reply(
				tool_calls=[call("get_document", doctype="ToDo", name=todo)],
				input_tokens=1000,
				output_tokens=1000,
			)
		)
		with (
			mcp_settings(),
			chat_settings(default_monthly_budget=2),
			as_user(USER),
			contained(),
			provider(fake),
			patch("frappe.sendmail"),
		):
			api.send_message(self.start(model), "Look it up")

		self.assertTrue(frappe.db.exists("AI Chat Budget Alert", {"user": USER, "threshold": 100}))

	def test_a_user_override_of_zero_keeps_the_role_budget(self):
		with chat_settings(
			role_budgets=[{"role": "AI Chat User", "monthly_budget": 10, "daily_messages": 5}],
			user_overrides=[{"user": USER, "daily_messages": 50}],
		) as settings:
			limits = billing.limits_for(USER, settings)
		self.assertEqual((limits.monthly_budget, limits.daily_messages), (10, 50))

	def test_blank_claude_cache_prices_follow_anthropics_multipliers(self):
		model = frappe._dict(is_free=0, provider_type="Anthropic", input_price=3, output_price=15)
		usage = Usage(cached_input_tokens=1_000_000, cache_write_tokens=1_000_000)
		self.assertAlmostEqual(billing.cost_usd(model, usage), 0.3 + 3.75)

	# Stop ---------------------------------------------------------------------------------

	def test_stop_reaches_a_reply_that_is_already_streaming(self):
		class Streaming:
			"""Streams until told to stop, the way a real provider does."""

			conversation = None

			def stream(self, request, on_delta):
				on_delta("first ")
				# What the Stop button does, from another process, mid-reply.
				frappe.cache.set_value(orchestrator.stop_key(self.conversation), 1, expires_in_sec=60)
				try:
					for _ in range(5):
						on_delta("more ")
				except StopRequested:
					return ChatResult(text="first more ", usage=Usage(output_tokens=2), stopped=True)
				return reply("It never stopped.")

		streaming = Streaming()
		with (
			chat_settings(),
			as_user(USER),
			contained(),
			provider(streaming),
			patch("frappe_mcp_bridge.chat.orchestrator.STOP_CHECK_EVERY", 0),
		):
			name = self.start()
			streaming.conversation = name
			try:
				api.send_message(name, "Write a long essay")
			finally:
				frappe.cache.delete_value(orchestrator.stop_key(name))

		answer = self.rows(name, "Assistant")[0]
		self.assertEqual(answer.content, "first more ")
		self.assertEqual(frappe.db.get_value("AI Chat Conversation", name, "status"), "Active")

	# Context limit ------------------------------------------------------------------------

	def test_a_full_chat_continues_in_a_new_one(self):
		small = make_model("Small Model", context_window=10_000)
		fake = FakeProvider(reply("A long answer about stock.", input_tokens=9_000))
		with chat_settings(), as_user(USER), contained(), provider(fake):
			name = self.start(small)
			api.send_message(name, "Tell me about stock")
			self.assertTrue(api.get_conversation(name)["conversation"]["near_limit"])
			self.assertRaises(frappe.ValidationError, api.send_message, name, "More")

			new = api.continue_in_new_chat(name)
			with provider(FakeProvider(reply("Carrying on."))) as follow:
				api.send_message(new["name"], "Go on")

		self.assertEqual(frappe.db.get_value("AI Chat Conversation", new["name"], "continued_from"), name)
		first = follow.requests[0].messages[0].content
		self.assertIn("A long answer about stock.", first)
		self.assertIn("Tell me about stock", first)

	# Templates and form context -----------------------------------------------------------

	def test_templates_fill_fields_and_nothing_else(self):
		todo = make_todo("buy paint")
		text = context.render_prompt(
			"Summarise {{ doc.description }} {{ frappe.db.sql('select 1') }} {% if 1 %}x{% endif %}",
			"ToDo",
			todo,
		)
		self.assertIn("buy paint", text)
		self.assertIn("{{ frappe.db.sql('select 1') }}", text)
		self.assertIn("{% if 1 %}", text)

	def test_form_context_respects_field_permissions(self):
		todo = make_todo("visible")
		with patch("frappe.model.document.Document.apply_fieldlevel_read_permissions") as apply:
			context.masked_document("ToDo", todo)
		apply.assert_called_once()

	def test_a_tool_failure_shows_no_traceback(self):
		with chat_settings(), mcp_settings(), as_user(USER):
			envelope = {
				"ok": False,
				"error": {
					"type": "X",
					"message": "boom",
					"messages": [],
					"traceback": "Traceback (most recent call last)",
				},
				"log": None,
			}
			text = tools.render_failure("get_document", envelope)
		self.assertNotIn("Traceback", text)
		self.assertIn("boom", text)


class TestChatToolsForClerks(ChatTestCase):
	"""A chat user who is not a System Manager, the setup the module is for."""

	def setUp(self):
		super().setUp()
		make_role("AI Chat Test Clerk")
		make_user(CLERK, "AI Chat User", "AI Chat Test Clerk")
		self.model = make_model()

	def test_clerks_get_read_tools_but_never_the_logs(self):
		with (
			mcp_settings(read_only_mode=0, allow_write=1, allow_sql_query=1) as mcp,
			chat_settings() as settings,
		):
			mcp.append("allowed_roles", {"role": "AI Chat Test Clerk"})
			names = tools.tool_access(settings, frappe.get_doc("AI Model", self.model), CLERK).names

		self.assertIn("list_documents", names)
		self.assertFalse(names & {"get_mcp_logs", "get_error_logs", "run_sql"})
