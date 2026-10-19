// Copyright (c) 2026, Blaze Technology Solutions and contributors
// For license information, please see license.txt

// Model output is untrusted: a model can be steered by text inside ERP records or
// attachments. It is turned into HTML by frappe.markdown and then cleaned here against a
// whitelist, whatever the Frappe version does itself, since older releases do not sanitise.
const ALLOWED_TAGS = new Set([
	"a", "b", "blockquote", "br", "code", "del", "em", "h1", "h2", "h3", "h4", "h5", "h6",
	"hr", "i", "img", "li", "ol", "p", "pre", "s", "strong", "table", "tbody", "td", "th",
	"thead", "tr", "ul",
]);
// Dropped with everything inside them, rather than unwrapped.
const DROPPED_TAGS = new Set([
	"script", "style", "iframe", "object", "embed", "template", "svg", "math", "form",
	"input", "button", "textarea", "select", "link", "meta", "base", "noscript",
]);
const SAFE_HREF = /^(https?:|mailto:|tel:|#|\/(?!\/))/i;
// Images only from this site's files or inline: any other address, even on this site,
// would make the browser send a request the moment the message is shown.
const SAFE_IMG = /^(data:image\/(png|jpe?g|gif|webp);|\/(private\/)?files\/)/i;
const SAFE_ALIGN = /^text-align:\s*(left|right|center);?$/i;

export function render_markdown(text) {
	const doc = new DOMParser().parseFromString(frappe.markdown(text || ""), "text/html");
	clean(doc.body, doc);
	return doc.body.innerHTML;
}

function clean(node, doc) {
	for (const el of [...node.children]) {
		const tag = el.tagName.toLowerCase();

		if (DROPPED_TAGS.has(tag)) {
			el.remove();
			continue;
		}

		clean(el, doc);

		if (!ALLOWED_TAGS.has(tag)) {
			el.replaceWith(...el.childNodes);
			continue;
		}

		for (const attr of [...el.attributes]) {
			const name = attr.name.toLowerCase();
			const keep =
				name === "title" ||
				name === "alt" ||
				(tag === "a" && name === "href" && SAFE_HREF.test(attr.value)) ||
				(tag === "img" && name === "src" && SAFE_IMG.test(attr.value)) ||
				((tag === "code" || tag === "pre") && name === "class") ||
				((tag === "th" || tag === "td") && name === "style" && SAFE_ALIGN.test(attr.value));
			if (!keep) el.removeAttribute(attr.name);
		}

		if (tag === "img" && !el.getAttribute("src")) {
			el.replaceWith(placeholder(doc, el));
		} else if (tag === "a" && el.getAttribute("href") && !el.getAttribute("href").startsWith("#")) {
			el.setAttribute("target", "_blank");
			el.setAttribute("rel", "noopener noreferrer");
		}
	}
}

function placeholder(doc, img) {
	const label = doc.createElement("span");
	label.textContent = `[${img.getAttribute("alt") || __("image not shown")}]`;
	return label;
}

export function money(amount, currency, decimals) {
	const value = Number(amount || 0);
	const places = decimals ?? (Math.abs(value) < 0.01 && value !== 0 ? 4 : 2);
	return format_currency(value, currency || "USD", places);
}

export function tokens(count) {
	return format_number(count || 0, null, 0);
}

export function when(datetime) {
	return datetime ? frappe.datetime.prettyDate(datetime) : "";
}

// prettyDate only speaks of the past, so a deadline is shown as a clock time.
export function at(datetime) {
	return datetime ? frappe.datetime.str_to_user(datetime) : "";
}

export function summarise_args(args) {
	if (!args || typeof args !== "object") return "";
	const parts = [];
	for (const key of ["doctype", "name", "report_name", "query"]) {
		if (args[key]) parts.push(String(args[key]).slice(0, 60));
	}
	return parts.join(" · ");
}
