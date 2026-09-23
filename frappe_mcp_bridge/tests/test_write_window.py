# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

from frappe_mcp_bridge.api import mcp as api
from frappe_mcp_bridge.frappe_mcp_bridge.doctype.mcp_bridge_settings import mcp_bridge_settings
from frappe_mcp_bridge.tests.utils import IntegrationTestCase, make_todo, mcp_settings


class TestWriteWindow(IntegrationTestCase):
	def update(self, **settings):
		name = make_todo()
		with mcp_settings(allow_write=1, **settings):
			return api.execute(
				tool="update_document",
				params={"doctype": "ToDo", "name": name, "values": {"priority": "High"}},
				dry_run=True,
			)

	def test_open_window_allows_writes(self):
		result = self.update(read_only_mode=0, writes_allowed_until=add_to_date(now_datetime(), minutes=10))
		self.assertTrue(result["ok"], result)

	def test_expired_window_refuses_writes_before_the_scheduler_runs(self):
		result = self.update(read_only_mode=0, writes_allowed_until=add_to_date(now_datetime(), minutes=-1))

		self.assertFalse(result["ok"])
		self.assertIn("write window ended", result["error"]["message"])

	def test_expired_window_still_allows_reads(self):
		with mcp_settings(read_only_mode=0, writes_allowed_until=add_to_date(now_datetime(), minutes=-1)):
			result = api.execute(tool="describe_site", params={})

		self.assertTrue(result["ok"], result)
		self.assertTrue(result["result"]["mcp"]["read_only_mode"])

	def test_allow_writes_for_and_close(self):
		frappe.db.set_single_value("MCP Bridge Settings", {"enabled": 1, "read_only_mode": 1})

		mcp_bridge_settings.allow_writes_for(30)
		settings = frappe.get_single("MCP Bridge Settings")
		self.assertFalse(settings.read_only_mode)
		minutes_left = (get_datetime(settings.writes_allowed_until) - now_datetime()).total_seconds() / 60
		self.assertTrue(29 < minutes_left <= 30, minutes_left)

		frappe.db.set_single_value(
			"MCP Bridge Settings", "writes_allowed_until", add_to_date(now_datetime(), minutes=-1)
		)
		mcp_bridge_settings.close_expired_write_window()

		settings = frappe.get_single("MCP Bridge Settings")
		self.assertTrue(settings.read_only_mode)
		self.assertFalse(settings.writes_allowed_until)

	def test_window_length_is_bounded(self):
		frappe.db.set_single_value("MCP Bridge Settings", "enabled", 1)
		self.assertRaises(frappe.ValidationError, mcp_bridge_settings.allow_writes_for, 0)
		self.assertRaises(
			frappe.ValidationError,
			mcp_bridge_settings.allow_writes_for,
			mcp_bridge_settings.MAX_WRITE_WINDOW_MINUTES + 1,
		)

	def test_ticking_read_only_ends_the_window(self):
		settings = frappe.get_single("MCP Bridge Settings")
		settings.writes_allowed_until = add_to_date(now_datetime(), minutes=30)
		settings.read_only_mode = 1
		settings.save()

		self.assertFalse(frappe.get_single("MCP Bridge Settings").writes_allowed_until)
