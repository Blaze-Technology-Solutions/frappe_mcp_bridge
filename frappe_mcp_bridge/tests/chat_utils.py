# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Helpers for the AI Chat tests: a scripted provider, in-memory settings, test users,
and a guard that keeps the code under test from committing or rolling back the test's
own transaction."""

import contextlib
from unittest.mock import patch

import frappe

from frappe_mcp_bridge.chat.providers.base import ChatResult, StopRequested, Usage
from frappe_mcp_bridge.tests.utils import IntegrationTestCase

CHAT_DEFAULTS = {
	"enabled": 1,
	"erp_access": "Read and write with approval",
	"max_tool_rounds": 8,
	"tool_result_chars": 20_000,
	"approval_expiry_minutes": 30,
	"reply_timeout": 900,
	"display_currency": "USD",
	"exchange_rate": 1,
	"budget_alerts": 1,
	"allow_attachments": 1,
	"max_attachment_mb": 10,
	"max_attachments": 5,
}

PROVIDER = "Test Provider"


class FakeProvider:
	"""Plays back a script: each step is a ChatResult, an exception, or a function of the request."""

	def __init__(self, *steps):
		self.steps = list(steps)
		self.requests = []

	def stream(self, request, on_delta):
		self.requests.append(request)
		if not self.steps:
			raise AssertionError("FakeProvider ran out of scripted replies")

		step = self.steps.pop(0)
		if isinstance(step, Exception):
			raise step
		if callable(step):
			step = step(request)

		try:
			if step.text:
				on_delta(step.text)
		except StopRequested:
			return ChatResult(text=step.text, usage=step.usage, stop_reason="stopped", stopped=True)

		return step


def reply(text="", tool_calls=None, input_tokens=1000, output_tokens=200, cached=0, stop_reason=None):
	return ChatResult(
		text=text,
		tool_calls=tool_calls or [],
		usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens, cached_input_tokens=cached),
		stop_reason=stop_reason or ("tool_use" if tool_calls else "end_turn"),
		raw_provider="openai",
	)


@contextlib.contextmanager
def chat_settings(**values):
	"""In-memory AI Chat Settings, read through frappe.get_cached_doc like the real one."""
	settings = frappe.new_doc("AI Chat Settings")
	tables = {
		key: values.pop(key)
		for key in ("role_budgets", "user_overrides", "alert_recipients")
		if key in values
	}
	settings.update({**CHAT_DEFAULTS, **values})
	for table, rows in tables.items():
		for row in rows:
			settings.append(table, row)

	original = frappe.get_cached_doc

	def cached_doc(doctype, *args, **kwargs):
		if doctype == "AI Chat Settings":
			return settings
		return original(doctype, *args, **kwargs)

	with patch("frappe.get_cached_doc", side_effect=cached_doc):
		yield settings


@contextlib.contextmanager
def contained():
	"""No commits, no full rollbacks (savepoint rollbacks still run, so dry runs still
	undo themselves), background jobs run inline, realtime events are collected."""
	db = frappe.local.db
	original_rollback = db.rollback
	events = []

	def rollback(*args, save_point=None, **kwargs):
		if save_point:
			return original_rollback(save_point=save_point)

	def enqueue(method, **kwargs):
		kwargs = {key: kwargs[key] for key in ("conversation", "request_ip") if key in kwargs}
		return frappe.get_attr(method)(**kwargs)

	def publish(event, message=None, **kwargs):
		events.append((event, message))

	# Error Log rows are deferred inserts that outlive the test's rollback, and budget alerts
	# would try to send real mail; neither may leave anything on the site.
	with (
		patch.object(db, "commit"),
		patch.object(db, "rollback", side_effect=rollback),
		patch("frappe.enqueue", side_effect=enqueue),
		patch("frappe.publish_realtime", side_effect=publish),
		patch("frappe.sendmail"),
		patch("frappe.log_error"),
	):
		yield events


@contextlib.contextmanager
def provider(fake):
	with patch("frappe_mcp_bridge.chat.orchestrator.get_provider", return_value=fake):
		yield fake


def make_role(name: str) -> str:
	"""CI sites carry only Frappe's own roles, so tests bring the ones they need."""
	if not frappe.db.exists("Role", name):
		frappe.get_doc({"doctype": "Role", "role_name": name, "desk_access": 1}).insert(
			ignore_permissions=True
		)
	return name


def make_user(email: str, *roles: str) -> str:
	for role in roles:
		make_role(role)

	if frappe.db.exists("User", email):
		user = frappe.get_doc("User", email)
		user.add_roles(*roles)
		return email

	frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": email.split("@")[0],
			"send_welcome_email": 0,
			"roles": [{"role": role} for role in roles],
		}
	).insert(ignore_permissions=True)
	return email


def make_model(label="Test Model", **values) -> str:
	if not frappe.db.exists("AI Provider", PROVIDER):
		frappe.get_doc(
			{
				"doctype": "AI Provider",
				"provider_name": PROVIDER,
				"provider_type": "Custom (OpenAI-compatible)",
				"base_url": "http://127.0.0.1:9",
			}
		).insert(ignore_permissions=True)

	roles = [make_role(role) for role in values.pop("allowed_roles", [])]
	doc = frappe.get_doc(
		{
			"doctype": "AI Model",
			"model_label": label,
			"provider": PROVIDER,
			"model_id": label.lower().replace(" ", "-"),
			"enabled": 1,
			"input_price": 3,
			"output_price": 15,
			"may_read_erp_data": 1,
			**values,
			"allowed_roles": [{"role": role} for role in roles],
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


class ChatTestCase(IntegrationTestCase):
	"""Each test runs in its own savepoint, so records made in one never leak into the next."""

	def setUp(self):
		frappe.db.savepoint("ai_chat_test")
		frappe.set_user("Administrator")

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback(save_point="ai_chat_test")
		frappe.clear_cache(doctype="AI Model")
