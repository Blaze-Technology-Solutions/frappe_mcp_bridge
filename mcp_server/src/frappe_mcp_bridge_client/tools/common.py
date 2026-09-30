# Copyright (c) 2026, Blaze Technology Solutions and contributors
# For license information, please see license.txt

"""Shared rendering for tool output."""

import json

DRY_RUN_BANNER = (
	"DRY RUN — the work ran and then the transaction was rolled back. "
	"Nothing below was saved. Pass dry_run=false to make it real.\n\n"
)


def render(envelope: dict) -> str:
	result = envelope.get("result")
	# A text result, such as a file's contents, goes out as it is rather than as a JSON string.
	body = (
		result if isinstance(result, str) else json.dumps(result, indent=2, default=str, ensure_ascii=False)
	)

	footer = [f"{envelope.get('duration', 0)}s"]
	if envelope.get("log"):
		footer.append(f"MCP Bridge Log {envelope['log']}")

	banner = DRY_RUN_BANNER if envelope.get("dry_run") else ""

	return f"{banner}{body}\n\n— {' · '.join(footer)}"


def render_plain(payload: dict) -> str:
	return json.dumps(payload, indent=2, default=str, ensure_ascii=False)
