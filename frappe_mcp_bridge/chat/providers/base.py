# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""What every provider adapter speaks.

The orchestrator only ever deals in these shapes. Each adapter translates them to its
vendor's wire format on the way out and back again on the way in, so adding a provider
never means touching the chat loop.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# Tool call ids travel between providers when someone switches model mid-chat, and
# Anthropic accepts only these characters in them.
TOOL_ID_PATTERN = re.compile(r"[^a-zA-Z0-9_-]")


@dataclass
class ChatMessage:
	role: str  # user | assistant | tool
	content: str = ""
	# User attachments: {"type": "image" | "pdf" | "text", "name", "media_type", "data"}.
	# image and pdf carry base64 in data; text carries the extracted text.
	parts: list[dict] = field(default_factory=list)
	tool_calls: list[dict] = field(default_factory=list)  # [{"id", "name", "args"}]
	tool_call_id: str | None = None
	tool_name: str | None = None
	is_error: bool = False
	# The provider's own content for an assistant turn, replayed unchanged to the same
	# kind of provider. Anthropic needs its thinking blocks back exactly as they came.
	raw: Any = None
	raw_provider: str | None = None


@dataclass
class ToolDefinition:
	name: str
	description: str
	input_schema: dict


@dataclass
class Usage:
	input_tokens: int = 0  # uncached input
	cached_input_tokens: int = 0
	cache_write_tokens: int = 0
	output_tokens: int = 0  # includes reasoning tokens
	reasoning_tokens: int = 0
	estimated: bool = False


@dataclass
class ChatResult:
	text: str = ""
	tool_calls: list[dict] = field(default_factory=list)
	usage: Usage = field(default_factory=Usage)
	stop_reason: str | None = None
	raw: Any = None
	raw_provider: str | None = None
	request_id: str | None = None
	stopped: bool = False  # the user pressed Stop mid-reply
	latency_ms: int = 0


@dataclass
class ChatRequest:
	model_id: str
	system: str
	messages: list[ChatMessage]
	tools: list[ToolDefinition]
	max_tokens: int
	reasoning_effort: str | None = None


class StopRequested(Exception):
	"""Raised from the delta callback to abandon a stream."""


class ProviderError(Exception):
	"""The provider failed. The message is fit to show the user; the fallback model is tried next."""


class LLMProvider:
	"""One configured AI Provider. Subclasses implement stream, list_models and ping."""

	adapter = "base"

	def __init__(self, *, api_key: str, base_url: str | None, timeout: int, headers: dict, settings: dict):
		self.api_key = api_key
		self.base_url = base_url or None
		self.timeout = timeout
		self.headers = headers or {}
		# Provider-type specifics from the presets, e.g. which max-tokens parameter to send.
		self.settings = settings or {}

	def stream(self, request: ChatRequest, on_delta: Callable[[str], None]) -> ChatResult:
		raise NotImplementedError

	def list_models(self) -> list[str]:
		raise NotImplementedError

	def ping(self) -> str:
		"""A short success description. Raises on failure."""
		models = self.list_models()
		return f"Connected. {len(models)} models available."


def clean_tool_id(value: str | None, fallback: str) -> str:
	cleaned = TOOL_ID_PATTERN.sub("_", value or "")
	return cleaned[:64] or fallback


def estimate_tokens(text: str) -> int:
	"""About four characters a token. Only for providers that report no usage."""
	return max(1, len(text) // 4) if text else 0
