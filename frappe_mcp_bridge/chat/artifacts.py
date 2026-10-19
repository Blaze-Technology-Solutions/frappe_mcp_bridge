# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Artifacts: charts, dashboards, tables, documents, pages, diagrams and code that the
model makes and the chat shows beside the conversation.

None of them runs code. Charts, dashboards and tables arrive as data and are drawn by the
page's own code; documents are Markdown; html and svg are cleaned here, and the page shows
them again in a sandbox that runs no scripts and loads nothing. A model can be steered by
text inside ERP records, so an artifact must never be able to send what it shows anywhere.
"""

import json
import re
import xml.etree.ElementTree as ET
from html import escape
from html.parser import HTMLParser

import frappe
from frappe import _

from frappe_mcp_bridge.chat.providers.base import ToolDefinition

TOOL_NAME = "create_artifact"
TYPES = ("chart", "dashboard", "table", "document", "html", "svg", "code")
CHART_TYPES = ("bar", "line", "pie", "donut", "percentage", "axis-mixed")

MAX_LABELS = 500
MAX_DATASETS = 10
MAX_ROWS = 2000
MAX_COLUMNS = 50
MAX_TILES = 12
MAX_DASHBOARD_CHARTS = 6
MAX_DASHBOARD_TABLES = 4

TOOL = ToolDefinition(
	name=TOOL_NAME,
	description=(
		"Show a chart, dashboard, table, document, static HTML page, SVG diagram or code file "
		"beside the chat, where the user can view, download and keep it. Calling it again with "
		"the key of an earlier artifact saves a new version of that artifact."
	),
	input_schema={
		"type": "object",
		"properties": {
			"title": {"type": "string", "description": "Short title shown on the artifact."},
			"type": {"type": "string", "enum": list(TYPES)},
			"content": {
				"type": "string",
				"description": (
					"chart, dashboard and table: a JSON object written as text. document: Markdown. "
					"html: one static page with inline CSS. svg: one <svg> element. code: the source."
				),
			},
			"key": {
				"type": "string",
				"description": "Optional. The key of an earlier artifact, to save a new version of it.",
			},
			"language": {"type": "string", "description": "For code: the language, such as python."},
		},
		"required": ["title", "type", "content"],
	},
)

GUIDE = """\
You can show work beside the chat with create_artifact. Use it for things the user will look \
at, download or keep: charts, dashboards, tables of more than about fifteen rows, documents, \
letters, printable pages, diagrams and code files. Keep short answers in the chat itself. \
After making an artifact, say in a sentence or two what it shows instead of repeating it. \
To change one, call create_artifact again with its key.

Artifacts never run scripts. The content for each type:
- chart: {"type": "bar" | "line" | "pie" | "donut" | "percentage" | "axis-mixed", "title": "...", \
"labels": ["Jan", "Feb"], "datasets": [{"name": "Sales", "values": [120, 95], "chartType": "bar" | "line"}]}
- table: {"title": "...", "columns": ["Item", {"label": "Qty", "type": "number"}], "rows": [["Widget", 4]]}
- dashboard: {"title": "...", "tiles": [{"label": "Open orders", "value": "42", "note": "this week"}], \
"charts": [chart, ...], "tables": [table, ...]}
- document: Markdown.
- html: one static page with inline CSS. Scripts, forms, frames, outside images and links to \
other pages are removed.
- svg: one <svg> element. Scripts and outside references are removed.
Put company figures in an artifact only when you read them with tools in this chat, and never \
invent numbers."""


class ArtifactError(frappe.ValidationError):
	"""What was wrong with the content, worded for the model to fix it."""


# Creating ------------------------------------------------------------------------------


def create(conversation, message_name: str, call: dict, settings) -> dict:
	"""Run one create_artifact call. Returns the tool outcome the reply loop stores."""
	args = call.get("args") if isinstance(call.get("args"), dict) else {}
	try:
		artifact = save(conversation, message_name, args, settings)
	except ArtifactError as error:
		return {
			"text": _("The artifact was not saved: {0}").format(error),
			"is_error": True,
			"log": None,
			"action": None,
		}

	return {
		"text": _(
			'Saved artifact {0} version {1}, "{2}" ({3}, {4} bytes). The user sees it beside the chat.'
		).format(
			artifact.artifact_key, artifact.version, artifact.title, artifact.artifact_type, artifact.size
		),
		"is_error": False,
		"log": None,
		"action": None,
		"artifact": artifact.name,
	}


def save(conversation, message_name: str | None, args: dict, settings):
	title = " ".join(str(args.get("title") or "").split())[:140]
	kind = str(args.get("type") or "").strip().lower()
	content = args.get("content")
	language = re.sub(r"[^a-z0-9+#.-]", "", str(args.get("language") or "").lower())[:30]

	if not title:
		raise ArtifactError(_("give it a title."))
	if kind not in TYPES:
		raise ArtifactError(_("type must be one of {0}.").format(", ".join(TYPES)))
	if content is None or (isinstance(content, str) and not content.strip()):
		raise ArtifactError(_("content is empty."))

	clean = clean_content(kind, content)

	limit = int(settings.max_artifact_kb or 200) * 1024
	size = len(clean.encode())
	if size > limit:
		raise ArtifactError(
			_("it is {0} KB, over the {1} KB limit. Make it smaller.").format(size // 1024, limit // 1024)
		)

	key = slug(args.get("key") or title)
	version = (
		frappe.db.sql(
			"select coalesce(max(version), 0) from `tabAI Chat Artifact` where conversation = %s and artifact_key = %s",
			(conversation.name, key),
		)[0][0]
		+ 1
	)

	artifact = frappe.get_doc(
		{
			"doctype": "AI Chat Artifact",
			"conversation": conversation.name,
			"user": conversation.user,
			"message": message_name,
			"title": title,
			"artifact_type": kind,
			"artifact_key": key,
			"version": version,
			"language": language if kind == "code" else None,
			"size": size,
			"content": clean,
		}
	)
	artifact.insert(ignore_permissions=True)
	return artifact


def slug(text: str) -> str:
	return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", str(text).lower())).strip("-")[:60] or "artifact"


def clean_content(kind: str, content) -> str:
	"""The stored form of the content: checked JSON, cleaned markup, or plain text."""
	if kind in ("chart", "table", "dashboard"):
		data = _json(content)
		checked = {"chart": check_chart, "table": check_table, "dashboard": check_dashboard}[kind](data)
		return json.dumps(checked, ensure_ascii=False, separators=(",", ":"))

	text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
	if kind == "html":
		return clean_html(text)
	if kind == "svg":
		return clean_svg(text)
	return text


# Data types ------------------------------------------------------------------------------


def _json(content) -> dict:
	if isinstance(content, dict):
		return content
	try:
		data = json.loads(content)
	except (TypeError, ValueError):
		raise ArtifactError(_("content must be a JSON object written as text."))
	if not isinstance(data, dict):
		raise ArtifactError(_("content must be a JSON object, not a list or a single value."))
	return data


def _text(value, limit=140) -> str:
	return " ".join(str(value if value is not None else "").split())[:limit]


def _number(value):
	if isinstance(value, bool) or not isinstance(value, int | float):
		try:
			value = float(str(value).replace(",", ""))
		except ValueError:
			raise ArtifactError(_("chart values must be numbers; got {0}.").format(_text(value, 40)))
	return value


def check_chart(spec: dict) -> dict:
	if not isinstance(spec, dict):
		raise ArtifactError(_("each chart must be a JSON object."))

	kind = str(spec.get("type") or "bar").lower()
	if kind not in CHART_TYPES:
		raise ArtifactError(_("chart type must be one of {0}.").format(", ".join(CHART_TYPES)))

	labels = spec.get("labels")
	if not isinstance(labels, list) or not labels or len(labels) > MAX_LABELS:
		raise ArtifactError(_("a chart needs between 1 and {0} labels.").format(MAX_LABELS))

	datasets = spec.get("datasets")
	if not isinstance(datasets, list) or not datasets or len(datasets) > MAX_DATASETS:
		raise ArtifactError(_("a chart needs between 1 and {0} datasets.").format(MAX_DATASETS))

	checked = []
	for dataset in datasets:
		values = dataset.get("values") if isinstance(dataset, dict) else None
		if not isinstance(values, list) or len(values) != len(labels):
			raise ArtifactError(_("every dataset needs one value per label ({0}).").format(len(labels)))
		entry = {"name": _text(dataset.get("name") or ""), "values": [_number(v) for v in values]}
		if dataset.get("chartType") in ("bar", "line"):
			entry["chartType"] = dataset["chartType"]
		checked.append(entry)

	chart = {
		"type": kind,
		"title": _text(spec.get("title")),
		"labels": [_text(label, 60) for label in labels],
		"datasets": checked,
	}

	colors = [
		c for c in spec.get("colors") or [] if isinstance(c, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", c)
	]
	if colors:
		chart["colors"] = colors[:MAX_DATASETS]
	return chart


def check_table(spec: dict) -> dict:
	if not isinstance(spec, dict):
		raise ArtifactError(_("each table must be a JSON object."))

	columns = spec.get("columns")
	if not isinstance(columns, list) or not columns or len(columns) > MAX_COLUMNS:
		raise ArtifactError(_("a table needs between 1 and {0} columns.").format(MAX_COLUMNS))

	checked_columns = []
	for column in columns:
		if isinstance(column, dict):
			kind = column.get("type") if column.get("type") in ("text", "number") else "text"
			checked_columns.append({"label": _text(column.get("label"), 80), "type": kind})
		else:
			checked_columns.append({"label": _text(column, 80), "type": "text"})

	rows = spec.get("rows")
	if not isinstance(rows, list) or len(rows) > MAX_ROWS:
		raise ArtifactError(_("a table holds at most {0} rows.").format(MAX_ROWS))

	checked_rows = []
	for row in rows:
		if not isinstance(row, list) or len(row) != len(checked_columns):
			raise ArtifactError(_("every row needs one cell per column ({0}).").format(len(checked_columns)))
		checked_rows.append(
			[
				cell if isinstance(cell, int | float) and not isinstance(cell, bool) else _text(cell, 500)
				for cell in row
			]
		)

	return {"title": _text(spec.get("title")), "columns": checked_columns, "rows": checked_rows}


def check_dashboard(spec: dict) -> dict:
	tiles = spec.get("tiles") or []
	charts = spec.get("charts") or []
	tables = spec.get("tables") or []

	if not all(isinstance(part, list) for part in (tiles, charts, tables)):
		raise ArtifactError(_("tiles, charts and tables must be lists."))
	if not (tiles or charts or tables):
		raise ArtifactError(_("a dashboard needs at least one tile, chart or table."))
	if len(tiles) > MAX_TILES or len(charts) > MAX_DASHBOARD_CHARTS or len(tables) > MAX_DASHBOARD_TABLES:
		raise ArtifactError(
			_("a dashboard holds at most {0} tiles, {1} charts and {2} tables.").format(
				MAX_TILES, MAX_DASHBOARD_CHARTS, MAX_DASHBOARD_TABLES
			)
		)

	checked_tiles = []
	for tile in tiles:
		if not isinstance(tile, dict):
			raise ArtifactError(_("each tile must be a JSON object with a label and a value."))
		checked_tiles.append(
			{
				"label": _text(tile.get("label"), 60),
				"value": _text(tile.get("value"), 40),
				"note": _text(tile.get("note"), 80),
			}
		)

	return {
		"title": _text(spec.get("title")),
		"tiles": checked_tiles,
		"charts": [check_chart(chart) for chart in charts],
		"tables": [check_table(table) for table in tables],
	}


# Markup ----------------------------------------------------------------------------------

HTML_TAGS = {
	"html",
	"head",
	"body",
	"title",
	"style",
	"main",
	"header",
	"footer",
	"section",
	"article",
	"aside",
	"nav",
	"div",
	"span",
	"p",
	"br",
	"hr",
	"h1",
	"h2",
	"h3",
	"h4",
	"h5",
	"h6",
	"strong",
	"b",
	"em",
	"i",
	"u",
	"s",
	"small",
	"sub",
	"sup",
	"mark",
	"blockquote",
	"pre",
	"code",
	"ul",
	"ol",
	"li",
	"dl",
	"dt",
	"dd",
	"table",
	"thead",
	"tbody",
	"tfoot",
	"tr",
	"th",
	"td",
	"caption",
	"colgroup",
	"col",
	"img",
	"figure",
	"figcaption",
	"a",
	"abbr",
	"time",
	"address",
	"details",
	"summary",
	"label",
	"progress",
	"meter",
	"q",
	"cite",
	"kbd",
	"samp",
	"var",
	"del",
	"ins",
	"wbr",
}
# Dropped together with everything inside them.
HTML_DROPPED = {
	"script",
	"iframe",
	"object",
	"embed",
	"template",
	"noscript",
	"frame",
	"frameset",
	"applet",
	"svg",
	"math",
	"form",
	"select",
	"textarea",
	"button",
	"canvas",
	"video",
	"audio",
	"picture",
	"dialog",
	"portal",
	"noembed",
	"noframes",
	"xmp",
	"plaintext",
}
HTML_VOID = {
	"br",
	"hr",
	"img",
	"col",
	"wbr",
	"meta",
	"link",
	"base",
	"input",
	"source",
	"track",
	"area",
	"param",
	"embed",
}
HTML_ATTRIBUTES = {
	"class",
	"id",
	"style",
	"title",
	"lang",
	"dir",
	"colspan",
	"rowspan",
	"scope",
	"align",
	"valign",
	"width",
	"height",
	"alt",
	"datetime",
	"value",
	"max",
	"min",
	"low",
	"high",
	"optimum",
	"open",
	"start",
	"reversed",
	"type",
	"span",
	"abbr",
	"cite",
	"href",
	"src",
}
# Only links within the page: a click on any other would send its address out of the sandbox.
SAFE_HREF = re.compile(r"^#")
SAFE_IMAGE = re.compile(r"^data:image/(png|jpe?g|gif|webp);base64,", re.I)
UNSAFE_CSS = re.compile(r"expression\s*\(|javascript:|behaviou?r\s*:|-moz-binding", re.I)
CSS_IMPORT = re.compile(r"@import[^;]*;?", re.I)
# Anything CSS would fetch from elsewhere; inline images and #references stay.
CSS_OUTSIDE_URL = re.compile(r"url\(\s*(?!['\"]?(data:image/|#))[^)]*\)", re.I)


def clean_css(css: str) -> str:
	return UNSAFE_CSS.sub("", CSS_OUTSIDE_URL.sub("none", CSS_IMPORT.sub("", css)))


class _HTMLCleaner(HTMLParser):
	def __init__(self):
		super().__init__(convert_charrefs=True)
		self.out: list[str] = []
		self.dropping: list[str] = []
		self.raw_tag: str | None = None

	def handle_starttag(self, tag, attrs):
		if self.dropping:
			if tag == self.dropping[-1] and tag not in HTML_VOID:
				self.dropping.append(tag)
			return
		if tag in HTML_DROPPED:
			if tag not in HTML_VOID:
				self.dropping.append(tag)
			return
		if tag not in HTML_TAGS:
			return

		kept = []
		for name, value in attrs:
			value = value or ""
			if name not in HTML_ATTRIBUTES or name.startswith("on"):
				continue
			if name == "href" and (tag != "a" or not SAFE_HREF.match(value.strip())):
				continue
			if name == "src" and (tag != "img" or not SAFE_IMAGE.match(value.strip())):
				continue
			if name == "style":
				if UNSAFE_CSS.search(value):
					continue
				value = clean_css(value)
			kept.append(f' {name}="{escape(value, quote=True)}"')

		if tag == "img" and not any(part.startswith(" src=") for part in kept):
			return
		self.out.append(f"<{tag}{''.join(kept)}>")
		if tag in ("style", "title"):
			self.raw_tag = tag

	def handle_startendtag(self, tag, attrs):
		self.handle_starttag(tag, attrs)

	def handle_endtag(self, tag):
		if self.dropping:
			if tag == self.dropping[-1]:
				self.dropping.pop()
			return
		if tag in HTML_TAGS and tag not in HTML_VOID:
			self.out.append(f"</{tag}>")
		if tag == self.raw_tag:
			self.raw_tag = None

	def handle_data(self, data):
		if self.dropping:
			return
		if self.raw_tag == "style":
			# Raw CSS: nothing may close the element early, and nothing may load from elsewhere.
			self.out.append(clean_css(data.replace("</", "<\\/")))
		else:
			self.out.append(escape(data, quote=False))


def clean_html(text: str) -> str:
	cleaner = _HTMLCleaner()
	cleaner.feed(text)
	cleaner.close()
	html = "".join(cleaner.out).strip()
	if not html:
		raise ArtifactError(_("nothing was left of the page after removing what isn't allowed."))
	return html


SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
SVG_TAGS = {
	"svg",
	"g",
	"path",
	"rect",
	"circle",
	"ellipse",
	"line",
	"polyline",
	"polygon",
	"text",
	"tspan",
	"textPath",
	"defs",
	"linearGradient",
	"radialGradient",
	"stop",
	"title",
	"desc",
	"clipPath",
	"mask",
	"pattern",
	"marker",
	"symbol",
	"use",
	"style",
}


def clean_svg(text: str) -> str:
	if "<!ENTITY" in text or "<!DOCTYPE" in text:
		raise ArtifactError(_("an svg may not declare a DOCTYPE or entities."))

	ET.register_namespace("", SVG_NS)
	ET.register_namespace("xlink", XLINK_NS)
	try:
		root = ET.fromstring(text.strip())
	except ET.ParseError as error:
		raise ArtifactError(_("the svg is not valid XML: {0}").format(error))

	if _local(root.tag) != "svg":
		raise ArtifactError(_("an svg artifact must be a single <svg> element."))

	_clean_svg_element(root)
	return ET.tostring(root, encoding="unicode")


def _local(tag: str) -> str:
	return tag.rsplit("}", 1)[-1]


def _clean_svg_element(element) -> None:
	for name in list(element.attrib):
		local = _local(name).lower()
		value = element.attrib[name].strip()
		if (
			local.startswith("on")
			or (local == "href" and not value.startswith("#"))
			or "javascript:" in value.lower()
		):
			del element.attrib[name]
		elif local == "style":
			if UNSAFE_CSS.search(value):
				del element.attrib[name]
			else:
				element.attrib[name] = clean_css(value)

	for child in list(element):
		if _local(child.tag) not in SVG_TAGS:
			element.remove(child)
			continue
		if _local(child.tag) == "style" and child.text:
			child.text = clean_css(child.text)
		_clean_svg_element(child)


# Reading ---------------------------------------------------------------------------------


def for_ui(name: str, with_content: bool = False) -> dict | None:
	fields = [
		"name",
		"artifact_key",
		"version",
		"title",
		"artifact_type",
		"language",
		"size",
		"conversation",
		"user",
		"creation",
	]
	if with_content:
		fields.append("content")
	return frappe.db.get_value("AI Chat Artifact", name, fields, as_dict=True)
