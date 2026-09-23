# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

from frappe_mcp_bridge.install import set_default_settings


def execute():
	"""Give sites that installed the app before after_install existed the same safe defaults."""
	set_default_settings()
