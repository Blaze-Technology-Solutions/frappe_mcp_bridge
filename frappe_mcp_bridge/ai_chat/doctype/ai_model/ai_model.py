# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

PRICE_FIELDS = ("input_price", "cached_input_price", "cache_write_price", "output_price")


class AIModel(Document):
	def validate(self):
		self.model_id = (self.model_id or "").strip()
		self.context_window = max(cint(self.context_window) or 128_000, 1_000)
		self.max_output_tokens = max(cint(self.max_output_tokens) or 8192, 256)

		for field in PRICE_FIELDS:
			if flt(self.get(field)) < 0:
				frappe.throw(_("{0} can't be negative.").format(_(self.meta.get_label(field))))

		# An unpriced model would bill every reply at zero and the usage report would lie.
		if self.enabled and not self.is_free and not (flt(self.input_price) and flt(self.output_price)):
			frappe.throw(
				_(
					"Enter the input and output prices before enabling {0}, or tick Free / Self-Hosted."
				).format(self.model_label)
			)

		if self.fallback_model == self.name:
			frappe.throw(_("A model can't be its own fallback."))

	def on_update(self):
		if not self.enabled and frappe.db.get_single_value("AI Chat Settings", "default_model") == self.name:
			frappe.msgprint(
				_("{0} is the default model in AI Chat Settings and is now disabled.").format(self.name)
			)
