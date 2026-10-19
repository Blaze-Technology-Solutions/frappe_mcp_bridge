# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Spend and tokens across every chat, for AI Chat Managers.

Every figure comes from the assistant messages themselves, each billed at the price in
force when it ran, so the report always matches what the providers charged.
"""

import frappe
from frappe import _
from frappe.utils import add_days, getdate

from frappe_mcp_bridge.chat import access

GROUPS = {
	"User": ("user", "Link", "User"),
	"Model": ("model", "Link", "AI Model"),
	"Provider": ("provider_type", "Data", None),
	"Day": ("date(creation)", "Date", None),
	"Month": ("date_format(creation, '%%Y-%%m')", "Data", None),
}
TIME_GROUPS = {"Day", "Month"}


def execute(filters=None):
	if not access.is_manager():
		frappe.throw(_("Only AI Chat Managers can see everyone's usage."), frappe.PermissionError)

	filters = frappe._dict(filters or {})
	group = filters.group_by if filters.group_by in GROUPS else "User"
	expression, fieldtype, options = GROUPS[group]

	conditions, values = _conditions(filters)
	rows = frappe.db.sql(
		f"""select {expression} as `group_key`,
			count(distinct conversation) as conversations,
			count(*) as replies,
			sum(input_tokens + cache_write_tokens) as input_tokens,
			sum(cached_input_tokens) as cached_input_tokens,
			sum(output_tokens) as output_tokens,
			sum(cost_usd) as cost_usd,
			sum(cost_display) as cost_display
		from `tabAI Chat Message`
		where role = 'Assistant' and {conditions}
		group by `group_key`
		order by {"`group_key` asc" if group in TIME_GROUPS else "cost_usd desc"}""",
		values,
		as_dict=True,
	)

	currencies = frappe.db.sql_list(
		f"""select distinct coalesce(display_currency, 'USD') from `tabAI Chat Message`
		where role = 'Assistant' and {conditions}""",
		values,
	)
	# Display-currency costs add up only when every reply used the same one; a stretch
	# shown in USD counts as a different currency.
	local = currencies[0] if len(currencies) == 1 and currencies[0] != "USD" else None

	for row in rows:
		row.usd = "USD"
		row.local_currency = local

	columns = [
		{
			"fieldname": "group_key",
			"label": _(group),
			"fieldtype": fieldtype,
			"options": options,
			"width": 220,
		},
		{"fieldname": "conversations", "label": _("Chats"), "fieldtype": "Int", "width": 90},
		{"fieldname": "replies", "label": _("Replies"), "fieldtype": "Int", "width": 90},
		{"fieldname": "input_tokens", "label": _("Input Tokens"), "fieldtype": "Int", "width": 130},
		{"fieldname": "cached_input_tokens", "label": _("Cached Tokens"), "fieldtype": "Int", "width": 130},
		{"fieldname": "output_tokens", "label": _("Output Tokens"), "fieldtype": "Int", "width": 130},
		{
			"fieldname": "cost_usd",
			"label": _("Cost (USD)"),
			"fieldtype": "Currency",
			"options": "usd",
			"width": 130,
		},
	]
	if local:
		columns.append(
			{
				"fieldname": "cost_display",
				"label": _("Cost ({0})").format(local),
				"fieldtype": "Currency",
				"options": "local_currency",
				"width": 130,
			}
		)

	total = sum(row.cost_usd or 0 for row in rows)
	summary = [
		{
			"label": _("Total cost (USD)"),
			"value": total,
			"datatype": "Currency",
			"currency": "USD",
			"indicator": "Blue",
		},
		{"label": _("Replies"), "value": sum(row.replies for row in rows), "datatype": "Int"},
		{"label": _("Chats"), "value": _chat_count(conditions, values), "datatype": "Int"},
	]

	chart = {
		"data": {
			"labels": [str(row.group_key or _("Not set")) for row in rows[:30]],
			"datasets": [
				{"name": _("Cost (USD)"), "values": [round(row.cost_usd or 0, 4) for row in rows[:30]]}
			],
		},
		"type": "line" if group in TIME_GROUPS else "bar",
		"fieldtype": "Currency",
		"options": "USD",
	}

	return columns, rows, None, chart, summary


def _conditions(filters) -> tuple[str, dict]:
	parts = ["creation >= %(from_date)s", "creation < %(to_date)s"]
	values = {
		"from_date": getdate(filters.from_date) if filters.from_date else getdate("2000-01-01"),
		# The To date is inclusive, so compare against the start of the next day.
		"to_date": add_days(getdate(filters.to_date), 1) if filters.to_date else add_days(getdate(), 1),
	}

	if filters.user:
		parts.append("user = %(user)s")
		values["user"] = filters.user

	if filters.model:
		parts.append("model = %(model)s")
		values["model"] = filters.model

	return " and ".join(parts), values


def _chat_count(conditions: str, values: dict) -> int:
	return frappe.db.sql(
		f"select count(distinct conversation) from `tabAI Chat Message` where role = 'Assistant' and {conditions}",
		values,
	)[0][0]
