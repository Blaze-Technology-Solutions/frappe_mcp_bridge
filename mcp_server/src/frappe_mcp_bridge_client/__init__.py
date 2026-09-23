# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""MCP server for a Frappe site.

Holds no database connection of its own: every tool posts to frappe_mcp_bridge.api.mcp.execute on
the target site, which is where access is gated and where the audit log is written.
"""

__version__ = "0.1.0"
