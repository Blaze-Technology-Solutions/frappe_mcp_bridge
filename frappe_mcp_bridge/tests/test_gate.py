# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe

from frappe_mcp_bridge.api import mcp as api
from frappe_mcp_bridge.tests.utils import IntegrationTestCase, make_todo, mcp_settings, request


class TestGate(IntegrationTestCase):
	def test_disabled_refuses_everything(self):
		with mcp_settings(enabled=0):
			result = api.execute(tool="describe_site", params={})

		self.assertFalse(result["ok"])
		self.assertIn("disabled", result["error"]["message"])

	def test_read_allowed_when_enabled(self):
		with mcp_settings():
			result = api.execute(tool="describe_site", params={})

		self.assertTrue(result["ok"], result)
		self.assertEqual(result["result"]["user"], frappe.session.user)

	def test_read_only_mode_refuses_writes(self):
		name = make_todo()
		with mcp_settings(allow_write=1):
			result = api.execute(
				tool="update_document",
				params={"doctype": "ToDo", "name": name, "values": {"priority": "High"}},
			)

		self.assertFalse(result["ok"])
		self.assertIn("Read Only Mode", result["error"]["message"])

	def test_unticked_capability_is_named(self):
		with mcp_settings(read_only_mode=0):
			result = api.execute(tool="run_sql", params={"query": "select 1"})

		self.assertFalse(result["ok"])
		self.assertIn("Allow SQL", result["error"]["message"])

	def test_permission_doctypes_are_always_blocked(self):
		with mcp_settings():
			result = api.execute(tool="get_document", params={"doctype": "User", "name": "Administrator"})

		self.assertFalse(result["ok"])
		self.assertIn("can never be reached", result["error"]["message"])

	def test_blocked_list(self):
		with mcp_settings(blocked_doctypes=[{"document_type": "ToDo"}]):
			result = api.execute(tool="count_documents", params={"doctype": "ToDo"})

		self.assertFalse(result["ok"])
		self.assertIn("blocked list", result["error"]["message"])

	def test_role_gate(self):
		with mcp_settings(), patch("frappe.get_roles", return_value=["Guest"]):
			result = api.execute(tool="describe_site", params={})

		self.assertFalse(result["ok"])
		self.assertIn("none of the roles", result["error"]["message"])

	def test_unknown_parameter_is_refused(self):
		with mcp_settings():
			result = api.execute(tool="get_single", params={"doctype": "ToDo", "bogus": 1})

		self.assertFalse(result["ok"])
		self.assertIn("does not take bogus", result["error"]["message"])

	def test_oauth_sign_in_needs_its_checkbox(self):
		with mcp_settings(allow_oauth=0), request(headers={"Authorization": "Bearer abc"}):
			refused = api.execute(tool="describe_site", params={})

		with mcp_settings(allow_oauth=1), request(headers={"Authorization": "Bearer abc"}):
			allowed = api.execute(tool="describe_site", params={})

		self.assertFalse(refused["ok"])
		self.assertIn("OAuth sign-in is off", refused["error"]["message"])
		self.assertTrue(allowed["ok"], allowed)

	def test_api_key_sign_in_can_be_switched_off(self):
		with mcp_settings(allow_api_keys=0), request(headers={"Authorization": "token key:secret"}):
			result = api.execute(tool="describe_site", params={})

		self.assertFalse(result["ok"])
		self.assertIn("API key sign-in is off", result["error"]["message"])


class TestDryRun(IntegrationTestCase):
	def test_dry_run_rolls_back_and_live_run_writes(self):
		name = make_todo("before")

		with mcp_settings(read_only_mode=0, allow_write=1):
			dry = api.execute(
				tool="update_document",
				params={"doctype": "ToDo", "name": name, "values": {"description": "after"}},
				dry_run=True,
			)
			self.assertEqual(frappe.db.get_value("ToDo", name, "description"), "before")

			live = api.execute(
				tool="update_document",
				params={"doctype": "ToDo", "name": name, "values": {"description": "after"}},
				dry_run=False,
			)

		self.assertTrue(dry["ok"] and dry["dry_run"], dry)
		self.assertTrue(live["ok"] and not live["dry_run"], live)
		self.assertEqual(frappe.db.get_value("ToDo", name, "description"), "after")

	def test_default_dry_run_setting(self):
		name = make_todo("before")

		with mcp_settings(read_only_mode=0, allow_write=1, default_dry_run=1):
			result = api.execute(
				tool="update_document",
				params={"doctype": "ToDo", "name": name, "values": {"description": "after"}},
			)

		self.assertTrue(result["dry_run"])
		self.assertEqual(frappe.db.get_value("ToDo", name, "description"), "before")
