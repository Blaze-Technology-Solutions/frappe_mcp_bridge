# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Builds the MCP server and wires the tools to the bridge."""

from mcp.server.mcpserver import MCPServer

from . import __version__
from .client import Bridge
from .config import Config
from .tools import register_all

INSTRUCTIONS = """\
Tools for a Frappe / ERPNext site.

How to work here:

1. Call site_ping first in a session. It reports the site, the user the key belongs
   to, and whether MCP is enabled and read-only. If a tool comes back blocked,
   site_capabilities names the exact setting that refused it — say which one, rather
   than guessing at permissions.
2. Call get_doctype_schema before creating or updating a doctype you have not touched
   in this session. Field names and Select options are not guessable.
3. Before changing anything: count_documents to see the blast radius, then run the
   write with dry_run=true. A dry run does the real work and rolls it back, so the
   errors it reports are the errors a live call would hit. Show the user what would
   change, and only then run it for real.
4. Prefer update_document and import_records, which re-run the document's validations,
   over force_set_values and run_server_script, which do not.
5. Every call is logged on the site in MCP Bridge Log whatever its outcome, and the log
   name comes back with each reply. get_mcp_logs reads that history back.

The site holds the real limits. Read Only Mode, the per-capability checkboxes, the
doctype allow/block lists and the batch ceilings all live in MCP Bridge Settings and
are the user's to change, not yours to work around.
"""


def build(config: Config) -> tuple[MCPServer, Bridge]:
	mcp = MCPServer(
		"frappe-mcp-bridge",
		title="Frappe MCP Bridge",
		instructions=INSTRUCTIONS,
		version=__version__,
	)

	bridge = Bridge(config)
	register_all(mcp, bridge)

	return mcp, bridge


def serve(config: Config) -> None:
	mcp, _bridge = build(config)

	if config.transport == "stdio":
		mcp.run(transport="stdio")
	else:
		# host and port are run() arguments in the 2.x SDK, not constructor settings.
		mcp.run(transport=config.transport, host=config.host, port=config.port)
