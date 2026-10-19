# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from frappe_mcp_bridge.chat.access import only_administrator_deletes


class AIChatAction(Document):
	def validate(self):
		# Only the approval flow itself moves an action along; nobody edits one by hand.
		if not self.is_new() and not self.flags.ai_chat_internal:
			frappe.throw(_("AI Chat approvals can't be edited."), frappe.PermissionError)

	def on_trash(self):
		only_administrator_deletes(self)
