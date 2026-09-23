# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Tool registry.

A handler declares the capability it needs and whether it writes. The dispatcher reads
only this, so a new tool is one decorator and never an edit to the gate.
"""

import importlib
import inspect
import types
import typing
from collections.abc import Callable
from dataclasses import dataclass, field

# Python annotation -> JSON Schema type, for the native HTTP endpoint's tools/list.
JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean", dict: "object", list: "array"}

DRY_RUN_SCHEMA = {
	"type": "boolean",
	"description": "true does the work and rolls it back; false writes for real. "
	"Leave it out to use the site's Default To Dry Run setting.",
}

HANDLER_MODULES = (
	"frappe_mcp_bridge.mcp.handlers.discovery",
	"frappe_mcp_bridge.mcp.handlers.documents",
	"frappe_mcp_bridge.mcp.handlers.data",
	"frappe_mcp_bridge.mcp.handlers.maintenance",
	"frappe_mcp_bridge.mcp.handlers.query",
	"frappe_mcp_bridge.mcp.handlers.script",
	"frappe_mcp_bridge.mcp.handlers.logs",
)


@dataclass(frozen=True)
class Tool:
	name: str
	capability: str
	handler: Callable
	summary: str = ""
	writes: bool = False
	# Which parameter carries the doctype being touched, so the gate can check the
	# allowed/blocked lists without knowing what the handler does.
	doctype_param: str | None = None
	params: tuple[str, ...] = field(default_factory=tuple)


_TOOLS: dict[str, Tool] = {}
_loaded = False


def tool(
	name: str,
	capability: str,
	*,
	summary: str = "",
	writes: bool = False,
	doctype_param: str | None = None,
):
	def decorator(fn: Callable) -> Callable:
		_TOOLS[name] = Tool(
			name=name,
			capability=capability,
			handler=fn,
			summary=summary or (fn.__doc__ or "").strip().splitlines()[0],
			writes=writes,
			doctype_param=doctype_param,
			params=tuple(fn.__code__.co_varnames[: fn.__code__.co_argcount]),
		)
		return fn

	return decorator


def load() -> None:
	global _loaded

	if _loaded:
		return

	for module in HANDLER_MODULES:
		importlib.import_module(module)

	_loaded = True


def get(name: str) -> Tool | None:
	load()
	return _TOOLS.get(name)


def all_tools() -> dict[str, Tool]:
	load()
	return dict(_TOOLS)


def input_schema(tool: Tool) -> dict:
	"""JSON Schema for a tool's arguments, read from the handler's signature and hints."""
	hints = typing.get_type_hints(tool.handler)
	properties, required = {}, []

	for name, parameter in inspect.signature(tool.handler).parameters.items():
		if name == "dry_run":
			properties[name] = dict(DRY_RUN_SCHEMA)
			continue

		properties[name] = _schema_for(hints.get(name))
		if parameter.default is not inspect.Parameter.empty:
			if parameter.default is not None:
				properties[name]["default"] = parameter.default
		else:
			required.append(name)

	schema = {"type": "object", "properties": properties}
	if required:
		schema["required"] = required

	return schema


def _schema_for(annotation) -> dict:
	"""`str | None` is a string; `list | dict` and bare params accept anything."""
	if annotation is None:
		return {}

	if isinstance(annotation, types.UnionType) or typing.get_origin(annotation) is typing.Union:
		members = [arg for arg in typing.get_args(annotation) if arg is not type(None)]
		return _schema_for(members[0]) if len(members) == 1 else {}

	origin = typing.get_origin(annotation) or annotation
	json_type = JSON_TYPES.get(origin)
	if not json_type:
		return {}

	schema = {"type": json_type}
	if origin is list and typing.get_args(annotation):
		schema["items"] = _schema_for(typing.get_args(annotation)[0])

	return schema
