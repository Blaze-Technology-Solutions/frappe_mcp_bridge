# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from frappe_mcp_bridge.chat.providers import from_doc, preset
from frappe_mcp_bridge.chat.providers.base import ProviderError


class AIProvider(Document):
	def validate(self):
		settings = preset(self.provider_type)
		if not self.base_url and settings.get("base_url"):
			self.base_url = settings["base_url"]

		if not self.base_url and self.provider_type != "Anthropic":
			frappe.throw(_("Set the Base URL for a {0} provider.").format(self.provider_type))

		if self.base_url:
			self.base_url = self.base_url.strip()
			if not self.base_url.startswith(("https://", "http://")):
				frappe.throw(_("The Base URL must start with https:// or http://."))

		if self.extra_headers:
			headers = frappe.parse_json(self.extra_headers)
			if not isinstance(headers, dict) or not all(
				isinstance(key, str) and isinstance(value, str) for key, value in headers.items()
			):
				frappe.throw(_("Extra Headers must be a JSON object of text values."))

		if not settings.get("key_optional") and not self.api_key and self.is_new():
			frappe.throw(_("Enter the API key for {0}.").format(self.provider_name))


@frappe.whitelist(methods=["POST"])
def test_connection(provider: str) -> dict:
	frappe.only_for("System Manager")
	doc = frappe.get_doc("AI Provider", provider)

	try:
		result = from_doc(doc).ping()
		ok = True
	except (ProviderError, frappe.ValidationError) as error:
		result, ok = str(error), False

	doc.db_set({"last_tested_on": now_datetime(), "last_test_result": result[:1000]})
	return {"ok": ok, "message": result}


@frappe.whitelist()
def fetch_models(provider: str) -> list[str]:
	frappe.only_for("System Manager")
	try:
		return from_doc(frappe.get_doc("AI Provider", provider)).list_models()
	except ProviderError as error:
		frappe.throw(str(error))


@frappe.whitelist(methods=["POST"])
def add_models(provider: str, model_ids) -> list[str]:
	"""Create AI Model rows for the picked ids. They start disabled: prices come first."""
	frappe.only_for("System Manager")
	created = []

	for model_id in frappe.parse_json(model_ids) or []:
		model_id = str(model_id).strip()
		if not model_id or frappe.db.exists("AI Model", {"provider": provider, "model_id": model_id}):
			continue

		label = model_id if not frappe.db.exists("AI Model", model_id) else f"{model_id} ({provider})"
		frappe.get_doc(
			{
				"doctype": "AI Model",
				"model_label": label,
				"provider": provider,
				"model_id": model_id,
				"enabled": 0,
			}
		).insert()
		created.append(label)

	return created
