# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from frappe_mcp_bridge.chat.access import only_administrator_deletes


class AIChatMessage(Document):
	def validate(self):
		# The audit trail is write-once: a stored message is never changed.
		if not self.is_new():
			frappe.throw(_("AI Chat messages can't be changed."), frappe.PermissionError)

	def on_trash(self):
		only_administrator_deletes(self)


def on_doctype_update():
	# Each position in a chat is taken once: a second writer fails rather than interleaving.
	frappe.db.add_unique(
		"AI Chat Message", ["conversation", "seq"], constraint_name="unique_conversation_seq"
	)
