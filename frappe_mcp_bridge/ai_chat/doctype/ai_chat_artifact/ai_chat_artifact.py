# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from frappe_mcp_bridge.chat.access import only_administrator_deletes


class AIChatArtifact(Document):
	def validate(self):
		# A version is never changed: a new one is saved instead.
		if not self.is_new():
			frappe.throw(_("AI Chat artifacts can't be changed."), frappe.PermissionError)

	def on_trash(self):
		only_administrator_deletes(self)
