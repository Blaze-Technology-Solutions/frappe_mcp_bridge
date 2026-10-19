# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document

from frappe_mcp_bridge.chat.context import PLACEHOLDER


class AIPromptTemplate(Document):
	def validate(self):
		# Templates are filled by plain substitution, so other Jinja would reach the user as
		# written. Say so now rather than surprise them later.
		leftover = PLACEHOLDER.sub("", self.prompt or "")
		if re.search(r"\{\{|\{%", leftover):
			frappe.throw(_("Templates support only {{ doc.fieldname }} placeholders."))
