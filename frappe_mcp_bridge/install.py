# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

import frappe


def after_install():
	set_default_settings()


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
