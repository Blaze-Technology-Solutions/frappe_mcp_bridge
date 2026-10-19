# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

import json

import frappe

from frappe_mcp_bridge.api import chat as api
from frappe_mcp_bridge.chat import access, billing, orchestrator
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

USER = "ai-chat-features@example.com"
OTHER = "ai-chat-features-other@example.com"
MANAGER = "ai-chat-features-manager@example.com"


def call(tool, **args):
	return {"id": f"call_{tool}", "name": tool, "args": args}


def messages(conversation):
	return frappe.get_all(
		"AI Chat Message",
		filters={"conversation": conversation},
		fields=["name", "role", "content", "model", "replaced", "exclude_from_context", "is_note"],
		order_by="seq asc",
	)


class TestChatFeatures(ChatTestCase):
	def setUp(self):
		super().setUp()
		# System Manager is MCP's default allowed role, so this user may use ERP tools.
		make_user(USER, "AI Chat User", "System Manager")
		make_user(OTHER, "AI Chat User", "System Manager")
		make_user(MANAGER, "AI Chat Manager")
		self.model = make_model()
		self.second = make_model("Second Model")

	def start(self):
		return api.create_conversation(model=self.model)["name"]

	# Search -----------------------------------------------------------------------------

	def test_search_finds_chats_by_what_was_said(self):
		fake = FakeProvider(reply("The Acme invoice is overdue."), reply("Fine."))
		with chat_settings(), as_user(USER), contained(), provider(fake):
			acme = self.start()
			api.send_message(acme, "Check the customer invoices please")
			other = self.start()
			api.send_message(other, "Plan the warehouse move")
			found = api.list_conversations(search="acme")
			titled = api.list_conversations(search="warehouse")

		self.assertEqual([row.name for row in found], [acme])
		self.assertIn("Acme invoice", found[0].match)
		# Matched by its title, so there is nothing to quote.
		self.assertEqual([row.name for row in titled], [other])
		self.assertNotIn("match", titled[0])

		with chat_settings(), as_user(OTHER):
			self.assertFalse(api.list_conversations(search="acme"))

	# Paging -----------------------------------------------------------------------------

	def test_a_long_chat_loads_a_page_at_a_time(self):
		with chat_settings(), as_user(USER), contained():
			name = self.start()
			doc = frappe.get_doc("AI Chat Conversation", name)
			for index in range(api.PAGE + 20):
				orchestrator.insert_message(doc, {"role": "User", "content": f"message {index}"})

			first = api.get_conversation(name)
			older = api.older_messages(name, first["messages"][0]["seq"])

		self.assertEqual(len(first["messages"]), api.PAGE)
		self.assertTrue(first["has_more"])
		self.assertEqual(first["messages"][-1]["content"], f"message {api.PAGE + 19}")
		self.assertEqual([m["content"] for m in older["messages"]], [f"message {i}" for i in range(20)])
		self.assertFalse(older["has_more"])

	# Feedback ---------------------------------------------------------------------------

	def test_replies_are_rated_by_the_chat_owner_only(self):
		with chat_settings(), as_user(USER), contained(), provider(FakeProvider(reply("Here you go."))):
			name = self.start()
			api.send_message(name, "Hello")
			question, answer = (row.name for row in messages(name))

			api.set_feedback(answer, "Bad", 'Wrong <b onmouseover="x()">total</b>')
			# Frappe cleans the note, since managers read it in Desk.
			self.assertNotIn("onmouseover", frappe.db.get_value("AI Chat Feedback", answer, "note"))
			self.assertRaises(frappe.ValidationError, api.set_feedback, question, "Good")
			self.assertRaises(frappe.ValidationError, api.set_feedback, answer, "Great")
			self.assertEqual(api.get_conversation(name)["feedback"], {answer: "Bad"})
			api.set_feedback(answer, "Good")

		with chat_settings(), as_user(MANAGER):
			self.assertRaises(access.ChatAccessDenied, api.set_feedback, answer, "Bad")
			self.assertEqual(api.get_conversation(name)["feedback"], {answer: "Good"})
		with chat_settings(), as_user(OTHER):
			self.assertRaises(access.ChatAccessDenied, api.set_feedback, answer, "Bad")

		feedback = frappe.get_doc("AI Chat Feedback", answer)
		self.assertEqual((feedback.rating, feedback.user, feedback.model), ("Good", USER, self.model))
		self.assertFalse(feedback.note)

		# Ratings go with their chat.
		frappe.delete_doc("AI Chat Conversation", name)
		self.assertFalse(frappe.db.exists("AI Chat Feedback", answer))

	def test_managers_see_ratings_by_model(self):
		from frappe_mcp_bridge.ai_chat.report.ai_reply_ratings import ai_reply_ratings as report

		with chat_settings(), as_user(USER), contained(), provider(FakeProvider(reply("A."), reply("B."))):
			name = self.start()
			api.send_message(name, "One")
			api.send_message(name, "Two")
			first, second = (row.name for row in messages(name) if row.role == "Assistant")
			api.set_feedback(first, "Good")
			api.set_feedback(second, "Bad", "Made up a total")
			self.assertRaises(frappe.PermissionError, report.execute, {})

		with as_user(MANAGER):
			_columns, rows = report.execute({})
		row = next(row for row in rows if row.model == self.model)
		self.assertEqual(
			(row.rated, row.good, row.bad, row.good_share, row.last_note), (2, 1, 1, 50.0, "Made up a total")
		)

	# Regenerate -------------------------------------------------------------------------

	def test_a_plain_reply_can_be_regenerated_with_another_model(self):
		fake = FakeProvider(reply("First answer."), reply("Second answer."))
		with chat_settings(), as_user(USER), contained(), provider(fake):
			name = self.start()
			api.send_message(name, "Tell me something")
			result = api.regenerate(name, model=self.second)

		rows = messages(name)
		self.assertEqual([row.role for row in rows], ["User", "Assistant", "Notice", "Assistant"])
		self.assertEqual(result["replaced"], [rows[1].name])
		self.assertTrue(rows[1].replaced and rows[1].exclude_from_context)
		self.assertEqual((rows[3].content, rows[3].model), ("Second answer.", self.second))
		# Only the end changed: the model got the same history as the first time.
		self.assertEqual(
			[m.content for m in fake.requests[1].messages], [m.content for m in fake.requests[0].messages]
		)
		# A regenerated reply counts toward the daily limit like a message.
		self.assertEqual(billing.messages_today(USER), 2)

	def test_only_a_last_reply_without_tools_is_regenerated_within_the_limit(self):
		fake = FakeProvider(
			reply(tool_calls=[call("count_documents", doctype="ToDo")]), reply("There are some.")
		)
		with mcp_settings(), chat_settings(), as_user(USER), contained(), provider(fake):
			name = self.start()
			api.send_message(name, "How many todos?")
			self.assertRaises(frappe.ValidationError, api.regenerate, name)

		# One message was sent above; this one reaches the limit of two, so no regenerating.
		with (
			chat_settings(default_daily_messages=2),
			as_user(USER),
			contained(),
			provider(FakeProvider(reply("x"))),
		):
			name = self.start()
			api.send_message(name, "Hi")
			self.assertRaises(billing.BudgetExceeded, api.regenerate, name)

	def test_model_health_counts_speed_and_trouble(self):
		from frappe_mcp_bridge.ai_chat.report.ai_model_health import ai_model_health as report

		with chat_settings(), as_user(USER), contained():
			doc = frappe.get_doc("AI Chat Conversation", self.start())
			for latency, finish in ((1000, "stop"), (2000, "stop"), (9000, "length")):
				orchestrator.insert_message(
					doc,
					{
						"role": "Assistant",
						"content": "x",
						"model": self.model,
						"latency_ms": latency,
						"finish_reason": finish,
					},
				)
			orchestrator.add_notice(doc, "The provider failed.", is_error=True)
			self.assertRaises(frappe.PermissionError, report.execute, {})

		with as_user(MANAGER):
			_columns, rows = report.execute({})
		row = next(row for row in rows if row.model == self.model)
		self.assertEqual((row.replies, row.cut_off, row.failed, row.slow_seconds), (3, 1, 1, 9.0))
		self.assertAlmostEqual(float(row.average_seconds), 4.0)

	# Rate limit -------------------------------------------------------------------------

	def test_messages_are_limited_per_minute(self):
		with (
			chat_settings(max_messages_per_minute=2),
			as_user(USER),
			contained(),
			provider(FakeProvider(*[reply("ok") for _ in range(3)])),
		):
			name = self.start()
			api.send_message(name, "one")
			api.send_message(name, "two")
			self.assertRaises(billing.TooFast, api.send_message, name, "three")

	# Ask AI on lists and reports ---------------------------------------------------------

	def open_from(self, view, fake):
		with mcp_settings(log_requests=1), chat_settings(), as_user(USER), contained(), provider(fake):
			name = api.create_conversation(model=self.model, context_view=json.dumps(view))["name"]
			api.send_message(name, "Summarise these")
		return name, fake.requests[0].messages[0].content

	def test_a_chat_opened_from_a_list_sends_the_selected_rows(self):
		picked = [make_todo("from the list 1"), make_todo("from the list 2")]
		make_todo("left unpicked")
		name, sent = self.open_from(
			{"kind": "list", "doctype": "ToDo", "names": picked}, FakeProvider(reply("Two todos."))
		)

		self.assertIn("ToDo list with 2 rows selected", sent)
		self.assertIn("from the list 1", sent)
		self.assertNotIn("left unpicked", sent)
		self.assertEqual(frappe.db.get_value("AI Chat Conversation", name, "context_doctype"), "ToDo")
		# Read through the bridge, so the read is in its log like any other.
		self.assertTrue(frappe.db.exists("MCP Bridge Log", {"tool": "list_documents", "user": USER}))

	def test_a_chat_opened_from_a_report_sends_its_result(self):
		report = frappe.get_doc(
			{
				"doctype": "Report",
				"report_name": "AI Chat Test ToDos",
				"ref_doctype": "ToDo",
				"report_type": "Query Report",
				"is_standard": "No",
				"query": "select name, description from `tabToDo` where description like 'report row%%'",
				"roles": [{"role": "System Manager"}],
			}
		).insert(ignore_permissions=True)
		make_todo("report row 1")

		name, sent = self.open_from(
			{"kind": "report", "report": report.name}, FakeProvider(reply("One row."))
		)
		self.assertIn("from the report AI Chat Test ToDos", sent)
		self.assertIn("report row 1", sent)
		self.assertEqual(
			frappe.db.get_value("AI Chat Conversation", name, ["context_doctype", "context_name"]),
			("Report", report.name),
		)

	def test_a_list_or_report_the_user_cant_read_is_refused(self):
		with mcp_settings(), chat_settings():
			with as_user(USER):
				for view in (
					{"kind": "report", "report": "No Such Report"},
					{"kind": "list", "doctype": "AI Chat Message"},
				):
					self.assertRaises(
						frappe.PermissionError,
						api.create_conversation,
						model=self.model,
						context_view=json.dumps(view),
					)
			with as_user(MANAGER):
				self.assertRaises(
					frappe.PermissionError,
					api.create_conversation,
					model=self.model,
					context_view=json.dumps({"kind": "list", "doctype": "Sales Taxes and Charges Template"})
					if frappe.db.exists("DocType", "Sales Taxes and Charges Template")
					else json.dumps({"kind": "list", "doctype": "Error Log"}),
				)

	# Draft emails ------------------------------------------------------------------------

	def test_an_email_draft_reads_the_document_is_escaped_and_billed(self):
		todo = make_todo("Ship 40 units to Acme on Friday")
		fake = FakeProvider(
			reply("Dear Acme,\n\nYour 40 units ship on Friday.\n\n<script>x()</script>Regards")
		)
		with (
			mcp_settings(),
			chat_settings(default_model=self.model),
			as_user(USER),
			contained(),
			provider(fake),
		):
			result = api.draft_email("ToDo", todo, subject="Your order", instruction="Confirm the date")

		self.assertEqual(
			result["html"],
			"<p>Dear Acme,</p><p>Your 40 units ship on Friday.</p><p>&lt;script&gt;x()&lt;/script&gt;Regards</p>",
		)
		sent = fake.requests[0].messages[0].content
		self.assertIn("Ship 40 units", sent)
		self.assertIn("Confirm the date", sent)
		chat = frappe.get_all(
			"AI Chat Conversation",
			filters={"user": USER, "title": ("like", "Email draft%")},
			fields=["status", "cost_usd"],
		)[0]
		self.assertEqual(chat.status, "Archived")
		self.assertGreater(chat.cost_usd, 0)

	def test_a_model_that_may_not_read_company_data_drafts_nothing(self):
		todo = make_todo("private")
		outside = make_model("Outside Model", may_read_erp_data=0)
		with mcp_settings(), chat_settings(default_model=outside), as_user(USER), contained():
			self.assertRaises(frappe.ValidationError, api.draft_email, "ToDo", todo)

	# Row-by-row approval ----------------------------------------------------------------

	def write_and_decide(self, tool_call, rows, **mcp):
		fake = FakeProvider(reply(tool_calls=[tool_call]), reply("Proposed."))
		with (
			mcp_settings(read_only_mode=0, allow_write=1, log_requests=1, **mcp),
			chat_settings(),
			as_user(USER),
			contained(),
			provider(fake),
		):
			name = self.start()
			api.send_message(name, "Make the change")
			action = frappe.get_doc("AI Chat Action", {"conversation": name})
			offered = json.loads(action.rows)
			with provider(FakeProvider(reply("Done."))) as follow_up:
				api.decide_action(action.name, approve=1, rows=json.dumps(rows(offered)))
		action.reload()
		return action, offered, follow_up.requests[0].messages[-1].content

	def test_a_bulk_change_can_be_approved_for_some_rows_only(self):
		todos = [make_todo(f"bulk {index}") for index in range(3)]
		action, offered, note = self.write_and_decide(
			call(
				"bulk_update_documents",
				doctype="ToDo",
				filters={"name": ["in", todos]},
				values={"description": "bulk done"},
			),
			lambda offered: todos[:2],
		)

		self.assertEqual(sorted(row["key"] for row in offered), sorted(todos))
		self.assertIn("bulk done", offered[0]["detail"])
		self.assertEqual(
			[frappe.db.get_value("ToDo", todo, "description") for todo in todos],
			["bulk done", "bulk done", "bulk 2"],
		)
		self.assertEqual(action.status, "Approved")
		self.assertIn(todos[0], action.approved_params)
		self.assertIn("Only 2 of the 3 rows", note)

	def test_an_import_can_be_approved_for_some_rows_only(self):
		_action, offered, note = self.write_and_decide(
			call("import_records", doctype="ToDo", rows=[{"description": f"imported {i}"} for i in range(3)]),
			lambda offered: [offered[1]["key"]],
			allow_data_import=1,
		)

		self.assertEqual([row["detail"] for row in offered], ["Would be added"] * 3)
		self.assertEqual(frappe.db.count("ToDo", {"description": ("like", "imported %")}), 1)
		self.assertTrue(frappe.db.exists("ToDo", {"description": "imported 1"}))
		self.assertIn("Only 1 of the 3 rows", note)

	def test_approving_no_rows_is_refused(self):
		todos = [make_todo(f"none {index}") for index in range(2)]
		self.assertRaises(
			frappe.ValidationError,
			self.write_and_decide,
			call(
				"bulk_update_documents",
				doctype="ToDo",
				filters={"name": ["in", todos]},
				values={"priority": "High"},
			),
			lambda offered: [],
		)
