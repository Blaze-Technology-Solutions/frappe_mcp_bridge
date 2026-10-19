# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Claude, through Anthropic's own SDK and Messages API."""

from collections.abc import Callable

from frappe_mcp_bridge.chat.providers.base import (
	ChatMessage,
	ChatRequest,
	ChatResult,
	LLMProvider,
	ProviderError,
	StopRequested,
	Usage,
	clean_tool_id,
	estimate_tokens,
)

ADAPTER = "anthropic"


class AnthropicProvider(LLMProvider):
	adapter = ADAPTER

	def _client(self):
		import anthropic

		return anthropic.Anthropic(
			api_key=self.api_key,
			base_url=self.base_url,
			timeout=float(self.timeout),
			max_retries=1,
			default_headers=self.headers or None,
		)

	def stream(self, request: ChatRequest, on_delta: Callable[[str], None]) -> ChatResult:
		import anthropic

		kwargs = {
			"model": request.model_id,
			"max_tokens": request.max_tokens,
			"system": request.system,
			"messages": translate_messages(request.messages),
			# Caches the tool list, the system prompt and the history so far, which every
			# later turn of the chat sends again unchanged.
			"cache_control": {"type": "ephemeral"},
		}
		if request.tools:
			# Tool inputs here are small, so they are left to the server to validate rather
			# than streamed eagerly and checked by hand.
			kwargs["tools"] = [
				{"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}
				for tool in request.tools
			]
		if request.reasoning_effort:
			kwargs["output_config"] = {"effort": request.reasoning_effort}

		streamed: list[str] = []
		try:
			with self._client().messages.stream(**kwargs) as stream:
				try:
					for event in stream:
						if event.type == "text" and event.text:
							streamed.append(event.text)
							on_delta(event.text)
				except StopRequested:
					return _stopped(stream, "".join(streamed), request)

				final = stream.get_final_message()
				request_id = getattr(stream, "request_id", None)
		except anthropic.APIStatusError as error:
			raise ProviderError(_status_message(error))
		except anthropic.APIConnectionError as error:
			raise ProviderError(f"Could not reach Anthropic: {error}")

		return ChatResult(
			text="".join(block.text for block in final.content if block.type == "text"),
			tool_calls=[
				{"id": block.id, "name": block.name, "args": block.input}
				for block in final.content
				if block.type == "tool_use"
			],
			usage=_usage(final.usage),
			stop_reason=final.stop_reason,
			raw=[block.model_dump(mode="json", exclude_none=True) for block in final.content],
			raw_provider=ADAPTER,
			request_id=request_id,
		)

	def list_models(self) -> list[str]:
		import anthropic

		try:
			return sorted(model.id for model in self._client().models.list())
		except anthropic.APIStatusError as error:
			raise ProviderError(_status_message(error))
		except anthropic.APIConnectionError as error:
			raise ProviderError(f"Could not reach Anthropic: {error}")


def translate_messages(messages: list[ChatMessage]) -> list[dict]:
	out: list[dict] = []
	results: list[dict] = []

	def flush_results():
		# Every tool result of one assistant turn goes back in a single user message.
		if results:
			out.append({"role": "user", "content": list(results)})
			results.clear()

	for message in messages:
		if message.role == "tool":
			results.append(
				{
					"type": "tool_result",
					"tool_use_id": clean_tool_id(message.tool_call_id, "tool_call"),
					"content": message.content or "(no output)",
					"is_error": bool(message.is_error),
				}
			)
			continue

		flush_results()

		if message.role == "assistant":
			if message.raw_provider == ADAPTER and message.raw:
				# Claude's own blocks go back exactly as they came, thinking included.
				out.append({"role": "assistant", "content": message.raw})
				continue

			blocks = [{"type": "text", "text": message.content}] if message.content else []
			blocks += [
				{
					"type": "tool_use",
					"id": clean_tool_id(call.get("id"), f"tool_call_{index}"),
					"name": call["name"],
					"input": call.get("args") if isinstance(call.get("args"), dict) else {},
				}
				for index, call in enumerate(message.tool_calls)
			]
			out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "(no reply)"}]})
			continue

		blocks = [_part(part) for part in message.parts]
		if message.content:
			blocks.append({"type": "text", "text": message.content})
		out.append({"role": "user", "content": blocks or [{"type": "text", "text": "(empty message)"}]})

	flush_results()
	return out


def _part(part: dict) -> dict:
	if part["type"] == "image":
		return {
			"type": "image",
			"source": {"type": "base64", "media_type": part["media_type"], "data": part["data"]},
		}

	if part["type"] == "pdf":
		return {
			"type": "document",
			"title": part.get("name") or "document.pdf",
			"source": {"type": "base64", "media_type": "application/pdf", "data": part["data"]},
		}

	return {"type": "text", "text": f"Attached file {part.get('name')}:\n\n{part['data']}"}


def _usage(usage) -> Usage:
	return Usage(
		input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
		cached_input_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
		cache_write_tokens=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
		output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
	)


def _stopped(stream, text: str, request: ChatRequest) -> ChatResult:
	"""The user pressed Stop. Input was reported when the stream opened; output is estimated."""
	snapshot = getattr(stream, "current_message_snapshot", None)
	usage = _usage(snapshot.usage) if snapshot is not None else Usage()
	if not (usage.input_tokens or usage.cached_input_tokens):
		usage.input_tokens = estimate_tokens(request.system + "".join(m.content for m in request.messages))
	usage.output_tokens = max(usage.output_tokens, estimate_tokens(text))
	usage.estimated = True

	stream.close()
	return ChatResult(text=text, usage=usage, stop_reason="stopped", stopped=True)


def _status_message(error) -> str:
	message = getattr(error, "message", None) or str(error)
	return f"Anthropic returned {error.status_code}: {message}"
