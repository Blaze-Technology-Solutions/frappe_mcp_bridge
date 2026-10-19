# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

import json

import frappe

from frappe_mcp_bridge.chat.providers import anthropic as claude
from frappe_mcp_bridge.chat.providers import from_doc, openai_compatible
from frappe_mcp_bridge.chat.providers.base import ChatMessage, ChatRequest
from frappe_mcp_bridge.chat.scrub import scrub_secrets
from frappe_mcp_bridge.tests.chat_utils import ChatTestCase
from frappe_mcp_bridge.tests.utils import IntegrationTestCase

HISTORY = [
	ChatMessage(
		role="user", content="What is on ToDo 1?", parts=[{"type": "text", "name": "a.csv", "data": "x,y"}]
	),
	ChatMessage(
		role="assistant",
		content="Let me look.",
		tool_calls=[
			{"id": "toolu_1", "name": "get_document", "args": {"doctype": "ToDo", "name": "1"}},
			{"id": "toolu_2", "name": "count_documents", "args": {"doctype": "ToDo"}},
		],
	),
	ChatMessage(
		role="tool", content='{"description": "milk"}', tool_call_id="toolu_1", tool_name="get_document"
	),
	ChatMessage(
		role="tool", content="Blocked", tool_call_id="toolu_2", tool_name="count_documents", is_error=True
	),
	ChatMessage(role="user", content="Thanks"),
]


def request(messages=HISTORY):
	return ChatRequest(model_id="m", system="Be brief.", messages=messages, tools=[], max_tokens=100)


def adapter(**settings):
	return openai_compatible.OpenAICompatibleProvider(
		api_key="k", base_url="http://localhost", timeout=5, headers={}, settings=settings
	)


class TestAnthropicTranslation(IntegrationTestCase):
	def test_tool_results_of_one_turn_go_back_together(self):
		out = claude.translate_messages(HISTORY)

		self.assertEqual([m["role"] for m in out], ["user", "assistant", "user", "user"])
		results = out[2]["content"]
		self.assertEqual([block["tool_use_id"] for block in results], ["toolu_1", "toolu_2"])
		self.assertTrue(results[1]["is_error"])
		self.assertEqual(out[1]["content"][1]["type"], "tool_use")

	def test_attachments_lead_the_user_message(self):
		blocks = claude.translate_messages(HISTORY)[0]["content"]
		self.assertEqual(blocks[0]["type"], "text")
		self.assertIn("Attached file a.csv", blocks[0]["text"])
		self.assertEqual(blocks[-1]["text"], "What is on ToDo 1?")

		image = claude._part({"type": "image", "media_type": "image/png", "data": "AAA"})
		self.assertEqual(image["source"], {"type": "base64", "media_type": "image/png", "data": "AAA"})
		pdf = claude._part({"type": "pdf", "name": "q.pdf", "data": "BBB"})
		self.assertEqual(pdf["type"], "document")

	def test_claudes_own_blocks_are_replayed_unchanged(self):
		raw = [{"type": "thinking", "thinking": "", "signature": "sig"}, {"type": "text", "text": "Hi"}]
		out = claude.translate_messages(
			[ChatMessage(role="assistant", content="Hi", raw=raw, raw_provider="anthropic")]
		)
		self.assertEqual(out[0]["content"], raw)

		# Another provider's raw content is never sent to Claude.
		out = claude.translate_messages(
			[ChatMessage(role="assistant", content="Hi", raw=raw, raw_provider="openai")]
		)
		self.assertEqual(out[0]["content"], [{"type": "text", "text": "Hi"}])

	def test_usage_keeps_cache_reads_and_writes_apart(self):
		usage = claude._usage(
			frappe._dict(
				input_tokens=10, cache_read_input_tokens=200, cache_creation_input_tokens=30, output_tokens=5
			)
		)
		self.assertEqual(
			(usage.input_tokens, usage.cached_input_tokens, usage.cache_write_tokens), (10, 200, 30)
		)


class TestOpenAITranslation(IntegrationTestCase):
	def test_shape_of_a_tool_conversation(self):
		out = adapter().translate_messages(request())

		self.assertEqual([m["role"] for m in out], ["system", "user", "assistant", "tool", "tool", "user"])
		call = out[2]["tool_calls"][0]
		self.assertEqual(call["type"], "function")
		self.assertEqual(json.loads(call["function"]["arguments"]), {"doctype": "ToDo", "name": "1"})
		self.assertEqual(out[3]["tool_call_id"], "toolu_1")
		self.assertEqual(out[1]["content"][0]["type"], "text")

	def test_mistral_ids_are_nine_characters_and_still_match(self):
		out = adapter(tool_id_style="alnum9").translate_messages(request())
		call_id = out[2]["tool_calls"][0]["id"]
		self.assertEqual(len(call_id), 9)
		self.assertEqual(out[3]["tool_call_id"], call_id)

	def test_usage_subtracts_cache_hits_from_input(self):
		openai_usage = frappe._dict(
			prompt_tokens=1000,
			completion_tokens=50,
			prompt_tokens_details=frappe._dict(cached_tokens=800),
			completion_tokens_details=frappe._dict(reasoning_tokens=20),
		)
		usage = openai_compatible._usage(openai_usage)
		self.assertEqual(
			(usage.input_tokens, usage.cached_input_tokens, usage.reasoning_tokens), (200, 800, 20)
		)

		deepseek_usage = frappe._dict(prompt_tokens=1000, completion_tokens=50, prompt_cache_hit_tokens=600)
		self.assertEqual(openai_compatible._usage(deepseek_usage).cached_input_tokens, 600)

	def test_bad_tool_arguments_are_flagged_not_guessed(self):
		self.assertEqual(openai_compatible._parse_arguments('{"a": 1}'), {"a": 1})
		self.assertIn("__invalid_arguments__", openai_compatible._parse_arguments('{"a": '))
		self.assertIn("__invalid_arguments__", openai_compatible._parse_arguments("[1, 2]"))

	def test_images_and_pdfs_become_data_urls(self):
		image = openai_compatible._part({"type": "image", "media_type": "image/jpeg", "data": "AAA"})
		self.assertEqual(image["image_url"]["url"], "data:image/jpeg;base64,AAA")
		pdf = openai_compatible._part({"type": "pdf", "name": "q.pdf", "data": "BBB"})
		self.assertEqual(pdf["file"]["file_data"], "data:application/pdf;base64,BBB")


class TestProviderSetup(ChatTestCase):
	def test_presets_fill_the_base_url_and_a_key_is_required(self):
		doc = frappe.get_doc(
			{"doctype": "AI Provider", "provider_name": "Preset Test", "provider_type": "DeepSeek"}
		)
		self.assertRaises(frappe.ValidationError, doc.insert)

		doc.api_key = "sk-test"
		doc.insert()
		self.assertEqual(doc.base_url, "https://api.deepseek.com")
		self.assertEqual(from_doc(doc).api_key, "sk-test")

	def test_models_need_prices_before_they_are_enabled(self):
		frappe.get_doc(
			{"doctype": "AI Provider", "provider_name": "Price Test", "provider_type": "Ollama"}
		).insert()
		model = frappe.get_doc(
			{
				"doctype": "AI Model",
				"model_label": "Unpriced",
				"provider": "Price Test",
				"model_id": "x",
				"enabled": 1,
			}
		)
		self.assertRaises(frappe.ValidationError, model.insert)

		model.is_free = 1
		model.insert()


class TestScrub(IntegrationTestCase):
	def test_keys_and_passwords_are_removed(self):
		text, found = scrub_secrets(
			"key sk-proj-abcdefghijklmnopqrstuvwxyz123456 and password: hunter22 and AKIAABCDEFGHIJKLMNOP"
		)
		self.assertNotIn("hunter22", text)
		self.assertNotIn("sk-proj-", text)
		self.assertNotIn("AKIA", text)
		self.assertEqual(set(found), {"OpenAI API key", "password", "AWS access key"})

	def test_card_numbers_go_but_barcodes_and_order_numbers_stay(self):
		text, found = scrub_secrets("Card 4111 1111 1111 1111, barcode 5901234123457, SO-2026-00012")
		self.assertIn("[card number removed]", text)
		self.assertIn("5901234123457", text)
		self.assertIn("SO-2026-00012", text)
		self.assertEqual(found, ["card number"])

	def test_ordinary_text_is_untouched(self):
		message = "How many Sales Orders did we raise in March for 1,250 units?"
		self.assertEqual(scrub_secrets(message), (message, []))
