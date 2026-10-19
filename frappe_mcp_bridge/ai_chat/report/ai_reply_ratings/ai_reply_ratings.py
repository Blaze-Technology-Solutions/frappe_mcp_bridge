# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""How people rate each model's replies, for AI Chat Managers deciding which models to pay for."""

import frappe
from frappe import _
from frappe.utils import add_days, getdate

from frappe_mcp_bridge.chat import access


def execute(filters=None):
	if not access.is_manager():
		frappe.throw(_("Only AI Chat Managers can see everyone's ratings."), frappe.PermissionError)

	filters = frappe._dict(filters or {})
	since = getdate(filters.from_date or add_days(getdate(), -30))
	until = add_days(getdate(filters.to_date or getdate()), 1)

	rows = frappe.db.sql(
		"""select model,
			sum(rating = 'Good') as good,
			sum(rating = 'Bad') as bad
		from `tabAI Chat Feedback`
		where rating in ('Good', 'Bad') and creation >= %(since)s and creation < %(until)s
		group by model
		order by bad desc, good desc""",
		{"since": since, "until": until},
		as_dict=True,
	)
	for row in rows:
		rated = (row.good or 0) + (row.bad or 0)
		row.rated = rated
		row.good_share = 100.0 * (row.good or 0) / rated if rated else 0
		row.last_note = frappe.db.get_value(
			"AI Chat Feedback",
			{"model": row.model, "rating": "Bad", "note": ("is", "set"), "creation": (">=", since)},
			"note",
			order_by="creation desc",
		)

	columns = [
		{"fieldname": "model", "label": _("Model"), "fieldtype": "Link", "options": "AI Model", "width": 220},
		{"fieldname": "rated", "label": _("Rated Replies"), "fieldtype": "Int", "width": 120},
		{"fieldname": "good", "label": _("Good"), "fieldtype": "Int", "width": 90},
		{"fieldname": "bad", "label": _("Bad"), "fieldtype": "Int", "width": 90},
		{"fieldname": "good_share", "label": _("Good %"), "fieldtype": "Percent", "width": 100},
		{
			"fieldname": "last_note",
			"label": _("Latest Note on a Bad Reply"),
			"fieldtype": "Data",
			"width": 360,
		},
	]
	return columns, rows
