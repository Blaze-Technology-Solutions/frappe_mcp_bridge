# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""The HTTP bridge to frappe_mcp_bridge.api.mcp on the target site."""

import json
import logging

import httpx
from mcp.server.mcpserver.exceptions import ToolError

from .config import Config

log = logging.getLogger("frappe_mcp_bridge_client.client")

EXECUTE = "frappe_mcp_bridge.api.mcp.execute"
PING = "frappe_mcp_bridge.api.mcp.ping"
LIST_TOOLS = "frappe_mcp_bridge.api.mcp.list_tools"

# Enough of a traceback to see where it broke, not so much that it floods the context.
TRACEBACK_CHARS = 2_000


class SiteError(ToolError):
	"""Anything the site refused, could not do, or could not be asked.

	Subclasses ToolError so the SDK treats it as an anticipated failure and passes the
	message through to the model. A plain exception would reach Claude as the bare
	"Error executing tool <name>", which says nothing it can act on.
	"""


class Bridge:
	def __init__(self, config: Config):
		self.config = config
		self._client = httpx.AsyncClient(
			base_url=config.endpoint,
			timeout=config.timeout,
			verify=config.verify_ssl,
			headers={
				"Authorization": config.auth_header,
				"Accept": "application/json",
				"Content-Type": "application/json",
				"User-Agent": f"frappe-mcp-bridge/{config.client_name}",
			},
		)

	async def aclose(self) -> None:
		await self._client.aclose()

	async def ping(self) -> dict:
		return await self._post(PING, {})

	async def catalogue(self) -> dict:
		return await self._post(LIST_TOOLS, {})

	async def call(
		self,
		tool: str,
		params: dict | None = None,
		*,
		writes: bool = False,
		dry_run: bool | None = None,
	) -> dict:
		if writes and self.config.read_only:
			raise SiteError(
				f"{tool} would change data, but this MCP server is in read-only mode. "
				f"Set FRAPPE_MCP_READ_ONLY=false in the .env and restart the server to allow it."
			)

		payload: dict = {
			"tool": tool,
			"params": _drop_unset(params or {}),
			"client": self.config.client_name,
		}

		resolved_dry_run = dry_run if dry_run is not None else self.config.dry_run
		if resolved_dry_run is not None:
			payload["dry_run"] = resolved_dry_run

		log.info("call %s dry_run=%s", tool, payload.get("dry_run", "site default"))
		envelope = await self._post(EXECUTE, payload)

		if not envelope.get("ok", False):
			raise SiteError(_format_error(tool, envelope))

		return envelope

	async def _post(self, method: str, payload: dict) -> dict:
		try:
			response = await self._client.post(f"/{method}", json=payload)
		except httpx.TimeoutException:
			raise SiteError(
				f"{self.config.url} did not answer within {self.config.timeout:.0f}s. "
				f"For a long import or patch, raise FRAPPE_MCP_TIMEOUT or pass background=true."
			)
		except httpx.HTTPError as exception:
			raise SiteError(f"Could not reach {self.config.url}: {exception}")

		if response.status_code in (401, 403):
			raise SiteError(
				f"{self.config.url} rejected the credentials ({response.status_code}). "
				f"Check FRAPPE_MCP_API_KEY and FRAPPE_MCP_API_SECRET, and that the user still has "
				f"an allowed role in MCP Bridge Settings.\n{_server_messages(response)}"
			)

		if response.status_code == 404:
			raise SiteError(
				f"{method} is not available on {self.config.url}. The frappe_mcp_bridge app "
				f"may be missing or out of date there; install it and run bench migrate on the site."
			)

		try:
			body = response.json()
		except ValueError:
			raise SiteError(
				f"{self.config.url} returned {response.status_code} and not JSON:\n{response.text[:500]}"
			)

		if response.status_code >= 400:
			raise SiteError(
				f"{self.config.url} returned {response.status_code}: "
				f"{body.get('exc_type') or ''} {_server_messages(response)}".strip()
			)

		message = body.get("message")
		if message is None:
			raise SiteError(f"Unexpected response from {method}: {json.dumps(body)[:500]}")

		return message


def _format_error(tool: str, envelope: dict) -> str:
	error = envelope.get("error") or {}
	lines = [f"{tool} failed: {error.get('message') or error.get('type') or 'unknown error'}"]

	for message in error.get("messages") or []:
		if message and message != error.get("message"):
			lines.append(f"  {message}")

	if envelope.get("log"):
		lines.append(f"  logged as MCP Bridge Log {envelope['log']}")

	traceback = error.get("traceback")
	if traceback:
		lines.append("")
		lines.append(traceback[-TRACEBACK_CHARS:])

	return "\n".join(lines)


def _server_messages(response: httpx.Response) -> str:
	"""Frappe hides the readable reason in a JSON-encoded list inside the body."""
	try:
		raw = response.json().get("_server_messages")
	except ValueError:
		return ""

	if not raw:
		return ""

	try:
		return " ".join(json.loads(entry).get("message", "") for entry in json.loads(raw))
	except (ValueError, AttributeError):
		return str(raw)


def _drop_unset(params: dict) -> dict:
	"""Claude fills optional arguments with null; the site treats a key as supplied."""
	return {key: value for key, value in params.items() if value is not None}
