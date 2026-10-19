# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""What each reply cost, what each user has spent, and the limits that stop them.

Costs are worked out in USD, the currency every provider bills in, from the prices on
the AI Model at the moment of the call. Both the USD figure and the display-currency
figure are frozen on the message, so a later price or exchange-rate change never
rewrites an old bill.
"""

from dataclasses import dataclass

import frappe
from frappe import _
from frappe.utils import cint, flt, fmt_money, get_first_day, getdate, today

from frappe_mcp_bridge.chat.access import user_override
from frappe_mcp_bridge.chat.providers.base import Usage

MILLION = 1_000_000
ALERT_THRESHOLDS = (80, 100)


class BudgetExceeded(frappe.ValidationError):
	pass


@dataclass
class Limits:
	monthly_budget: float = 0.0  # USD, 0 means no limit
	daily_messages: int = 0  # 0 means no limit


def cost_usd(model, usage: Usage) -> float:
	if model.is_free:
		return 0.0

	input_price = flt(model.input_price)
	# Blank cache prices: Anthropic bills reads at 0.1x and writes at 1.25x the input price;
	# for other providers the input price is the safe guess.
	claude = model.provider_type == "Anthropic"
	cached_price = flt(model.cached_input_price) or input_price * (0.1 if claude else 1)
	write_price = flt(model.cache_write_price) or input_price * (1.25 if claude else 1)

	return (
		usage.input_tokens * input_price
		+ usage.cached_input_tokens * cached_price
		+ usage.cache_write_tokens * write_price
		+ usage.output_tokens * flt(model.output_price)
	) / MILLION


def display_currency(settings) -> tuple[str, float]:
	"""(currency, units of it per USD)."""
	currency = settings.display_currency or "USD"
	if currency == "USD":
		return currency, 1.0

	return currency, flt(settings.exchange_rate) or 1.0


def month_key(date=None) -> str:
	return getdate(date or today()).strftime("%Y-%m")


def spent_this_month(user: str) -> float:
	total = frappe.db.sql(
		"""select coalesce(sum(cost_usd), 0) from `tabAI Chat Message`
		where user = %s and creation >= %s""",
		(user, get_first_day(today())),
	)
	return flt(total[0][0]) if total else 0.0


def messages_today(user: str) -> int:
	return frappe.db.count(
		"AI Chat Message",
		{"user": user, "role": "User", "is_note": 0, "creation": (">=", getdate(today()))},
	)


def limits_for(user: str, settings) -> Limits:
	"""The most generous of the user's role rows, where 0 (no limit) is the most generous
	of all; the defaults when no row matches. A figure in the user's override replaces
	the role's, and an override left at 0 keeps it."""
	roles = set(frappe.get_roles(user))
	rows = [row for row in settings.role_budgets or [] if row.role in roles]

	def most_generous(values):
		return 0 if any(not value for value in values) else max(values)

	if rows:
		limits = Limits(
			most_generous([flt(row.monthly_budget) for row in rows]),
			most_generous([cint(row.daily_messages) for row in rows]),
		)
	else:
		limits = Limits(flt(settings.default_monthly_budget), cint(settings.default_daily_messages))

	override = user_override(settings, user)
	if override:
		limits.monthly_budget = flt(override.monthly_budget) or limits.monthly_budget
		limits.daily_messages = cint(override.daily_messages) or limits.daily_messages

	return limits


def check_can_send(user: str, settings) -> None:
	"""Before a new message is accepted."""
	limits = limits_for(user, settings)

	if limits.daily_messages and messages_today(user) >= limits.daily_messages:
		raise BudgetExceeded(
			_("You have sent your {0} messages for today. The limit resets at midnight.").format(
				limits.daily_messages
			)
		)

	check_budget(user, settings, limits)


def check_budget(user: str, settings, limits: Limits | None = None) -> None:
	"""Before every model call, so a long run of tool calls stops at the limit too."""
	limits = limits or limits_for(user, settings)
	if not limits.monthly_budget:
		return

	spent = spent_this_month(user)
	if spent >= limits.monthly_budget:
		raise BudgetExceeded(
			_(
				"You have used your AI Chat budget for this month ({0} of {1}). Ask an admin to raise it."
			).format(format_usd(spent), format_usd(limits.monthly_budget))
		)


def format_usd(amount: float) -> str:
	return fmt_money(amount, precision=2, currency="USD")


def usage_summary(user: str, settings) -> dict:
	limits = limits_for(user, settings)
	spent = spent_this_month(user)
	currency, rate = display_currency(settings)

	return {
		"month": month_key(),
		"spent_usd": spent,
		"budget_usd": limits.monthly_budget,
		"percent": round(spent / limits.monthly_budget * 100, 1) if limits.monthly_budget else None,
		"spent_display": spent * rate,
		"budget_display": limits.monthly_budget * rate,
		"currency": currency,
		"exchange_rate": rate,
		"messages_today": messages_today(user),
		"daily_limit": limits.daily_messages,
	}


def check_alerts(user: str, settings) -> None:
	"""After a reply. One email per threshold per month; a jump past both sends one email."""
	if not settings.budget_alerts:
		return

	limits = limits_for(user, settings)
	if not limits.monthly_budget:
		return

	spent = spent_this_month(user)
	percent = spent / limits.monthly_budget * 100
	month = month_key()

	crossed = [
		threshold
		for threshold in ALERT_THRESHOLDS
		if percent >= threshold
		and not frappe.db.exists(
			"AI Chat Budget Alert", {"user": user, "month": month, "threshold": threshold}
		)
	]
	if not crossed:
		return

	recipients = _alert_recipients(user, settings)
	for threshold in crossed:
		frappe.get_doc(
			{
				"doctype": "AI Chat Budget Alert",
				"user": user,
				"month": month,
				"threshold": threshold,
				"spent_usd": spent,
				"budget_usd": limits.monthly_budget,
				"sent_to": ", ".join(recipients),
			}
		).insert(ignore_permissions=True)

	if recipients:
		_send_alert(user, max(crossed), spent, limits.monthly_budget, recipients, settings)


def _alert_recipients(user: str, settings) -> list[str]:
	users = [user] + [row.user for row in settings.alert_recipients or [] if row.user]
	emails = []
	for name in users:
		email = frappe.db.get_value("User", name, "email")
		if email and email not in emails:
			emails.append(email)
	return emails


def _send_alert(user: str, threshold: int, spent: float, budget: float, recipients: list[str], settings):
	currency, rate = display_currency(settings)
	full_name = frappe.utils.get_fullname(user)

	if threshold >= 100:
		subject = _("AI Chat budget used up: {0}").format(full_name)
		body = _(
			"{0} has used their whole AI Chat budget for {1}. New messages are refused until next month or until an admin raises the budget."
		)
	else:
		subject = _("AI Chat budget at {0}%: {1}").format(threshold, full_name)
		body = _("{0} has used {2}% of their AI Chat budget for {1}.")

	message = body.format(full_name, month_key(), threshold)
	message += "<br><br>" + _("Spent: {0} of {1}").format(format_usd(spent), format_usd(budget))
	if currency != "USD":
		message += " ({0} of {1})".format(
			fmt_money(spent * rate, currency=currency), fmt_money(budget * rate, currency=currency)
		)

	try:
		frappe.sendmail(recipients=recipients, subject=subject, message=message, now=False)
	except Exception:
		# A mail problem must not fail the reply; the alert row already records the attempt.
		frappe.log_error(title="AI Chat: could not send budget alert")


def stamp_cost(model, usage: Usage, settings) -> dict:
	"""The cost fields frozen onto a message."""
	usd = cost_usd(model, usage)
	currency, rate = display_currency(settings)
	return {
		"cost_usd": usd,
		"cost_display": usd * rate,
		"display_currency": currency,
		"exchange_rate": rate,
	}


@frappe.whitelist()
def active_users_card(filters=None) -> dict:
	"""Number card: people who chatted this month. Read through the permission hooks, so a
	manager counts everyone and anyone else counts only themselves, like the other cards."""
	rows = frappe.get_list(
		"AI Chat Message",
		filters={"role": "User", "creation": (">=", get_first_day(today()))},
		fields=["user"],
		distinct=True,
		limit_page_length=0,
	)
	return {"value": len(rows), "fieldtype": "Int"}
