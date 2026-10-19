# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Run Live Check on an AI Model: what AI Chat needs from a model, tried against the real
provider with its real key, so a model can be trusted before anyone chats with it.

Two or three short requests: a streamed reply, then a tool call and the answer built on its
result. They cost a fraction of a cent, which is billed like any reply, to an archived chat of
the person who ran the check, so the usage report still matches the provider's invoice.
"""

import time

import frappe
from frappe import _

from frappe_mcp_bridge.chat import billing, orchestrator
from frappe_mcp_bridge.chat.access import get_chat_settings
from frappe_mcp_bridge.chat.providers import from_doc
from frappe_mcp_bridge.chat.providers.base import ChatMessage, ChatRequest, ProviderError, ToolDefinition

SYSTEM = "You are being tested by the site's administrator. Follow the instructions exactly."
NUMBER = "4217"
TOOL = ToolDefinition(
	name="get_check_number",
	description="Returns today's check number. Call it whenever you are asked for the check number.",
	input_schema={"type": "object", "properties": {}},
)
MAX_TOKENS = 2000


@frappe.whitelist(methods=["POST"])
def run(model: str) -> dict:
	# Not frappe.only_for: on Frappe v15 it lets everyone through while tests run.
	if "System Manager" not in frappe.get_roles():
		frappe.throw(_("Only a System Manager can run a live check."), frappe.PermissionError)
	return LiveCheck(frappe.get_doc("AI Model", model)).run()


class LiveCheck:
	def __init__(self, model):
		self.model = model
		self.settings = get_chat_settings()
		self.checks: list[dict] = []
		self.results = []  # every ChatResult, for the totals
		self.log: list[tuple[str, object]] = []  # ("User" | "Tool", text) or ("Assistant", ChatResult)
		self.started = time.monotonic()

	def run(self) -> dict:
		try:
			provider = from_doc(frappe.get_doc("AI Provider", self.model.provider))
			self.reply(provider)
			if self.model.supports_tools:
				self.tool_round(provider)
			else:
				self.note(_("Tools"), None, _("Skipped: the model is not ticked Supports Tools."))
		except (ProviderError, frappe.ValidationError) as error:
			self.note(_("Reaches the provider"), False, str(error))

		return {
			"ok": all(check["ok"] is not False for check in self.checks),
			"checks": self.checks,
			"input_tokens": sum(r.usage.input_tokens + r.usage.cached_input_tokens for r in self.results),
			"output_tokens": sum(r.usage.output_tokens for r in self.results),
			"cost_usd": sum(billing.cost_usd(self.model, r.usage) for r in self.results),
			"seconds": round(time.monotonic() - self.started, 1),
			"conversation": self.record(),
		}

	def note(self, check: str, ok: bool | None, detail: str) -> None:
		self.checks.append({"check": check, "ok": ok, "detail": detail[:500]})

	def call(self, provider, messages: list[ChatMessage], tools: list):
		deltas = []
		request = ChatRequest(
			model_id=self.model.model_id,
			system=SYSTEM,
			messages=messages,
			tools=tools,
			max_tokens=min(int(self.model.max_output_tokens or MAX_TOKENS), MAX_TOKENS),
			reasoning_effort=self.model.reasoning_effort or None,
		)
		started = time.monotonic()
		result = provider.stream(request, deltas.append)
		result.latency_ms = int((time.monotonic() - started) * 1000)
		self.results.append(result)
		self.log.append(("Assistant", result))
		return result, len(deltas)

	def ask(self, prompt: str) -> ChatMessage:
		self.log.append(("User", prompt))
		return ChatMessage(role="user", content=prompt)

	def reply(self, provider) -> None:
		result, deltas = self.call(provider, [self.ask("Reply with exactly one word: ready")], [])
		answer = result.text.strip()[:80] or _("(nothing)")
		self.note(
			_("Streams a reply"),
			bool(deltas and result.text.strip()),
			_("Answered: {0}").format(answer)
			if deltas
			else _("Nothing was streamed. Answered: {0}").format(answer),
		)

		usage = result.usage
		self.note(
			_("Reports usage for billing"),
			not usage.estimated,
			_("No usage came back, so its costs would be estimated.")
			if usage.estimated
			else _("{0} tokens in, {1} out").format(
				usage.input_tokens + usage.cached_input_tokens, usage.output_tokens
			),
		)

	def tool_round(self, provider) -> None:
		history = [self.ask("What is the check number? Use the tool, then answer with just the number.")]
		first, _deltas = self.call(provider, history, [TOOL])

		called = [call for call in first.tool_calls if call.get("name") == TOOL.name]
		self.note(
			_("Calls a tool"),
			bool(called),
			_("Called {0}").format(TOOL.name)
			if called
			else _("No tool call. Answered: {0}").format(first.text.strip()[:80] or _("(nothing)")),
		)
		if not called:
			return

		history.append(
			ChatMessage(
				role="assistant",
				content=first.text,
				tool_calls=first.tool_calls,
				raw=first.raw,
				raw_provider=first.raw_provider,
			)
		)
		for call in first.tool_calls:
			output = NUMBER if call is called[0] else "Not used."
			self.log.append(("Tool", output))
			history.append(
				ChatMessage(role="tool", content=output, tool_call_id=call["id"], tool_name=call["name"])
			)

		second, _deltas = self.call(provider, history, [TOOL])
		self.note(
			_("Answers from the tool's result"),
			NUMBER in second.text,
			_("Answered: {0}").format(second.text.strip()[:80] or _("(nothing)")),
		)

	def record(self) -> str | None:
		"""Bill the calls to an archived chat of the person who ran the check."""
		if not self.results:
			return None

		chat = frappe.get_doc(
			{
				"doctype": "AI Chat Conversation",
				"user": frappe.session.user,
				"model": self.model.name,
				"title": _("Live check: {0}").format(self.model.model_label)[:140],
				"status": "Archived",
			}
		).insert(ignore_permissions=True)

		for role, entry in self.log:
			if role == "Assistant":
				message = orchestrator.insert_message(
					chat, orchestrator.assistant_values(self.model, entry, self.settings)
				)
				orchestrator.add_to_totals(chat.name, message)
			else:
				values = {"role": role, "content": entry}
				if role == "Tool":
					values["tool_name"] = TOOL.name
				orchestrator.insert_message(chat, values)
		return chat.name
