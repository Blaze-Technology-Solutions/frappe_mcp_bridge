# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class MCPWriteApproval(Document):
	def validate(self):
		# Only the bridge writes these: the record of what was asked must stay as it was.
		if not self.flags.mcp_internal:
			frappe.throw(
				_("MCP Write Approvals are changed only by approving or rejecting them."),
				frappe.PermissionError,
			)
