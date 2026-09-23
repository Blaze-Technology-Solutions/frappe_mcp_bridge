# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

import ipaddress

import frappe
from frappe import _
from frappe.model.document import Document

# Capability name -> the checkbox that unlocks it. Handlers declare a capability, the gate
# looks it up here, so adding a tool never means touching the gate.
CAPABILITY_FIELDS = {
	"read": "allow_read",
	"write": "allow_write",
	"submit": "allow_submit_cancel",
	"delete": "allow_delete",
	"import": "allow_data_import",
	"patch": "allow_run_patches",
	"sql": "allow_sql_query",
	"admin": "allow_admin_actions",
	"script": "allow_server_script",
}

# Capabilities that change data. Read Only Mode refuses all of them.
WRITE_CAPABILITIES = {"write", "submit", "delete", "import", "patch", "admin", "script"}

# Refused whatever the settings say: these hold the credentials that would let MCP
# widen its own permissions.
ALWAYS_BLOCKED_DOCTYPES = {
	"MCP Bridge Settings",
	"User",
	"Role",
	"Role Profile",
	"Custom Role",
	"DocPerm",
	"Custom DocPerm",
	"User Permission",
	"OAuth Bearer Token",
	"OAuth Client",
	"Access Log",
	"Webhook",
	"Server Script",
	"System Settings",
}


class MCPBridgeSettings(Document):
	def validate(self):
		self.validate_ips()
		self.clamp_limits()

	def validate_ips(self):
		for entry in self.get_allowed_ips():
			try:
				ipaddress.ip_network(entry, strict=False)
			except ValueError:
				frappe.throw(_("{0} is not a valid IP address or CIDR range").format(frappe.bold(entry)))

	def clamp_limits(self):
		# A zero or negative ceiling would read as "unlimited" to the handlers, which is the
		# opposite of what someone clearing the field means.
		self.max_rows = max(int(self.max_rows or 0), 1)
		self.max_write_batch = max(int(self.max_write_batch or 0), 1)
		self.max_requests_per_hour = max(int(self.max_requests_per_hour or 0), 0)
		self.log_retention_days = max(int(self.log_retention_days or 0), 0)

	def on_update(self):
		frappe.clear_cache(doctype="MCP Bridge Settings")

	# Helpers used by frappe_mcp_bridge.mcp.gate ------------------------------------------------

	def get_allowed_ips(self) -> list[str]:
		return [line.strip() for line in (self.allowed_ips or "").splitlines() if line.strip()]

	def get_allowed_roles(self) -> list[str]:
		roles = [row.role for row in (self.allowed_roles or []) if row.role]
		return roles or ["System Manager"]

	def capability_field(self, capability: str) -> str | None:
		return CAPABILITY_FIELDS.get(capability)

	def is_capability_allowed(self, capability: str) -> tuple[bool, str]:
		"""Return (allowed, reason). Reason is only meaningful when not allowed."""
		if not self.enabled:
			return False, _("MCP access is disabled in MCP Bridge Settings.")

		field = CAPABILITY_FIELDS.get(capability)
		if not field:
			return False, _("Unknown capability {0}.").format(capability)

		if self.read_only_mode and capability in WRITE_CAPABILITIES:
			return False, _("Read Only Mode is on, so {0} tools are refused.").format(capability)

		if not self.get(field):
			return False, _("{0} is not ticked in MCP Bridge Settings.").format(_(self.meta.get_label(field)))

		return True, ""

	def is_doctype_allowed(self, doctype: str) -> tuple[bool, str]:
		if doctype in ALWAYS_BLOCKED_DOCTYPES:
			return False, _("{0} can never be reached through MCP.").format(doctype)

		blocked = {row.document_type for row in (self.blocked_doctypes or [])}
		if doctype in blocked:
			return False, _("{0} is on the blocked list in MCP Bridge Settings.").format(doctype)

		if self.restrict_doctypes:
			allowed = {row.document_type for row in (self.allowed_doctypes or [])}
			if doctype not in allowed:
				return False, _("{0} is not on the allowed list in MCP Bridge Settings.").format(doctype)

		return True, ""

	def is_ip_allowed(self, ip: str | None) -> bool:
		allowed = self.get_allowed_ips()
		if not allowed:
			return True

		if not ip:
			return False

		try:
			address = ipaddress.ip_address(ip)
		except ValueError:
			return False

		return any(address in ipaddress.ip_network(entry, strict=False) for entry in allowed)


def get_settings() -> MCPBridgeSettings:
	return frappe.get_cached_doc("MCP Bridge Settings")


# Connection helpers for the settings form ---------------------------------------------

SERVE_METHOD = "frappe_mcp_bridge.api.mcp.serve"


@frappe.whitelist()
def get_connection_info() -> dict:
	"""The endpoint and ready-to-paste client config, with the secret left as a placeholder."""
	frappe.only_for("System Manager")
	return _connection_info("<api_key>", "<api_secret>")


@frappe.whitelist(methods=["POST"])
def generate_keys_for_mcp_user() -> dict:
	"""Issue a fresh key pair for MCP User. The secret is shown this once and never stored readable."""
	from frappe.core.doctype.user.user import generate_keys

	frappe.only_for("System Manager")

	user = frappe.db.get_single_value("MCP Bridge Settings", "mcp_user")
	if not user:
		frappe.throw(_("Set MCP User and save before generating keys."))

	if user in ("Guest", "Administrator"):
		frappe.throw(_("Pick a dedicated user for MCP rather than {0}.").format(user))

	if not frappe.db.get_value("User", user, "enabled"):
		frappe.throw(_("{0} is disabled.").format(user))

	keys = generate_keys(user)

	return {"user": user, **_connection_info(keys["api_key"], keys["api_secret"])}


def _connection_info(api_key: str, api_secret: str) -> dict:
	url = frappe.utils.get_url().rstrip("/")
	endpoint = f"{url}/api/method/{SERVE_METHOD}"
	# Claude Code and Codex both want a short identifier: letters, digits, _ and -.
	name = frappe.scrub(url.split("://", 1)[-1].split(":", 1)[0]).replace(".", "_") or "frappe"
	auth = f"token {api_key}:{api_secret}"

	return {
		"site_url": url,
		"endpoint": endpoint,
		"server_name": name,
		"claude_code": f'claude mcp add --transport http {name} {endpoint} --header "Authorization: {auth}"',
		"codex": (
			f"# ~/.codex/config.toml, then export FRAPPE_MCP_AUTH='{auth}'\n"
			f"[mcp_servers.{name}]\n"
			f'url = "{endpoint}"\n'
			f'env_http_headers = {{ "Authorization" = "FRAPPE_MCP_AUTH" }}'
		),
		"stdio_env": (
			f"FRAPPE_MCP_URL={url}\nFRAPPE_MCP_API_KEY={api_key}\nFRAPPE_MCP_API_SECRET={api_secret}"
		),
	}
