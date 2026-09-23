# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

import frappe


def execute():
	"""Allow API Key Sign-In arrived after the first release. A Single keeps no value for a
	field it did not have yet, which reads as unticked, so every existing API key client
	would be locked out. Tick it where it was never set."""
	stored = frappe.db.sql(
		"select value from `tabSingles` where doctype = %s and field = %s",
		("MCP Bridge Settings", "allow_api_keys"),
	)
	if not stored:
		frappe.db.set_single_value("MCP Bridge Settings", "allow_api_keys", 1)
