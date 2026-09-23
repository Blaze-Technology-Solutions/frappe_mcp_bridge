# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

import json
import random
from unittest.mock import patch

import frappe
from werkzeug.exceptions import NotFound

from frappe_mcp_bridge import oauth
from frappe_mcp_bridge.tests.utils import SITE_URL, IntegrationTestCase, mcp_settings, request

CLAUDE_CODE_REDIRECT = "http://localhost:53682/callback"


def random_ip():
	# The registration endpoint is rate limited per IP; keep repeated runs apart.
	return f"10.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(1, 254)}"


class TestRedirectURIs(IntegrationTestCase):
	def test_acceptable(self):
		for uri in (
			"https://claude.ai/api/mcp/auth_callback",
			CLAUDE_CODE_REDIRECT,
			"http://127.0.0.1:1455/auth/callback",
			"http://[::1]:8080/cb",
		):
			self.assertTrue(oauth._acceptable_redirect(uri), uri)

	def test_refused(self):
		for uri in (
			"http://evil.example.com/cb",
			"https://claude.ai/cb#fragment",
			"cursor://callback",
			"https://claude.ai/cb other",
			"not a url",
		):
			self.assertFalse(oauth._acceptable_redirect(uri), uri)


class TestRegistration(IntegrationTestCase):
	def register(self, payload, **settings):
		with mcp_settings(allow_oauth=1, **settings), request(json=payload, ip=random_ip()):
			return oauth.register_client()

	def test_claude_code_registration(self):
		response = self.register(
			{
				"client_name": "Claude Code (prod)",
				"redirect_uris": [CLAUDE_CODE_REDIRECT],
				"grant_types": ["authorization_code", "refresh_token"],
				"response_types": ["code"],
				"token_endpoint_auth_method": "none",
			}
		)

		self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
		body = json.loads(response.get_data())
		self.assertNotIn("client_secret", body)

		client = frappe.get_doc("OAuth Client", body["client_id"])
		self.assertEqual(client.default_redirect_uri, CLAUDE_CODE_REDIRECT)
		self.assertEqual(client.app_name, "Claude Code (prod) (MCP)")
		self.assertFalse(client.skip_authorization)
		if client.meta.has_field("token_endpoint_auth_method"):
			self.assertEqual(client.token_endpoint_auth_method, "None")

	def test_confidential_client_gets_a_secret(self):
		response = self.register({"redirect_uris": ["https://claude.ai/api/mcp/auth_callback"]})

		self.assertEqual(response.status_code, 201)
		self.assertIn("client_secret", json.loads(response.get_data()))

	def test_bad_redirect_is_refused(self):
		response = self.register({"redirect_uris": ["http://evil.example.com/cb"]})

		self.assertEqual(response.status_code, 400)
		self.assertEqual(json.loads(response.get_data())["error"], "invalid_client_metadata")

	def test_off_without_frappe_discovery_is_not_found(self):
		with (
			mcp_settings(allow_oauth=0),
			request(json={"redirect_uris": [CLAUDE_CODE_REDIRECT]}, ip=random_ip()),
			patch.object(oauth, "frappe_publishes_discovery", return_value=False),
		):
			self.assertRaises(NotFound, oauth.register_client)


class TestDiscovery(IntegrationTestCase):
	def test_challenge(self):
		with mcp_settings(allow_oauth=1), request():
			on = oauth.unauthorized_response()

		with mcp_settings(allow_oauth=0), request():
			off = oauth.unauthorized_response()

		self.assertEqual(on.status_code, 401)
		self.assertEqual(
			on.headers["WWW-Authenticate"],
			f'Bearer resource_metadata="{SITE_URL}/.well-known/oauth-protected-resource"',
		)
		self.assertNotIn("resource_metadata", off.headers["WWW-Authenticate"])

	def test_metadata_points_at_frappe_and_our_registration(self):
		with request():
			resource = oauth.protected_resource_metadata()
			server = oauth.authorization_server_metadata()

		self.assertEqual(resource["resource"], SITE_URL)
		self.assertEqual(resource["authorization_servers"], [SITE_URL])
		self.assertEqual(server["issuer"], SITE_URL)
		self.assertTrue(server["authorization_endpoint"].endswith("frappe.integrations.oauth2.authorize"))
		self.assertTrue(server["registration_endpoint"].endswith(oauth.REGISTER_METHOD))
		self.assertEqual(server["code_challenge_methods_supported"], ["S256"])

	def test_v15_renderer(self):
		with (
			mcp_settings(allow_oauth=1),
			request(method="GET", path="/.well-known/oauth-authorization-server"),
			patch.object(oauth, "frappe_publishes_discovery", return_value=False),
		):
			renderer = oauth.WellKnownRenderer(".well-known/oauth-authorization-server", 200)
			self.assertTrue(renderer.can_render())
			body = json.loads(renderer.render().get_data())

			self.assertTrue(
				oauth.WellKnownRenderer(".well-known/oauth-protected-resource/api", 200).can_render()
			)
			self.assertFalse(oauth.WellKnownRenderer("about", 200).can_render())

		self.assertEqual(body["issuer"], SITE_URL)

	def test_renderer_stays_out_of_the_way_when_off(self):
		with (
			mcp_settings(allow_oauth=0),
			patch.object(oauth, "frappe_publishes_discovery", return_value=False),
		):
			renderer = oauth.WellKnownRenderer(".well-known/oauth-authorization-server", 200)
			self.assertFalse(renderer.can_render())
