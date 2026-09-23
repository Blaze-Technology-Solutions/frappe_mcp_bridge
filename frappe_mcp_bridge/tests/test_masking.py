# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

from frappe_mcp_bridge.api import mcp as api
from frappe_mcp_bridge.mcp import masking
from frappe_mcp_bridge.mcp.masking import MASK
from frappe_mcp_bridge.tests.utils import IntegrationTestCase, make_todo, mcp_settings

SECRET = "the secret description"


class TestMaskedValues(IntegrationTestCase):
	def setUp(self):
		self.todo = make_todo(SECRET)

	def test_get_document(self):
		with mcp_settings(masked=[("ToDo", "description")]):
			result = api.execute(tool="get_document", params={"doctype": "ToDo", "name": self.todo})

		self.assertTrue(result["ok"], result)
		self.assertEqual(result["result"]["description"], MASK)
		self.assertEqual(result["result"]["name"], self.todo)

	def test_list_rows_and_global_rule(self):
		with mcp_settings(masked=["description"]):
			result = api.execute(
				tool="list_documents",
				params={"doctype": "ToDo", "filters": {"name": self.todo}, "fields": ["name", "description"]},
			)

		self.assertEqual(result["result"]["rows"][0]["description"], MASK)

	def test_rule_for_another_doctype_does_not_apply(self):
		with mcp_settings(masked=[("Note", "description")]):
			result = api.execute(tool="get_document", params={"doctype": "ToDo", "name": self.todo})

		self.assertEqual(result["result"]["description"], SECRET)

	def test_child_rows_follow_their_own_doctype(self):
		with mcp_settings(masked=[("DocField", "label")]):
			result = api.execute(tool="get_document", params={"doctype": "DocType", "name": "ToDo"})

		self.assertTrue(result["ok"], result)
		labelled = [row for row in result["result"]["fields"] if row.get("label")]
		self.assertTrue(labelled)
		self.assertTrue(all(row["label"] == MASK for row in labelled))
		# The parent is a DocType, so its own fields are untouched.
		self.assertEqual(result["result"]["name"], "ToDo")

	def test_change_report_is_masked(self):
		with mcp_settings(masked=[("ToDo", "description")], read_only_mode=0, allow_write=1):
			result = api.execute(
				tool="update_document",
				params={"doctype": "ToDo", "name": self.todo, "values": {"description": "new"}},
				dry_run=True,
			)

		self.assertTrue(result["ok"], result)
		self.assertEqual(result["result"]["changed"]["description"], MASK)

	def test_csv_export(self):
		with mcp_settings(masked=[("ToDo", "description")]):
			result = api.execute(
				tool="export_records",
				params={"doctype": "ToDo", "fields": ["name", "description"], "filters": {"name": self.todo}},
			)

		self.assertTrue(result["ok"], result)
		self.assertNotIn(SECRET, result["result"]["csv"])
		self.assertIn(MASK, result["result"]["csv"])


class TestMaskRefusals(IntegrationTestCase):
	def refused(self, tool, params, **settings):
		with mcp_settings(masked=[("ToDo", "description")], **settings):
			result = api.execute(tool=tool, params=params)
		return not result["ok"] and result["error"]["type"] == "Blocked"

	def test_filtering_on_masked_field(self):
		self.assertTrue(
			self.refused("list_documents", {"doctype": "ToDo", "filters": {"description": ["like", "%a%"]}})
		)
		self.assertTrue(
			self.refused("count_documents", {"doctype": "ToDo", "filters": [["description", "like", "%a%"]]})
		)

	def test_sorting_on_masked_field(self):
		self.assertTrue(self.refused("list_documents", {"doctype": "ToDo", "order_by": "description desc"}))

	def test_expression_around_masked_field(self):
		self.assertTrue(
			self.refused("list_documents", {"doctype": "ToDo", "fields": ["name", "description as d"]})
		)

	def test_plain_field_and_filter_value_are_fine(self):
		self.assertFalse(
			self.refused(
				"list_documents",
				{"doctype": "ToDo", "fields": ["name", "description"], "filters": {"status": "description"}},
			)
		)

	def test_sql_naming_masked_field(self):
		self.assertTrue(
			self.refused(
				"run_sql", {"query": "select description from `tabToDo`"}, allow_sql_query=1, read_only_mode=0
			)
		)


class TestReportRows(IntegrationTestCase):
	def test_list_rows_masked_by_column(self):
		result = {
			"columns": [{"fieldname": "name"}, {"fieldname": "salary"}, "Bank Account:Data:120"],
			"rows": [["EMP-1", 5000, "123"], ["EMP-2", None, "456"]],
		}

		masked = masking._mask_report_rows(result, {"salary", "bank_account"})

		self.assertEqual(masked["rows"], [["EMP-1", MASK, MASK], ["EMP-2", None, MASK]])
