# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""OAuth sign-in for MCP clients.

Frappe is the authorization server: its own login page, its own Allow screen, its own
authorize and token endpoints and its own bearer tokens. This module adds the pieces an
MCP client needs to find all that on its own:

- The 401 challenge that tells a client where to start (`unauthorized_response`).
- The discovery documents. Frappe v16 publishes them itself once OAuth Settings says so,
  and `publish_discovery` says so. v15 has none, so `WellKnownRenderer` serves them.
- Dynamic client registration that accepts http://localhost redirects, which is what
  Claude Code and Codex register. v16's own endpoint only accepts loopback IPs outside
  developer mode, so hooks.py routes it here; v15 has no such endpoint at all.
"""

import json
import time
from typing import ClassVar
from urllib.parse import urlparse

import frappe
from frappe.rate_limiter import rate_limit
from werkzeug.exceptions import NotFound
from werkzeug.wrappers import Response

REGISTER_METHOD = "frappe_mcp_bridge.oauth.register_client"
AUTHORIZE_METHOD = "frappe.integrations.oauth2.authorize"
TOKEN_METHOD = "frappe.integrations.oauth2.get_token"
REVOKE_METHOD = "frappe.integrations.oauth2.revoke_token"

# RFC 8252: native apps receive the code on a loopback redirect. Claude Code and Codex
# both use localhost by name, which Frappe's own registration refuses in production.
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}

AUTH_METHODS = {
	"none": "None",
	"client_secret_basic": "Client Secret Basic",
	"client_secret_post": "Client Secret Post",
}

GRANT_TYPES = {"authorization_code", "refresh_token"}


def oauth_allowed() -> bool:
	from frappe_mcp_bridge.frappe_mcp_bridge.doctype.mcp_bridge_settings.mcp_bridge_settings import (
		get_settings,
	)

	settings = get_settings()
	return bool(settings.enabled and settings.allow_oauth)


def frappe_publishes_discovery() -> bool:
	"""True on v16, whose app.py answers /.well-known/oauth-* itself."""
	from frappe.integrations import oauth2

	return hasattr(oauth2, "handle_wellknown")


def site_origin() -> str:
	"""The origin the client actually used, so metadata matches the URL it connected to."""
	request = getattr(frappe.local, "request", None)
	if request:
		parsed = urlparse(request.url)
		return f"{parsed.scheme}://{parsed.netloc}"

	return frappe.utils.get_url().rstrip("/")


def resource_metadata_url() -> str:
	return f"{site_origin()}/.well-known/oauth-protected-resource"


def protected_resource_metadata() -> dict:
	"""RFC 9728. The resource is the site origin, which covers the MCP endpoint's path."""
	origin = site_origin()
	return {
		"resource": origin,
		"authorization_servers": [origin],
		"bearer_methods_supported": ["header"],
		"resource_name": "Frappe MCP Bridge",
	}


def authorization_server_metadata() -> dict:
	"""RFC 8414, pointing at Frappe's own endpoints and our registration endpoint."""
	origin = site_origin()
	return {
		"issuer": origin,
		"authorization_endpoint": f"{origin}/api/method/{AUTHORIZE_METHOD}",
		"token_endpoint": f"{origin}/api/method/{TOKEN_METHOD}",
		"revocation_endpoint": f"{origin}/api/method/{REVOKE_METHOD}",
		"registration_endpoint": f"{origin}/api/method/{REGISTER_METHOD}",
		"response_types_supported": ["code"],
		"response_modes_supported": ["query"],
		"grant_types_supported": sorted(GRANT_TYPES),
		"token_endpoint_auth_methods_supported": sorted(AUTH_METHODS),
		"code_challenge_methods_supported": ["S256"],
	}


def publish_discovery() -> None:
	"""On v16, switch on the OAuth Settings that publish discovery and allow registration.

	Registration itself still lands on our endpoint (see override_whitelisted_methods in
	hooks.py), so localhost redirects work. On v15 there is nothing to switch on:
	WellKnownRenderer serves the documents whenever OAuth sign-in is allowed.
	"""
	if not frappe_publishes_discovery():
		return

	oauth_settings = frappe.get_single("OAuth Settings")
	oauth_settings.show_auth_server_metadata = 1
	oauth_settings.show_protected_resource_metadata = 1
	oauth_settings.enable_dynamic_client_registration = 1
	oauth_settings.flags.ignore_permissions = True
	oauth_settings.save()


def unauthorized_response() -> Response:
	"""The 401 a client needs to begin signing in: MCP clients only start OAuth on a 401."""
	body = {
		"error": "unauthorized",
		"error_description": "Send Authorization: token <api_key>:<api_secret>, or sign in with OAuth.",
	}
	response = Response(json.dumps(body), status=401, mimetype="application/json")

	if oauth_allowed():
		response.headers["WWW-Authenticate"] = f'Bearer resource_metadata="{resource_metadata_url()}"'
	else:
		response.headers["WWW-Authenticate"] = f'Bearer realm="{site_origin()}"'

	return response


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=30, seconds=60 * 60)
def register_client():
	"""RFC 7591 dynamic client registration for MCP clients.

	Registering grants nothing by itself: the person still has to sign in to this site and
	press Allow before the client gets a token, and every call is then gated as usual.
	"""
	if not oauth_allowed():
		if frappe_publishes_discovery():
			# This endpoint replaces v16's own through override_whitelisted_methods. While
			# MCP OAuth is off, behave exactly as Frappe would have.
			from frappe.integrations.oauth2 import register_client as frappe_register_client

			return frappe_register_client()

		raise NotFound

	data = frappe.request.get_json(silent=True)
	if not isinstance(data, dict):
		return _registration_error("Request body must be a JSON object.")

	error, client = _validate_registration(data)
	if error:
		return _registration_error(error)

	doc = _create_client(client)

	body = {
		"client_id": doc.client_id or doc.name,
		"client_id_issued_at": int(time.time()),
		"client_name": doc.app_name,
		"redirect_uris": client["redirect_uris"],
		"grant_types": sorted(GRANT_TYPES),
		"response_types": ["code"],
		"token_endpoint_auth_method": client["auth_method"],
		"scope": doc.scopes,
	}

	if client["auth_method"] != "none":
		body["client_secret"] = doc.client_secret
		body["client_secret_expires_at"] = 0

	return Response(json.dumps(body), status=201, mimetype="application/json")


def _validate_registration(data: dict) -> tuple[str | None, dict]:
	redirect_uris = data.get("redirect_uris")
	if not isinstance(redirect_uris, list) or not redirect_uris:
		return "redirect_uris is required.", {}

	for uri in redirect_uris:
		if not isinstance(uri, str) or not _acceptable_redirect(uri):
			return (
				f"{uri!r} is not an acceptable redirect URI. Use https, or http on localhost / "
				"127.0.0.1 / [::1].",
				{},
			)

	grant_types = data.get("grant_types") or ["authorization_code"]
	if not set(grant_types) <= GRANT_TYPES:
		return "Only authorization_code and refresh_token grants are supported.", {}

	response_types = data.get("response_types") or ["code"]
	if any(response_type != "code" for response_type in response_types):
		return "Only the code response type is supported.", {}

	auth_method = data.get("token_endpoint_auth_method") or "client_secret_basic"
	if auth_method not in AUTH_METHODS:
		return f"token_endpoint_auth_method must be one of {', '.join(sorted(AUTH_METHODS))}.", {}

	scope = data.get("scope")
	if scope is not None and not isinstance(scope, str):
		return "scope must be a space-separated string.", {}

	return None, {
		"redirect_uris": redirect_uris,
		"auth_method": auth_method,
		"client_name": str(data.get("client_name") or "MCP client").strip()[:120],
		"scope": (scope or "all").strip(),
		"extra": {
			key: data[key]
			for key in ("client_uri", "logo_uri", "tos_uri", "policy_uri", "software_id", "software_version")
			if isinstance(data.get(key), str)
		},
	}


def _acceptable_redirect(uri: str) -> bool:
	if any(char.isspace() for char in uri):
		# Frappe stores redirect URIs space-separated.
		return False

	parsed = urlparse(uri)
	if parsed.fragment or not parsed.netloc:
		return False

	if parsed.scheme == "https":
		return True

	return parsed.scheme == "http" and (parsed.hostname or "") in LOOPBACK_HOSTS


def _create_client(client: dict):
	from frappe.oauth import get_url_delimiter

	doc = frappe.new_doc("OAuth Client")
	doc.app_name = f"{client['client_name']} (MCP)"
	doc.scopes = client["scope"]
	# Frappe splits redirect_uris on this delimiter when it checks a redirect.
	doc.redirect_uris = get_url_delimiter().join(client["redirect_uris"])
	doc.default_redirect_uri = client["redirect_uris"][0]
	doc.response_type = "Code"
	doc.grant_type = "Authorization Code"
	doc.skip_authorization = 0

	# v16 only: public clients and the descriptive metadata.
	if doc.meta.has_field("token_endpoint_auth_method"):
		doc.token_endpoint_auth_method = AUTH_METHODS[client["auth_method"]]

	for key, value in client["extra"].items():
		if doc.meta.has_field(key):
			doc.set(key, value[:140])

	doc.insert(ignore_permissions=True)
	return doc


def _registration_error(description: str) -> Response:
	body = {"error": "invalid_client_metadata", "error_description": description}
	return Response(json.dumps(body), status=400, mimetype="application/json")


# v15 discovery ------------------------------------------------------------------------

try:
	from frappe.website.page_renderers.base_renderer import BaseRenderer
except ImportError:  # pragma: no cover - every supported Frappe has it
	BaseRenderer = object


class WellKnownRenderer(BaseRenderer):
	"""Serves /.well-known/oauth-* on v15. v16 answers these paths before any renderer runs."""

	DOCUMENTS: ClassVar[dict] = {
		".well-known/oauth-protected-resource": protected_resource_metadata,
		".well-known/oauth-authorization-server": authorization_server_metadata,
	}

	def _document(self):
		path = (self.path or "").strip("/")
		for prefix, builder in self.DOCUMENTS.items():
			# RFC 9728 and 8414 both allow a path suffix after the well-known name.
			if path == prefix or path.startswith(f"{prefix}/"):
				return builder

		return None

	def can_render(self):
		return bool(self._document()) and not frappe_publishes_discovery() and oauth_allowed()

	def render(self):
		response = Response(json.dumps(self._document()()), mimetype="application/json")
		response.headers["Access-Control-Allow-Origin"] = "*"
		return response
