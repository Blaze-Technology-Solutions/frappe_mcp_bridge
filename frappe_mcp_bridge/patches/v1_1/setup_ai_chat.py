# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

from frappe_mcp_bridge.install import setup_ai_chat


def execute():
	"""AI Chat arrived after the first release: give existing sites its roles and settings."""
	setup_ai_chat()
