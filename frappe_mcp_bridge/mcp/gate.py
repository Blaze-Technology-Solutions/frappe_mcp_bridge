# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Everything that can refuse an MCP call, in one place."""

import frappe
from frappe import _
from frappe.utils import add_to_date, now_datetime

from frappe_mcp_bridge.frappe_mcp_bridge.doctype.mcp_bridge_settings.mcp_bridge_settings import get_settings
from frappe_mcp_bridge.mcp.registry import Tool


class MCPBlocked(frappe.PermissionError):
	"""Refused by MCP Bridge Settings rather than by Frappe's own permission model."""


def check_request(tool: Tool, params: dict) -> None:
	"""Raise MCPBlocked unless this call is allowed. Order matters: cheapest checks first."""
	settings = get_settings()

	allowed, reason = settings.is_capability_allowed(tool.capability)
	if not allowed:
		raise MCPBlocked(reason)

	check_role(settings)
	check_ip(settings)
	check_rate_limit(settings)
	check_doctype(settings, tool, params)


def check_role(settings) -> None:
	allowed_roles = set(settings.get_allowed_roles())
	if allowed_roles & set(frappe.get_roles()):
		return

	raise MCPBlocked(
		_("{0} holds none of the roles allowed for MCP access: {1}").format(
			frappe.session.user, ", ".join(sorted(allowed_roles))
		)
	)


def check_ip(settings) -> None:
	ip = getattr(frappe.local, "request_ip", None)
	if settings.is_ip_allowed(ip):
		return

	raise MCPBlocked(_("{0} is not in the allowed IP list for MCP access.").format(ip or _("Unknown IP")))


def check_rate_limit(settings) -> None:
	limit = int(settings.max_requests_per_hour or 0)
	if not limit:
		return

	if not settings.log_requests:
		# Without logging there is nothing to count, so the limit cannot be enforced.
		# Say so rather than silently letting everything through.
		raise MCPBlocked(
			_("Max Requests Per Hour is set but Log Requests is off, so the limit cannot be applied.")
		)

	used = frappe.db.count(
		"MCP Bridge Log",
		{
			"user": frappe.session.user,
			"creation": (">", add_to_date(now_datetime(), hours=-1)),
		},
	)
	if used >= limit:
		raise MCPBlocked(_("MCP rate limit reached: {0} calls in the last hour.").format(limit))


def check_doctype(settings, tool: Tool, params: dict) -> None:
	if not tool.doctype_param:
		return

	doctype = params.get(tool.doctype_param)
	if not doctype:
		return

	for target in doctype if isinstance(doctype, list | tuple) else [doctype]:
		allowed, reason = settings.is_doctype_allowed(target)
		if not allowed:
			raise MCPBlocked(reason)


def assert_doctype_allowed(doctype: str) -> None:
	"""For handlers that resolve a doctype themselves, after the gate has already run."""
	allowed, reason = get_settings().is_doctype_allowed(doctype)
	if not allowed:
		raise MCPBlocked(reason)


def resolve_dry_run(tool: Tool, requested: bool | None) -> bool:
	if not tool.writes:
		return False

	if requested is not None:
		return bool(requested)

	return bool(get_settings().default_dry_run)


def max_rows() -> int:
	return int(get_settings().max_rows or 500)


def max_write_batch() -> int:
	return int(get_settings().max_write_batch or 100)


def require_capability(capability: str) -> None:
	"""For handlers that take a second capability on top of their own, e.g. create + submit."""
	allowed, reason = get_settings().is_capability_allowed(capability)
	if not allowed:
		raise MCPBlocked(reason)
