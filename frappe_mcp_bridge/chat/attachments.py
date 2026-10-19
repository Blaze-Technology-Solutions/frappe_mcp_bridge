# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Files users attach to a message.

They are ordinary private Files attached to the conversation, so Frappe's own file
permissions keep them to the chat's owner (and to managers, who may read the chat).
Each turn sends them in whatever form the current model can read: images and PDFs
natively where supported, PDFs as extracted text otherwise, other files as text.
Secrets are taken out of the text that is sent; the stored file is left as uploaded.
Images and PDFs sent natively go as they are, so they can't be cleaned.
"""

import base64
import io
import json
import mimetypes
import re

import frappe
from frappe import _
from frappe.utils import escape_html

from frappe_mcp_bridge.chat.access import may_share_company_data
from frappe_mcp_bridge.chat.scrub import scrub_secrets

TEXT_EXTENSIONS = {"txt", "csv", "tsv", "md", "json", "xml", "log", "html", "yaml", "yml"}
IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
MAX_TEXT_CHARS = 60_000


def kind_of(file_name: str) -> str | None:
	extension = (file_name.rsplit(".", 1)[-1] if "." in file_name else "").lower()
	media_type = mimetypes.guess_type(file_name)[0] or ""

	if media_type in IMAGE_TYPES:
		return "image"
	if extension == "pdf":
		return "pdf"
	if extension in TEXT_EXTENSIONS:
		return "text"
	return None


def save_upload(conversation: str, file_name: str, content: bytes, settings) -> dict:
	"""Store an upload as a private File on the chat.

	Frappe's own upload checks write permission on the record a file is attached to, and
	chat users have none on their chats: everything about a chat is written by the
	server. The caller has checked that this chat is theirs, so the check is done here.
	"""
	if not settings.allow_attachments or (settings.erp_access or "Off") == "Off":
		frappe.throw(_("Attachments are switched off in AI Chat Settings."))

	# Frappe rewrites markup in a stored file name, which could change its extension after
	# this check, and messages show names as HTML; neither can happen without brackets.
	file_name = re.sub(r'[<>"/\\]', "_", (file_name or "").strip()) or "upload"
	if not kind_of(file_name):
		frappe.throw(
			_("{0} can't be read by the AI. Attach images, PDFs or text files such as CSV.").format(
				escape_html(file_name)
			)
		)

	limit_mb = int(settings.max_attachment_mb or 10)
	if len(content) > limit_mb * 1024 * 1024:
		frappe.throw(_("{0} is larger than {1} MB.").format(escape_html(file_name), limit_mb))

	file = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": file_name,
			"content": content,
			"is_private": 1,
			"attached_to_doctype": "AI Chat Conversation",
			"attached_to_name": conversation,
		}
	)
	file.flags.ignore_permissions = True
	file.insert()

	kind = kind_of(file_name)
	return {
		"name": file.name,
		"file_name": file.file_name,
		"file_url": file.file_url,
		"kind": kind,
		# Told now, so the user knows before sending what will be taken out of it.
		"removed": _secrets_in(content, kind),
	}


def _secrets_in(content: bytes, kind: str) -> list[str]:
	if kind == "text":
		text = content.decode("utf-8", errors="replace")
	elif kind == "pdf":
		text = _pdf_text(content)
	else:
		return []
	return scrub_secrets(text)[1]


def validate(conversation: str, names: list[str], settings, model) -> list[dict]:
	"""Check the files the user picked and return what the message stores about them."""
	if not names:
		return []

	if not settings.allow_attachments:
		frappe.throw(_("Attachments are switched off in AI Chat Settings."))

	if not may_share_company_data(settings, model):
		frappe.throw(
			_("{0} is not allowed to read company data, so it can't take attachments.").format(
				model.model_label
			)
		)

	limit = int(settings.max_attachments or 5)
	if len(names) > limit:
		frappe.throw(_("Attach at most {0} files to one message.").format(limit))

	max_bytes = int(settings.max_attachment_mb or 10) * 1024 * 1024
	stored = []

	for name in names:
		file = frappe.get_doc("File", name)
		if (
			file.attached_to_doctype != "AI Chat Conversation"
			or file.attached_to_name != conversation
			or file.owner != frappe.session.user
		):
			frappe.throw(_("{0} was not uploaded to this chat.").format(escape_html(file.file_name)))

		if int(file.file_size or 0) > max_bytes:
			frappe.throw(
				_("{0} is larger than {1} MB.").format(
					escape_html(file.file_name), settings.max_attachment_mb or 10
				)
			)

		kind = kind_of(file.file_name or "")
		if not kind:
			frappe.throw(
				_("{0} can't be read by the AI. Attach images, PDFs or text files such as CSV.").format(
					escape_html(file.file_name)
				)
			)

		if kind == "image" and not model.supports_vision:
			frappe.throw(_("{0} can't read images. Pick a model that can.").format(model.model_label))

		stored.append(
			{"name": file.name, "file_name": file.file_name, "file_url": file.file_url, "kind": kind}
		)

	return stored


def parts_for(attachments_json: str | None, model, cache: dict) -> tuple[list[dict], list[str]]:
	"""(content parts for the provider, notes about files this model can't take)."""
	if not attachments_json:
		return [], []

	parts, notes = [], []
	for entry in json.loads(attachments_json):
		kind = entry.get("kind")
		label = entry.get("file_name") or entry.get("name")

		if not model.may_read_erp_data:
			notes.append(f"[{label} is attached but this model may not read company data]")
			continue

		if kind == "image" and not model.supports_vision:
			notes.append(f"[{label} is an image this model can't read]")
			continue

		native_pdf = kind == "pdf" and bool(model.supports_pdf)
		key = (entry.get("name"), kind, native_pdf)
		if key not in cache:
			cache[key] = _load(entry, kind, native_pdf)

		if cache[key] is None:
			notes.append(f"[{label} could not be read]")
		else:
			parts.append(cache[key])

	return parts, notes


def _load(entry: dict, kind: str, native_pdf: bool) -> dict | None:
	try:
		content = frappe.get_doc("File", entry["name"]).get_content()
	except Exception:
		return None

	if isinstance(content, str):
		content = content.encode()

	name = entry.get("file_name") or entry["name"]

	if kind == "image":
		media_type = mimetypes.guess_type(name)[0] or "image/png"
		return {"type": "image", "name": name, "media_type": media_type, "data": _b64(content)}

	if kind == "pdf" and native_pdf:
		return {"type": "pdf", "name": name, "media_type": "application/pdf", "data": _b64(content)}

	text = _pdf_text(content) if kind == "pdf" else content.decode("utf-8", errors="replace")
	text = scrub_secrets(text)[0]
	if len(text) > MAX_TEXT_CHARS:
		text = text[:MAX_TEXT_CHARS] + "\n... (cut short)"

	return {"type": "text", "name": name, "data": text}


def _pdf_text(content: bytes) -> str:
	from pypdf import PdfReader

	try:
		reader = PdfReader(io.BytesIO(content))
		return "\n\n".join(page.extract_text() or "" for page in reader.pages)
	except Exception:
		return "(The PDF's text could not be extracted.)"


def _b64(content: bytes) -> str:
	return base64.standard_b64encode(content).decode("ascii")
