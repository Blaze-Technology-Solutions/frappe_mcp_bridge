# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

import frappe


def execute():
	"""The per-minute limit arrived after AI Chat. A Single keeps no value for a field it did not
	have yet, which reads as no limit, so set the default where nobody has chosen yet."""
	stored = frappe.db.sql(
		"select value from `tabSingles` where doctype = %s and field = %s",
		("AI Chat Settings", "max_messages_per_minute"),
	)
	if not stored:
		frappe.db.set_single_value("AI Chat Settings", "max_messages_per_minute", 10)
