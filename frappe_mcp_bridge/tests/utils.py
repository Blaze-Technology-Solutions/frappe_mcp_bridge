# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Shared test helpers. Works on Frappe v15 and v16."""

import contextlib
from unittest.mock import patch

import frappe
from frappe.utils import set_request

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # Frappe v15
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

SITE_URL = "http://mcp-test.localhost"
SERVE_PATH = "/api/method/frappe_mcp_bridge.api.mcp.serve"

DEFAULTS = {
	"enabled": 1,
	"read_only_mode": 1,
	"allow_read": 1,
	"allow_api_keys": 1,
	"allow_oauth": 0,
	"log_requests": 0,
	"max_rows": 500,
	"max_write_batch": 100,
}


@contextlib.contextmanager
def mcp_settings(masked=(), **values):
	"""Swap in an in-memory MCP Bridge Settings for the duration.

	Code under test reads the settings through frappe.get_cached_doc, so nothing is
	written to the site. Commits are suppressed too, because the HTTP handler commits
	after each tool call and the test transaction must stay rollback-able.

	masked takes fieldnames, or (doctype, fieldname) pairs.
	"""
	settings = frappe.new_doc("MCP Bridge Settings")
	settings.update({**DEFAULTS, **values})

	for entry in masked:
		doctype, fieldname = entry if isinstance(entry, tuple) else (None, entry)
		settings.append("masked_fields", {"document_type": doctype, "fieldname": fieldname})

	original = frappe.get_cached_doc

	def cached_doc(doctype, *args, **kwargs):
		if doctype == "MCP Bridge Settings":
			return settings
		return original(doctype, *args, **kwargs)

	with patch("frappe.get_cached_doc", side_effect=cached_doc), patch.object(frappe.local.db, "commit"):
		yield settings


@contextlib.contextmanager
def request(path=SERVE_PATH, method="POST", headers=None, json=None, ip="127.0.0.1"):
	"""Pretend the code is running inside an HTTP request."""
	previous_request = getattr(frappe.local, "request", None)
	previous_ip = getattr(frappe.local, "request_ip", None)

	set_request(path=path, method=method, headers=headers or {}, json=json, base_url=SITE_URL)
	frappe.local.request_ip = ip

	try:
		yield frappe.local.request
	finally:
		frappe.local.request = previous_request
		frappe.local.request_ip = previous_ip


@contextlib.contextmanager
def as_user(user: str):
	previous = frappe.session.user
	frappe.set_user(user)
	try:
		yield
	finally:
		frappe.set_user(previous)


def make_todo(description: str = "mcp bridge test") -> str:
	return frappe.get_doc({"doctype": "ToDo", "description": description}).insert().name
