# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""How each model behaves: speed, replies cut off or declined, and failures, from the replies
AI Chat already keeps. For AI Chat Managers choosing and watching models."""

import frappe
from frappe import _
from frappe.utils import add_days, flt, getdate

from frappe_mcp_bridge.chat import access


def execute(filters=None):
	if not access.is_manager():
		frappe.throw(_("Only AI Chat Managers can see how the models behave."), frappe.PermissionError)

	filters = frappe._dict(filters or {})
	window = {
		"since": getdate(filters.from_date or add_days(getdate(), -30)),
		"until": add_days(getdate(filters.to_date or getdate()), 1),
	}

	rows = frappe.db.sql(
		"""select model,
			count(*) as replies,
			avg(latency_ms) / 1000 as average_seconds,
			sum(finish_reason in ('length', 'max_tokens')) as cut_off,
			sum(finish_reason = 'stopped') as stopped,
			sum(exclude_from_context = 1 and replaced = 0) as declined,
			sum(usage_estimated) as estimated
		from `tabAI Chat Message`
		where role = 'Assistant' and model is not null and creation >= %(since)s and creation < %(until)s
		group by model
		order by replies desc""",
		window,
		as_dict=True,
	)
	failures = dict(
		frappe.db.sql(
			"""select model, count(*) from `tabAI Chat Message`
			where role = 'Notice' and is_error = 1 and model is not null
				and creation >= %(since)s and creation < %(until)s
			group by model""",
			window,
		)
	)

	for row in rows:
		latencies = frappe.db.sql_list(
			"""select latency_ms from `tabAI Chat Message`
			where role = 'Assistant' and model = %(model)s and creation >= %(since)s and creation < %(until)s
			order by latency_ms""",
			{**window, "model": row.model},
		)
		# The slowest one in twenty: what a user waits on a bad day.
		row.slow_seconds = (
			flt(latencies[min(int(len(latencies) * 0.95), len(latencies) - 1)]) / 1000 if latencies else 0
		)
		row.failed = failures.get(row.model, 0)

	columns = [
		{"fieldname": "model", "label": _("Model"), "fieldtype": "Link", "options": "AI Model", "width": 200},
		{"fieldname": "replies", "label": _("Replies"), "fieldtype": "Int", "width": 90},
		{
			"fieldname": "average_seconds",
			"label": _("Average (s)"),
			"fieldtype": "Float",
			"precision": 1,
			"width": 110,
		},
		{
			"fieldname": "slow_seconds",
			"label": _("Slowest 5% (s)"),
			"fieldtype": "Float",
			"precision": 1,
			"width": 120,
		},
		{"fieldname": "failed", "label": _("Failed"), "fieldtype": "Int", "width": 80},
		{"fieldname": "declined", "label": _("Declined"), "fieldtype": "Int", "width": 90},
		{"fieldname": "cut_off", "label": _("Cut Off"), "fieldtype": "Int", "width": 80},
		{"fieldname": "stopped", "label": _("Stopped by User"), "fieldtype": "Int", "width": 120},
		{"fieldname": "estimated", "label": _("Usage Estimated"), "fieldtype": "Int", "width": 120},
	]
	return columns, rows
