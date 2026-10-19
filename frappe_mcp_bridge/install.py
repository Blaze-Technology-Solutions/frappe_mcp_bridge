# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

import frappe

# AI Chat User: open AI Chat, use the models their roles allow, see their own usage.
# AI Chat Manager: all of that, plus reading everyone's chats and usage.
AI_CHAT_ROLES = ("AI Chat User", "AI Chat Manager")


def after_install():
	set_default_settings()
	setup_ai_chat()


def set_default_settings():
	"""Ship switched off, read only, read capability only, System Manager only.

	Safe to re-run: it only fills what is still empty, so it never undoes a choice
	someone made in MCP Bridge Settings.
	"""
	settings = frappe.get_single("MCP Bridge Settings")

	if not settings.allowed_roles:
		settings.append("allowed_roles", {"role": "System Manager"})

	settings.flags.ignore_permissions = True
	settings.save()
	frappe.db.commit()


def setup_ai_chat():
	"""Roles, and AI Chat Settings left switched off. Safe to re-run, like the above."""
	for role in AI_CHAT_ROLES:
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(
				ignore_permissions=True
			)

	settings = frappe.get_single("AI Chat Settings")
	if not settings.display_currency:
		currency = frappe.db.get_default("currency") or "USD"
		rate = _usd_rate(currency)
		# Without a known rate, show USD rather than USD amounts labelled as another currency.
		settings.display_currency = currency if rate else "USD"
		settings.exchange_rate = rate or 1

	settings.flags.ignore_permissions = True
	settings.save()
	frappe.db.commit()


def _usd_rate(currency: str) -> float | None:
	if currency == "USD":
		return 1.0

	if not frappe.db.exists("DocType", "Currency Exchange"):
		return None

	rate = frappe.get_all(
		"Currency Exchange",
		filters={"from_currency": "USD", "to_currency": currency},
		fields=["exchange_rate"],
		order_by="date desc",
		limit=1,
	)
	return float(rate[0].exchange_rate) if rate and rate[0].exchange_rate else None
