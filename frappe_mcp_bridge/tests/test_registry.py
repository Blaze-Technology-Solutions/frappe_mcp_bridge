# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

from frappe_mcp_bridge.mcp import registry
from frappe_mcp_bridge.mcp.descriptions import DESCRIPTIONS
from frappe_mcp_bridge.tests.utils import IntegrationTestCase


class TestRegistry(IntegrationTestCase):
	def test_every_tool_has_a_description(self):
		missing = sorted(set(registry.all_tools()) - set(DESCRIPTIONS))
		self.assertFalse(missing, f"Add these to mcp/descriptions.py: {missing}")

	def test_schema_covers_every_parameter(self):
		for name, tool in registry.all_tools().items():
			schema = registry.input_schema(tool)
			self.assertEqual(set(schema["properties"]), set(tool.params), name)

	def test_schema_types_and_required(self):
		tools = registry.all_tools()

		update = registry.input_schema(tools["update_document"])
		self.assertEqual(update["required"], ["doctype", "name", "values"])
		self.assertEqual(update["properties"]["values"]["type"], "object")
		self.assertEqual(update["properties"]["dry_run"]["type"], "boolean")

		listing = registry.input_schema(tools["list_documents"])
		self.assertEqual(listing["properties"]["limit"], {"type": "integer", "default": 20})
		# Untyped and multi-type parameters accept anything.
		self.assertEqual(listing["properties"]["filters"], {})
		self.assertEqual(registry.input_schema(tools["run_sql"])["properties"]["values"], {})

	def test_writing_tools_take_dry_run(self):
		for name, tool in registry.all_tools().items():
			if tool.writes:
				self.assertIn("dry_run", tool.params, name)
