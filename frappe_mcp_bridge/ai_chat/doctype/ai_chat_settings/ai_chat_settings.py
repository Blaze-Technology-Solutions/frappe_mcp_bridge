# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

from frappe_mcp_bridge.chat import access


class AIChatSettings(Document):
	def validate(self):
		self.clamp_limits()
		self.validate_currency()
		self.validate_rows()

	def clamp_limits(self):
		# A blank or zero ceiling would read as "nothing allowed" to the reply loop.
		self.max_tool_rounds = min(max(cint(self.max_tool_rounds) or 8, 1), 50)
		self.tool_result_chars = max(cint(self.tool_result_chars) or 20_000, 1_000)
		self.approval_expiry_minutes = max(cint(self.approval_expiry_minutes) or 30, 1)
		self.reply_timeout = max(cint(self.reply_timeout) or 900, 60)
		self.max_attachment_mb = max(cint(self.max_attachment_mb) or 10, 1)
		self.max_attachments = max(cint(self.max_attachments) or 5, 1)
		self.default_monthly_budget = max(flt(self.default_monthly_budget), 0)
		self.default_daily_messages = max(cint(self.default_daily_messages), 0)
		self.max_messages_per_minute = max(cint(self.max_messages_per_minute), 0)

	def validate_currency(self):
		if not self.display_currency or self.display_currency == "USD":
			self.exchange_rate = 1
		elif flt(self.exchange_rate) <= 0:
			frappe.throw(_("Set how many {0} make one USD.").format(self.display_currency))

	def validate_rows(self):
		for table in ("role_budgets", "user_overrides"):
			key = "role" if table == "role_budgets" else "user"
			seen = set()
			for row in self.get(table) or []:
				if row.get(key) in seen:
					frappe.throw(
						_("{0} appears twice in {1}.").format(row.get(key), _(self.meta.get_label(table)))
					)
				seen.add(row.get(key))
				row.monthly_budget = max(flt(row.monthly_budget), 0)
				row.daily_messages = max(cint(row.daily_messages), 0)

		if self.default_model and not frappe.db.get_value("AI Model", self.default_model, "enabled"):
			frappe.throw(_("The default model {0} is disabled.").format(self.default_model))

	def on_update(self):
		frappe.clear_cache(doctype="AI Chat Settings")
		access.sync_workspace(self.enabled)
