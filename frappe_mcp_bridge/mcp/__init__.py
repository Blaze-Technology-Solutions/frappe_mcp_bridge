# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Server side of the Frappe MCP Bridge bridge.

The MCP server process (see `mcp_server/` at the app root) never talks to the database.
It forwards every tool call to `frappe_mcp_bridge.api.mcp.execute`, which is the single place where
access is gated, the work is dispatched and the call is logged.
"""
