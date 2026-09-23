# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Connection and capability tools. Start here when something is refused."""

from ..client import Bridge
from .common import render, render_plain


def register(mcp, bridge: Bridge) -> None:
	@mcp.tool()
	async def site_ping() -> str:
		"""Check the connection to the Frappe site and whether MCP access is switched on.

		Answers even while MCP is disabled, so this is the tool to reach for when another
		tool says it was blocked. Reports the site, the user the API key belongs to, its
		roles, and whether Read Only Mode is on.
		"""
		result = await bridge.ping()
		result["configured_url"] = bridge.config.url
		result["client_read_only"] = bridge.config.read_only
		return render_plain(result)

	@mcp.tool()
	async def site_capabilities() -> str:
		"""List every MCP tool the site knows and whether it is currently permitted.

		Each entry says which capability it needs and, when unavailable, exactly which
		checkbox in MCP Bridge Settings is off. Use this before telling the user that
		something cannot be done.
		"""
		return render_plain(await bridge.catalogue())

	@mcp.tool()
	async def describe_site() -> str:
		"""Site name, installed app versions, time zone, and the current MCP permissions.

		Worth calling once at the start of a session so later work targets the right
		versions and the right scope.
		"""
		return render(await bridge.call("describe_site"))

	@mcp.tool()
	async def list_doctypes(
		search: str | None = None,
		module: str | None = None,
		app: str | None = None,
		include_child_tables: bool = False,
		limit: int = 100,
	) -> str:
		"""Find doctypes by partial name, module or app.

		Use it to confirm a doctype's exact spelling before any other call; "Work Order"
		and "Workorder" are not the same thing to Frappe.
		"""
		return render(
			await bridge.call(
				"list_doctypes",
				{
					"search": search,
					"module": module,
					"app": app,
					"include_child_tables": include_child_tables,
					"limit": limit,
				},
			)
		)

	@mcp.tool()
	async def get_doctype_schema(
		doctype: str,
		include_children: bool = True,
		include_layout_fields: bool = False,
	) -> str:
		"""Fields, types, link targets, mandatory flags and naming rules for one doctype.

		Read this before creating or updating documents of a doctype you have not touched
		yet, so field names and Select options are right the first time. Child table
		schemas come along by default.
		"""
		return render(
			await bridge.call(
				"get_doctype_schema",
				{
					"doctype": doctype,
					"include_children": include_children,
					"include_layout_fields": include_layout_fields,
				},
			)
		)

	@mcp.tool()
	async def list_reports(
		search: str | None = None,
		doctype: str | None = None,
		limit: int = 100,
	) -> str:
		"""List the query and script reports on the site, optionally filtered by doctype."""
		return render(
			await bridge.call(
				"list_reports",
				{"search": search, "doctype": doctype, "limit": limit},
			)
		)
