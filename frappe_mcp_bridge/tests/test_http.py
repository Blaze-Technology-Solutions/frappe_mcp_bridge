# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

import json

import frappe

from frappe_mcp_bridge.api import mcp as api
from frappe_mcp_bridge.mcp import http
from frappe_mcp_bridge.tests.utils import IntegrationTestCase, as_user, mcp_settings, request


def rpc(method, params=None, request_id=1):
	message = {"jsonrpc": "2.0", "method": method}
	if params is not None:
		message["params"] = params
	if request_id is not None:
		message["id"] = request_id
	return json.dumps(message).encode()


class TestStreamableHTTP(IntegrationTestCase):
	def test_initialize_echoes_a_supported_version(self):
		status, body = http.handle(rpc("initialize", {"protocolVersion": "2025-06-18"}), "test")

		self.assertEqual(status, 200)
		self.assertEqual(body["result"]["protocolVersion"], "2025-06-18")
		self.assertIn("tools", body["result"]["capabilities"])
		self.assertIn("site_ping", body["result"]["instructions"])

	def test_initialize_falls_back_to_newest_version(self):
		_status, body = http.handle(rpc("initialize", {"protocolVersion": "1999-01-01"}), "test")
		self.assertEqual(body["result"]["protocolVersion"], http.SUPPORTED_PROTOCOL_VERSIONS[0])

	def test_notification_gets_202_and_no_body(self):
		self.assertEqual(http.handle(rpc("notifications/initialized", request_id=None), "test"), (202, None))

	def test_tools_list(self):
		_status, body = http.handle(rpc("tools/list"), "test")
		tools = {tool["name"]: tool for tool in body["result"]["tools"]}

		self.assertIn("site_ping", tools)
		self.assertIn("describe_site", tools)
		self.assertTrue(tools["get_document"]["annotations"]["readOnlyHint"])
		self.assertTrue(tools["delete_document"]["annotations"]["destructiveHint"])
		for tool in tools.values():
			self.assertEqual(tool["inputSchema"]["type"], "object")
			self.assertTrue(tool["description"], tool["name"])

	def test_unknown_method(self):
		_status, body = http.handle(rpc("nope/nothing"), "test")
		self.assertEqual(body["error"]["code"], http.METHOD_NOT_FOUND)

	def test_parse_error(self):
		status, body = http.handle(b"{not json", "test")
		self.assertEqual(status, 400)
		self.assertEqual(body["error"]["code"], http.PARSE_ERROR)

	def test_batch_skips_notifications(self):
		batch = json.dumps(
			[
				{"jsonrpc": "2.0", "id": 1, "method": "ping"},
				{"jsonrpc": "2.0", "method": "notifications/initialized"},
				{"jsonrpc": "2.0", "id": 2, "method": "ping"},
			]
		).encode()

		status, body = http.handle(batch, "test")
		self.assertEqual(status, 200)
		self.assertEqual([reply["id"] for reply in body], [1, 2])

	def test_tool_call_success_and_refusal(self):
		with mcp_settings(allow_write=1):
			_status, ok = http.handle(rpc("tools/call", {"name": "describe_site", "arguments": {}}), "test")
			_status, refused = http.handle(
				rpc(
					"tools/call",
					{
						"name": "update_document",
						"arguments": {"doctype": "ToDo", "name": "x", "values": {}, "dry_run": True},
					},
				),
				"test",
			)

		self.assertFalse(ok["result"]["isError"])
		self.assertIn(frappe.local.site, ok["result"]["content"][0]["text"])
		self.assertTrue(refused["result"]["isError"])
		self.assertIn("Read Only Mode", refused["result"]["content"][0]["text"])

	def test_site_ping_answers_while_disabled(self):
		with mcp_settings(enabled=0):
			_status, body = http.handle(rpc("tools/call", {"name": "site_ping", "arguments": {}}), "test")

		self.assertFalse(body["result"]["isError"])
		self.assertFalse(json.loads(body["result"]["content"][0]["text"])["enabled"])


class TestServeEndpoint(IntegrationTestCase):
	def test_guest_gets_401_challenge(self):
		with mcp_settings(allow_oauth=1), request(), as_user("Guest"):
			response = api.serve()

		self.assertEqual(response.status_code, 401)
		self.assertIn("resource_metadata=", response.headers["WWW-Authenticate"])

	def test_get_is_not_allowed(self):
		with mcp_settings(), request(method="GET"):
			response = api.serve()

		self.assertEqual(response.status_code, 405)
