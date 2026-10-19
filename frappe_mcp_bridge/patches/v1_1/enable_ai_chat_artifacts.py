# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

import frappe


def execute():
	"""Artifacts arrived after AI Chat. A Single keeps no value for a field it did not have
	yet, which reads as unticked, so switch them on where nobody has chosen yet."""
	for field, value in (("allow_artifacts", 1), ("max_artifact_kb", 200)):
		stored = frappe.db.sql(
			"select value from `tabSingles` where doctype = %s and field = %s", ("AI Chat Settings", field)
		)
		if not stored:
			frappe.db.set_single_value("AI Chat Settings", field, value)
