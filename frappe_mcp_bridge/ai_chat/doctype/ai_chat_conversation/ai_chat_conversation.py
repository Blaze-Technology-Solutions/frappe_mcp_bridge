# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from frappe_mcp_bridge.chat.access import only_administrator_deletes


class AIChatConversation(Document):
	def on_trash(self):
		only_administrator_deletes(self)

		# Each child record goes through delete_doc, so every one lands in Deleted
		# Documents and the whole chat can be restored. force skips the link check:
		# messages, approvals and artifacts point at each other.
		for doctype in ("AI Chat Action", "AI Chat Artifact", "AI Chat Feedback", "AI Chat Message"):
			for name in frappe.get_all(doctype, filters={"conversation": self.name}, pluck="name"):
				frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
