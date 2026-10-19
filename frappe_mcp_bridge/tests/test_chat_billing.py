# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe

from frappe_mcp_bridge.api import chat as api
from frappe_mcp_bridge.chat import billing
from frappe_mcp_bridge.chat.providers.base import Usage
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

USER = "ai-chat-billing@example.com"


class TestChatBilling(ChatTestCase):
	def setUp(self):
		super().setUp()
		make_user(USER, "AI Chat User", "AI Chat Test Finance")

	def model(self, **values):
		return frappe._dict(
			{
				"is_free": 0,
				"input_price": 3,
				"output_price": 15,
				"cached_input_price": 0.3,
				"cache_write_price": 3.75,
			}
			| values
		)

	def test_cost_counts_each_kind_of_token_at_its_price(self):
		usage = Usage(input_tokens=1000, cached_input_tokens=2000, cache_write_tokens=500, output_tokens=100)
		expected = (1000 * 3 + 2000 * 0.3 + 500 * 3.75 + 100 * 15) / 1_000_000
		self.assertAlmostEqual(billing.cost_usd(self.model(), usage), expected)

	def test_blank_cache_prices_fall_back_to_the_input_price(self):
		usage = Usage(cached_input_tokens=1_000_000, cache_write_tokens=1_000_000)
		model = self.model(cached_input_price=None, cache_write_price=None)
		self.assertAlmostEqual(billing.cost_usd(model, usage), 6.0)

	def test_free_models_cost_nothing(self):
		self.assertEqual(
			billing.cost_usd(self.model(is_free=1), Usage(input_tokens=10**6, output_tokens=10**6)), 0
		)

	def test_display_currency_is_stamped_with_its_rate(self):
		with chat_settings(display_currency="QAR", exchange_rate=3.64) as settings:
			stamp = billing.stamp_cost(self.model(), Usage(output_tokens=1_000_000), settings)
		self.assertAlmostEqual(stamp["cost_usd"], 15)
		self.assertAlmostEqual(stamp["cost_display"], 54.6)
		self.assertEqual((stamp["display_currency"], stamp["exchange_rate"]), ("QAR", 3.64))

		with chat_settings(display_currency="USD", exchange_rate=99) as settings:
			self.assertEqual(billing.display_currency(settings), ("USD", 1.0))

	def test_the_most_generous_role_wins_and_zero_means_no_limit(self):
		rows = [
			{"role": "AI Chat User", "monthly_budget": 10, "daily_messages": 20},
			{"role": "AI Chat Test Finance", "monthly_budget": 25, "daily_messages": 0},
			{"role": "AI Chat Test Stock", "monthly_budget": 500, "daily_messages": 500},
		]
		with chat_settings(role_budgets=rows) as settings:
			limits = billing.limits_for(USER, settings)
		# Stock User is not one of the user's roles, so it does not count.
		self.assertEqual((limits.monthly_budget, limits.daily_messages), (25, 0))

	def test_a_user_override_replaces_the_role_budgets(self):
		with chat_settings(
			role_budgets=[{"role": "AI Chat User", "monthly_budget": 10}],
			user_overrides=[{"user": USER, "monthly_budget": 2, "daily_messages": 3}],
		) as settings:
			limits = billing.limits_for(USER, settings)
		self.assertEqual((limits.monthly_budget, limits.daily_messages), (2, 3))

	def test_defaults_apply_when_no_role_row_matches(self):
		with chat_settings(default_monthly_budget=7, default_daily_messages=9) as settings:
			limits = billing.limits_for(USER, settings)
		self.assertEqual((limits.monthly_budget, limits.daily_messages), (7, 9))

	def test_a_reply_is_billed_on_the_message_and_the_chat(self):
		model = make_model(input_price=3, output_price=15)
		with chat_settings(display_currency="QAR", exchange_rate=3.64), as_user(USER), contained():
			with provider(FakeProvider(reply("Fine.", input_tokens=2000, output_tokens=500))):
				conversation = api.create_conversation(model=model)
				api.send_message(conversation["name"], "How are you?")

			usage = api.my_usage()

		message = frappe.get_doc(
			"AI Chat Message", {"conversation": conversation["name"], "role": "Assistant"}
		)
		expected = (2000 * 3 + 500 * 15) / 1_000_000
		self.assertAlmostEqual(message.cost_usd, expected)
		self.assertAlmostEqual(message.cost_display, expected * 3.64)
		self.assertEqual((message.input_tokens, message.output_tokens), (2000, 500))

		chat = frappe.get_doc("AI Chat Conversation", conversation["name"])
		self.assertAlmostEqual(chat.cost_usd, expected)
		# The chat's running totals keep the same precision as the messages they add up.
		self.assertAlmostEqual(chat.cost_display, expected * 3.64, places=6)
		self.assertEqual(chat.output_tokens, 500)
		self.assertAlmostEqual(usage["spent_usd"], expected)

	def test_the_daily_message_limit_refuses_the_next_message(self):
		model = make_model()
		with chat_settings(default_daily_messages=1), as_user(USER), contained():
			with provider(FakeProvider(reply("one"))):
				conversation = api.create_conversation(model=model)
				api.send_message(conversation["name"], "first")

			self.assertRaises(billing.BudgetExceeded, api.send_message, conversation["name"], "second")

		self.assertEqual(
			frappe.db.count("AI Chat Message", {"conversation": conversation["name"], "role": "User"}), 1
		)

	def test_the_monthly_budget_stops_the_chat(self):
		model = make_model(input_price=1000, output_price=1000)
		with chat_settings(default_monthly_budget=1), as_user(USER), contained():
			with provider(FakeProvider(reply("costly", input_tokens=1000, output_tokens=1000))):
				conversation = api.create_conversation(model=model)
				api.send_message(conversation["name"], "spend it all")

			self.assertRaises(billing.BudgetExceeded, api.send_message, conversation["name"], "more")

	def test_budget_alerts_go_out_once_per_threshold(self):
		model = make_model(input_price=1000, output_price=1000)
		with (
			chat_settings(default_monthly_budget=2, alert_recipients=[{"user": "Administrator"}]),
			as_user(USER),
			contained(),
			patch("frappe.sendmail") as sendmail,
		):
			# 1000 input + 1000 output tokens at $1000 per million: $2, all of the budget.
			with provider(FakeProvider(reply("costly", input_tokens=1000, output_tokens=1000))):
				conversation = api.create_conversation(model=model)
				api.send_message(conversation["name"], "spend it all")

			billing.check_alerts(USER, frappe.get_cached_doc("AI Chat Settings"))

		alerts = frappe.get_all("AI Chat Budget Alert", filters={"user": USER}, pluck="threshold")
		self.assertEqual(sorted(alerts), [80, 100])
		# Both thresholds crossed in one reply: one email, about the higher one.
		self.assertEqual(sendmail.call_count, 1)
		self.assertIn("used up", sendmail.call_args.kwargs["subject"])
		self.assertIn(frappe.db.get_value("User", USER, "email"), sendmail.call_args.kwargs["recipients"])
