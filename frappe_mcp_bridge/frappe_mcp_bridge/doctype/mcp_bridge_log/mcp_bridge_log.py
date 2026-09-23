# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import add_days, now_datetime


class MCPBridgeLog(Document):
	pass


def clear_old_logs():
	"""Daily scheduler hook. Retention of 0 keeps everything."""
	retention = frappe.db.get_single_value("MCP Bridge Settings", "log_retention_days")
	if not retention:
		return

	cutoff = add_days(now_datetime(), -int(retention))
	frappe.db.delete("MCP Bridge Log", {"creation": ("<", cutoff)})
