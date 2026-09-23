# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""bench commands for setting up and inspecting the MCP bridge."""

import click
import frappe
from frappe.commands import get_site, pass_context


@click.command("mcp-bridge-keys")
@click.option("--user", required=True, help="User the MCP client will act as, e.g. mcp@example.com")
@click.option("--show-env", is_flag=True, help="Print a ready to paste .env block for the stdio bridge.")
@pass_context
def mcp_bridge_keys(context, user, show_env):
	"""Generate the API key and secret Claude Code or Codex authenticates with.

	The secret is shown once and never again, so copy it now.
	"""
	from frappe.core.doctype.user.user import generate_keys

	from frappe_mcp_bridge.frappe_mcp_bridge.doctype.mcp_bridge_settings.mcp_bridge_settings import (
		_connection_info,
	)

	site = get_site(context)
	frappe.init(site=site)
	frappe.connect()

	try:
		if not frappe.db.exists("User", user):
			click.secho(f"No user named {user} on {site}.", fg="red")
			raise SystemExit(1)

		frappe.set_user("Administrator")
		keys = generate_keys(user)
		frappe.db.commit()

		click.secho(f"API keys regenerated for {user} on {site}.", fg="green")

		info = _connection_info(keys["api_key"], keys["api_secret"])
		if show_env:
			click.echo("")
			click.echo(info["stdio_env"])
		else:
			click.echo(f"  api_key    {keys['api_key']}")
			click.echo(f"  api_secret {keys['api_secret']}")

		click.echo(f"\nEndpoint     {info['endpoint']}")
		click.echo(f"\nClaude Code\n  {info['claude_code']}")
		click.echo(f"\nCodex\n{info['codex']}")

		click.secho(
			"\nAny previously issued secret for this user has just stopped working.",
			fg="yellow",
		)
	finally:
		frappe.destroy()


@click.command("mcp-bridge-status")
@pass_context
def mcp_bridge_status(context):
	"""Show whether MCP access is on, what it may do, and recent activity."""
	from frappe_mcp_bridge.frappe_mcp_bridge.doctype.mcp_bridge_settings.mcp_bridge_settings import (
		CAPABILITY_FIELDS,
	)

	site = get_site(context)
	frappe.init(site=site)
	frappe.connect()

	try:
		settings = frappe.get_single("MCP Bridge Settings")

		click.echo(f"Site            {site}")
		click.secho(
			f"MCP access      {'ON' if settings.enabled else 'OFF'}",
			fg="green" if settings.enabled else "red",
		)
		click.secho(
			f"Read only mode  {'ON' if settings.read_only_mode else 'OFF'}",
			fg="blue" if settings.read_only_mode else "yellow",
		)
		if not settings.read_only_mode and settings.writes_allowed_until:
			click.echo(f"Writes until    {settings.writes_allowed_until}")
		click.echo(f"Default dry run {'ON' if settings.default_dry_run else 'OFF'}")
		click.echo(f"MCP user        {settings.mcp_user or '-'}")
		click.echo(
			f"Sign-in         API keys {'on' if settings.allow_api_keys else 'off'}, "
			f"OAuth {'on' if settings.allow_oauth else 'off'}"
		)
		click.echo(f"Masked fields   {len(settings.masked_fields or [])}")

		click.echo("\nCapabilities")
		for capability, field in sorted(CAPABILITY_FIELDS.items()):
			mark = "x" if settings.get(field) else " "
			click.echo(f"  [{mark}] {capability}")

		total = frappe.db.count("MCP Bridge Log")
		click.echo(f"\nLogged calls    {total}")

		for row in frappe.get_all(
			"MCP Bridge Log",
			fields=["creation", "tool", "status", "user"],
			order_by="creation desc",
			limit=5,
		):
			click.echo(f"  {row.creation}  {row.status:<8} {row.tool:<24} {row.user}")
	finally:
		frappe.destroy()


@click.command("mcp-bridge-enable")
@click.option("--read-only/--allow-writes", default=True, show_default=True)
@click.option(
	"--minutes",
	type=int,
	help="With --allow-writes: switch Read Only Mode back on after this many minutes.",
)
@pass_context
def mcp_bridge_enable(context, read_only, minutes):
	"""Switch MCP access on. Stays read only unless --allow-writes is passed."""
	_set_enabled(context, 1, read_only, minutes)


@click.command("mcp-bridge-disable")
@pass_context
def mcp_bridge_disable(context):
	"""Switch MCP access off. Every tool call is refused until it is switched back on."""
	_set_enabled(context, 0, True)


def _set_enabled(context, enabled: int, read_only: bool, minutes: int | None = None) -> None:
	from frappe.utils import add_to_date, now_datetime

	from frappe_mcp_bridge.frappe_mcp_bridge.doctype.mcp_bridge_settings.mcp_bridge_settings import (
		MAX_WRITE_WINDOW_MINUTES,
	)

	if minutes is not None and (read_only or not 1 <= minutes <= MAX_WRITE_WINDOW_MINUTES):
		click.secho(
			f"--minutes needs --allow-writes and a value from 1 to {MAX_WRITE_WINDOW_MINUTES}.", fg="red"
		)
		raise SystemExit(1)

	site = get_site(context)
	frappe.init(site=site)
	frappe.connect()

	try:
		frappe.set_user("Administrator")
		settings = frappe.get_single("MCP Bridge Settings")
		settings.enabled = enabled
		if enabled:
			settings.read_only_mode = 1 if read_only else 0
			settings.writes_allowed_until = add_to_date(now_datetime(), minutes=minutes) if minutes else None
		settings.save()
		frappe.db.commit()

		state = "OFF" if not enabled else ("ON, read only" if read_only else "ON, writes allowed")
		if enabled and minutes:
			state += f" until {settings.writes_allowed_until}"
		click.secho(f"MCP access on {site}: {state}", fg="green" if enabled else "red")
	finally:
		frappe.destroy()


commands = [mcp_bridge_keys, mcp_bridge_status, mcp_bridge_enable, mcp_bridge_disable]
