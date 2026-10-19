# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Every provider that speaks OpenAI's Chat Completions: OpenAI and Azure OpenAI, plus
Gemini, DeepSeek, xAI, Mistral, Groq, OpenRouter, Ollama and other compatible servers."""

import hashlib
import json
from collections.abc import Callable

from frappe_mcp_bridge.chat.providers.base import (
	ChatRequest,
	ChatResult,
	LLMProvider,
	ProviderError,
	StopRequested,
	Usage,
	clean_tool_id,
	estimate_tokens,
)

ADAPTER = "openai"


class OpenAICompatibleProvider(LLMProvider):
	adapter = ADAPTER

	@property
	def label(self) -> str:
		return self.settings.get("provider_type") or "The provider"

	def _client(self):
		import openai

		common = {"timeout": float(self.timeout), "max_retries": 1, "default_headers": self.headers or None}

		if self.settings.get("azure"):
			return openai.AzureOpenAI(
				api_key=self.api_key,
				azure_endpoint=self.base_url,
				api_version=self.settings.get("api_version"),
				**common,
			)

		# Local servers such as Ollama ignore the key, but the SDK insists on one.
		return openai.OpenAI(api_key=self.api_key or "not-needed", base_url=self.base_url, **common)

	def stream(self, request: ChatRequest, on_delta: Callable[[str], None]) -> ChatResult:
		import openai

		kwargs = {
			"model": request.model_id,
			"messages": self.translate_messages(request),
			"stream": True,
			"stream_options": {"include_usage": True},
			self.settings.get("max_tokens_param", "max_tokens"): request.max_tokens,
		}
		if request.tools:
			kwargs["tools"] = [
				{
					"type": "function",
					"function": {
						"name": tool.name,
						"description": tool.description,
						"parameters": tool.input_schema,
					},
				}
				for tool in request.tools
			]
		if request.reasoning_effort:
			kwargs["reasoning_effort"] = request.reasoning_effort

		client = self._client()
		try:
			try:
				stream = client.chat.completions.create(**kwargs)
			except openai.BadRequestError as error:
				# A few compatible servers reject stream_options; usage is then estimated.
				if "stream_options" not in str(error):
					raise
				kwargs.pop("stream_options")
				stream = client.chat.completions.create(**kwargs)

			return self._read(stream, request, on_delta)
		except openai.APIStatusError as error:
			message = getattr(error, "message", None) or str(error)
			raise ProviderError(f"{self.label} returned {error.status_code}: {message}")
		except openai.APIConnectionError as error:
			raise ProviderError(f"Could not reach {self.label}: {error}")
		except openai.APIError as error:
			# An error event in the middle of a stream arrives as the bare base class.
			raise ProviderError(f"{self.label} failed mid-reply: {getattr(error, 'message', None) or error}")

	def _read(self, stream, request: ChatRequest, on_delta) -> ChatResult:
		text: list[str] = []
		calls: dict[int, dict] = {}
		finish_reason = usage = None
		stopped = False

		try:
			for chunk in stream:
				if getattr(chunk, "usage", None):
					usage = chunk.usage
				if not chunk.choices:
					continue

				choice = chunk.choices[0]
				delta = choice.delta
				if delta is not None and delta.content:
					text.append(delta.content)
					on_delta(delta.content)

				for call in (delta.tool_calls if delta is not None else None) or []:
					index = call.index if call.index is not None else len(calls)
					slot = calls.setdefault(index, {"id": "", "name": "", "arguments": "", "extra": None})
					if call.id:
						slot["id"] = call.id
					# Provider-specific data on a call, such as Gemini's thought signature,
					# which must come back with the call on the next turn.
					extra = getattr(call, "extra_content", None)
					if extra:
						slot["extra"] = extra
					if call.function is not None:
						if call.function.name:
							slot["name"] = call.function.name
						if call.function.arguments:
							slot["arguments"] += call.function.arguments

				if choice.finish_reason:
					finish_reason = choice.finish_reason
		except StopRequested:
			stopped = True
		finally:
			stream.close()

		output = "".join(text)
		response = getattr(stream, "response", None)
		request_id = response.headers.get("x-request-id") if response is not None else None

		if stopped:
			return ChatResult(
				text=output,
				usage=self._estimate(request, output),
				stop_reason="stopped",
				stopped=True,
				request_id=request_id,
			)

		tool_calls = [
			{
				"id": slot["id"] or f"call_{index}",
				"name": slot["name"],
				"args": _parse_arguments(slot["arguments"]),
				**({"extra": slot["extra"]} if slot["extra"] else {}),
			}
			for index, slot in sorted(calls.items())
			if slot["name"]
		]

		return ChatResult(
			text=output,
			tool_calls=tool_calls,
			usage=_usage(usage) if usage is not None else self._estimate(request, output),
			stop_reason=finish_reason,
			raw_provider=ADAPTER,
			request_id=request_id,
		)

	def translate_messages(self, request: ChatRequest) -> list[dict]:
		out: list[dict] = [{"role": "system", "content": request.system}] if request.system else []

		for message in request.messages:
			if message.role == "tool":
				out.append(
					{
						"role": "tool",
						"tool_call_id": self._tool_id(message.tool_call_id, "call"),
						"content": message.content or "(no output)",
					}
				)
				continue

			if message.role == "assistant":
				entry = {"role": "assistant", "content": message.content or None}
				if message.tool_calls:
					entry["tool_calls"] = [
						{
							"id": self._tool_id(call.get("id"), f"call_{index}"),
							"type": "function",
							"function": {
								"name": call["name"],
								"arguments": json.dumps(call.get("args") or {}, default=str),
							},
							# Only this kind of provider wrote it, so only it gets it back.
							**(
								{"extra_content": call["extra"]}
								if call.get("extra") and message.raw_provider == ADAPTER
								else {}
							),
						}
						for index, call in enumerate(message.tool_calls)
					]
				elif not message.content:
					entry["content"] = "(no reply)"
				out.append(entry)
				continue

			if not message.parts:
				out.append({"role": "user", "content": message.content or "(empty message)"})
				continue

			content = [_part(part) for part in message.parts]
			if message.content:
				content.append({"type": "text", "text": message.content})
			out.append({"role": "user", "content": content})

		return out

	def _tool_id(self, value: str | None, fallback: str) -> str:
		if self.settings.get("tool_id_style") == "alnum9":
			return hashlib.sha1((value or fallback).encode()).hexdigest()[:9]
		return clean_tool_id(value, fallback)

	def _estimate(self, request: ChatRequest, output: str) -> Usage:
		sent = request.system + "".join(message.content for message in request.messages)
		return Usage(
			input_tokens=estimate_tokens(sent), output_tokens=estimate_tokens(output), estimated=True
		)

	def list_models(self) -> list[str]:
		import openai

		try:
			return sorted(model.id for model in self._client().models.list())
		except openai.APIStatusError as error:
			message = getattr(error, "message", None) or str(error)
			raise ProviderError(f"{self.label} returned {error.status_code}: {message}")
		except openai.APIConnectionError as error:
			raise ProviderError(f"Could not reach {self.label}: {error}")


def _part(part: dict) -> dict:
	if part["type"] == "image":
		return {"type": "image_url", "image_url": {"url": f"data:{part['media_type']};base64,{part['data']}"}}

	if part["type"] == "pdf":
		return {
			"type": "file",
			"file": {
				"filename": part.get("name") or "document.pdf",
				"file_data": f"data:application/pdf;base64,{part['data']}",
			},
		}

	return {"type": "text", "text": f"Attached file {part.get('name')}:\n\n{part['data']}"}


def _parse_arguments(arguments: str) -> dict:
	try:
		parsed = json.loads(arguments or "{}")
	except ValueError:
		return {"__invalid_arguments__": arguments}

	return parsed if isinstance(parsed, dict) else {"__invalid_arguments__": arguments}


def _usage(usage) -> Usage:
	prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
	details = getattr(usage, "prompt_tokens_details", None)
	# OpenAI and Gemini report cache hits under prompt_tokens_details; DeepSeek uses its own field.
	cached = int(getattr(details, "cached_tokens", 0) or 0) or int(
		getattr(usage, "prompt_cache_hit_tokens", 0) or 0
	)
	completion_details = getattr(usage, "completion_tokens_details", None)

	return Usage(
		input_tokens=max(prompt - cached, 0),
		cached_input_tokens=cached,
		output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
		reasoning_tokens=int(getattr(completion_details, "reasoning_tokens", 0) or 0),
	)
