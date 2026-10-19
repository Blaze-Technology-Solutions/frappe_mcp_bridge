# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

from frappe.model.document import Document

from frappe_mcp_bridge.chat.access import only_administrator_deletes


class AIChatBudgetAlert(Document):
	def on_trash(self):
		only_administrator_deletes(self)
