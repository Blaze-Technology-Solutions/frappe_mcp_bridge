# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Keeps the fields listed under Sensitive Fields away from the AI model.

Two halves. `refusal` runs in the gate, before anything executes, and refuses calls that
would reveal a masked value indirectly: filtering or sorting on it, aggregating or
aliasing it, or naming it in SQL. `mask` runs on the result, and replaces masked values
wherever they appear, including child tables and before/after change reports.

run_server_script is not covered: a script can read anything its user can. Keep the
Server Script capability off on sites with masked fields.
"""

import re

import frappe
from frappe import _

MASK = "•••"

# Tools whose rows come without a doctype to scope the rules by, so every masked
# fieldname applies to every column.
UNSCOPED_TOOLS = {"run_sql", "run_report"}

# Parameters that can filter, sort, group or compute on a field.
FIELD_EXPRESSION_PARAMS = ("filters", "or_filters", "order_by", "group_by")


def refusal(settings, tool, params: dict) -> str | None:
	"""A reason to refuse this call, or None."""
	everywhere, by_doctype = settings.get_masked_fields()
	if not everywhere and not by_doctype:
		return None

	all_masked = everywhere.union(*by_doctype.values())

	if tool.name == "run_sql":
		named = _mentioned(params.get("query") or "", all_masked)
		if named:
			return _("The query mentions masked fields ({0}), so it is refused.").format(", ".join(named))
		return None

	doctype = params.get("doctype") if isinstance(params.get("doctype"), str) else None
	masked = everywhere | by_doctype.get(doctype, set())

	for param in FIELD_EXPRESSION_PARAMS:
		named = _mentioned(_flatten(params.get(param)), masked)
		if named:
			return _("{0} uses masked fields ({1}), which would reveal their values.").format(
				param, ", ".join(named)
			)

	# A plain fieldname in fields is fine: the value is masked on the way out. An
	# expression around it, like sum(salary) or salary as s, would escape the mask.
	for field in params.get("fields") or []:
		if isinstance(field, str) and field.strip() not in masked:
			named = _mentioned(field, all_masked)
			if named:
				return _("fields may name {0} only on its own, not inside an expression or alias.").format(
					", ".join(named)
				)

	return None


def mask(settings, tool, params: dict, result):
	"""Return result with every masked value replaced."""
	everywhere, by_doctype = settings.get_masked_fields()
	if not everywhere and not by_doctype:
		return result

	if tool.name in UNSCOPED_TOOLS:
		all_masked = everywhere.union(*by_doctype.values())
		result = _mask_report_rows(result, all_masked)
		return _walk(result, None, lambda _doctype: all_masked)

	doctype = params.get("doctype") if isinstance(params.get("doctype"), str) else None
	return _walk(result, doctype, lambda current: everywhere | by_doctype.get(current, set()))


def mask_rows(settings, doctype: str, rows: list) -> list:
	"""For handlers that serialise rows themselves, such as the CSV export."""
	everywhere, by_doctype = settings.get_masked_fields()
	masked = everywhere | by_doctype.get(doctype, set())
	if not masked:
		return rows

	return _walk(rows, doctype, lambda _doctype: masked)


def _walk(value, doctype, masked_for):
	if isinstance(value, dict):
		# Child rows and linked documents carry their own doctype; rules follow it.
		own = value.get("doctype")
		if isinstance(own, str) and own:
			doctype = own

		masked = masked_for(doctype)
		return {
			key: MASK if key in masked and item not in (None, "") else _walk(item, doctype, masked_for)
			for key, item in value.items()
		}

	if isinstance(value, list | tuple):
		return [_walk(item, doctype, masked_for) for item in value]

	return value


def _mask_report_rows(result, masked: set[str]):
	"""Report rows can be plain lists, positioned by the columns list rather than keyed."""
	if not isinstance(result, dict) or not isinstance(result.get("columns"), list):
		return result

	positions = {
		index for index, column in enumerate(result["columns"]) if _column_fieldname(column) in masked
	}
	if not positions:
		return result

	rows = [
		[MASK if index in positions and cell not in (None, "") else cell for index, cell in enumerate(row)]
		if isinstance(row, list | tuple)
		else row
		for row in result.get("rows") or []
	]
	return {**result, "rows": rows}


def _column_fieldname(column) -> str | None:
	if isinstance(column, dict):
		return column.get("fieldname")

	if isinstance(column, str):
		# Old-style "Label:Fieldtype/Options:Width" columns.
		return frappe.scrub(column.split(":", 1)[0])

	return None


def _mentioned(text: str, fieldnames: set[str]) -> list[str]:
	return sorted(
		fieldname for fieldname in fieldnames if re.search(rf"(?<![\w]){re.escape(fieldname)}(?![\w])", text)
	)


def _flatten(value) -> str:
	"""The field names a filter, order_by or group_by refers to, as one searchable string.

	Only field positions count, so a filter whose value happens to spell a masked
	fieldname is not refused.
	"""
	if value is None:
		return ""

	if isinstance(value, str):
		stripped = value.strip()
		if stripped[:1] in ("[", "{"):
			try:
				return _flatten(frappe.parse_json(stripped))
			except ValueError:
				pass
		# order_by and group_by: SQL-ish text, searched as it stands.
		return value

	if isinstance(value, dict):
		# {"fieldname": value} or {"fieldname": [operator, value]}
		return " ".join(str(key) for key in value)

	if isinstance(value, list | tuple):
		names = []
		for entry in value:
			if isinstance(entry, list | tuple) and entry:
				# [fieldname, operator, value] or [doctype, fieldname, operator, value]
				names.append(str(entry[1] if len(entry) >= 4 else entry[0]))
			elif isinstance(entry, dict):
				names.append(_flatten(entry))
			elif isinstance(entry, str):
				names.append(entry)
		return " ".join(names)

	return ""
