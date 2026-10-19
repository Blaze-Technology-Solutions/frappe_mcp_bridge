# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import add_to_date, now_datetime

from frappe_mcp_bridge.api import mcp as api
from frappe_mcp_bridge.mcp import approvals
from frappe_mcp_bridge.tests.chat_utils import ChatTestCase, contained, make_user
from frappe_mcp_bridge.tests.utils import as_user, make_todo, mcp_settings

USER = "mcp-approvals@example.com"
OTHER = "mcp-approvals-other@example.com"
HOLD = {"read_only_mode": 0, "allow_write": 1, "writes_need_approval": 1, "log_requests": 1}


def ask(todo, description, client="Claude Code", dry_run=None):
	params = {"doctype": "ToDo", "name": todo, "values": {"description": description}}
	return api.execute(tool="update_document", params=params, client=client, dry_run=dry_run)


def status_of(approval):
	return api.execute(tool="get_write_approval", params={"approval": approval}, client="Claude Code")


class TestWriteApprovals(ChatTestCase):
	def setUp(self):
		super().setUp()
		# System Manager is MCP's default allowed role.
		make_user(USER, "System Manager")
		make_user(OTHER, "System Manager")

	def test_a_write_waits_until_its_owner_approves_it_in_desk(self):
		todo = make_todo("before")
		with mcp_settings(**HOLD), contained():
			with as_user(USER):
				envelope = ask(todo, "after")
				approval = envelope["result"]["approval"]
				self.assertTrue(envelope["ok"])
				self.assertFalse(envelope["result"]["saved"])
				self.assertIn("NOT SAVED YET", envelope["result"]["message"])
				self.assertEqual(frappe.db.get_value("ToDo", todo, "description"), "before")
				self.assertIn("after", frappe.db.get_value("MCP Write Approval", approval, "preview"))
				self.assertTrue(
					frappe.db.exists("Notification Log", {"document_name": approval, "for_user": USER})
				)
				self.assertEqual(status_of(approval)["result"]["status"], "Pending")

			with as_user(OTHER):
				self.assertRaises(frappe.PermissionError, approvals.decide, approval, 1)
				self.assertFalse(status_of(approval)["ok"])

			with as_user(USER):
				self.assertEqual(approvals.decide(approval, 1)["status"], "Approved")
				self.assertRaises(frappe.ValidationError, approvals.decide, approval, 1)
				outcome = status_of(approval)["result"]

		self.assertEqual(frappe.db.get_value("ToDo", todo, "description"), "after")
		self.assertTrue(outcome["saved"])
		self.assertTrue(frappe.db.get_value("MCP Write Approval", approval, "result_log"))

	def test_a_rejected_or_expired_write_is_never_made(self):
		todo = make_todo("kept")
		with mcp_settings(**HOLD), contained(), as_user(USER):
			rejected = ask(todo, "rejected")["result"]["approval"]
			self.assertEqual(approvals.decide(rejected, 0)["status"], "Rejected")

			expired = ask(todo, "expired")["result"]["approval"]
			frappe.db.set_value(
				"MCP Write Approval", expired, "expires_at", add_to_date(now_datetime(), minutes=-1)
			)
			self.assertRaises(frappe.ValidationError, approvals.decide, expired, 1)

		self.assertEqual(frappe.db.get_value("ToDo", todo, "description"), "kept")
		self.assertEqual(frappe.db.get_value("MCP Write Approval", expired, "status"), "Expired")

	def test_ai_chat_dry_runs_and_the_switch_off_are_not_held(self):
		todo = make_todo("start")
		with contained(), as_user(USER):
			with mcp_settings(**HOLD):
				# A dry run changes nothing, so it needs no approval.
				self.assertTrue(ask(todo, "dry", dry_run=True)["dry_run"])
				# AI Chat has approval cards of its own.
				self.assertTrue(ask(todo, "from chat", client="AI Chat")["ok"])
			self.assertEqual(frappe.db.get_value("ToDo", todo, "description"), "from chat")

			with mcp_settings(**{**HOLD, "writes_need_approval": 0}):
				self.assertTrue(ask(todo, "straight through")["ok"])
			self.assertEqual(frappe.db.get_value("ToDo", todo, "description"), "straight through")
		self.assertFalse(frappe.db.exists("MCP Write Approval", {"user": USER}))
