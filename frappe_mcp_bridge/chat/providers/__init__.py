# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Provider presets, and the factory that turns an AI Provider row into an adapter.

Two adapters cover every provider type: Claude speaks Anthropic's Messages API, and
everything else speaks OpenAI's Chat Completions, which Gemini, DeepSeek, xAI, Mistral,
Groq, OpenRouter, Ollama and most self-hosted servers also accept.
"""

import frappe
from frappe import _

from frappe_mcp_bridge.chat.providers.base import LLMProvider

# provider_type -> how to reach it. base_url fills an empty Base URL on the provider.
PRESETS = {
	"Anthropic": {"adapter": "anthropic", "base_url": ""},
	"OpenAI": {
		"adapter": "openai",
		"base_url": "https://api.openai.com/v1",
		"max_tokens_param": "max_completion_tokens",
	},
	"Azure OpenAI": {
		"adapter": "openai",
		"base_url": "",
		"azure": True,
		"max_tokens_param": "max_completion_tokens",
	},
	"Google Gemini": {
		"adapter": "openai",
		"base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
	},
	"DeepSeek": {"adapter": "openai", "base_url": "https://api.deepseek.com"},
	"xAI": {"adapter": "openai", "base_url": "https://api.x.ai/v1"},
	# Mistral rejects tool call ids that are not exactly nine letters and digits.
	"Mistral": {"adapter": "openai", "base_url": "https://api.mistral.ai/v1", "tool_id_style": "alnum9"},
	"Groq": {"adapter": "openai", "base_url": "https://api.groq.com/openai/v1"},
	"OpenRouter": {"adapter": "openai", "base_url": "https://openrouter.ai/api/v1"},
	"Ollama": {"adapter": "openai", "base_url": "http://localhost:11434/v1", "key_optional": True},
	"Custom (OpenAI-compatible)": {"adapter": "openai", "base_url": "", "key_optional": True},
}


def preset(provider_type: str) -> dict:
	return PRESETS.get(provider_type) or PRESETS["Custom (OpenAI-compatible)"]


def get_provider(provider_name: str) -> LLMProvider:
	doc = frappe.get_doc("AI Provider", provider_name)
	if not doc.enabled:
		frappe.throw(_("AI Provider {0} is disabled.").format(provider_name))

	return from_doc(doc)


def from_doc(doc) -> LLMProvider:
	settings = dict(preset(doc.provider_type))
	settings["api_version"] = doc.api_version
	settings["provider_type"] = doc.provider_type

	api_key = doc.get_password("api_key", raise_exception=False) or ""
	if not api_key and not settings.get("key_optional"):
		frappe.throw(_("AI Provider {0} has no API key.").format(doc.name))

	kwargs = {
		"api_key": api_key,
		"base_url": doc.base_url or settings.get("base_url") or None,
		"timeout": int(doc.timeout or 120),
		"headers": frappe.parse_json(doc.extra_headers) if doc.extra_headers else {},
		"settings": settings,
	}

	if settings["adapter"] == "anthropic":
		from frappe_mcp_bridge.chat.providers.anthropic import AnthropicProvider

		return AnthropicProvider(**kwargs)

	from frappe_mcp_bridge.chat.providers.openai_compatible import OpenAICompatibleProvider

	return OpenAICompatibleProvider(**kwargs)
