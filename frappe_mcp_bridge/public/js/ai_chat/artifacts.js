// Copyright (c) 2026, Blaze Technology Solutions and contributors
// For license information, please see license.txt

// Drawing artifacts. Nothing here runs content from the model: charts, dashboards and
// tables are data drawn by this code, html is shown in a sandbox with scripts off and a
// policy that loads nothing, and svg is shown as an image, which never runs scripts.

export const ICONS = {
	chart: "📊",
	dashboard: "🧭",
	table: "🧮",
	document: "📄",
	html: "🖼️",
	svg: "✏️",
	code: "⌨️",
};

// No network at all: only inline styles and inline images.
const POLICY =
	"default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; " +
	"base-uri 'none'; form-action 'none'; frame-src 'none'";

const XLINK = "http://www.w3.org/1999/xlink";

// The sandbox can't stop a click on a link from loading another page, which would send the
// link's address. The server removes such links; this removes any it missed. A parsed
// document runs nothing and loads nothing.
function within_page(content) {
	const page = new DOMParser().parseFromString(content, "text/html");
	for (const element of page.querySelectorAll("[*|href]")) {
		const href = element.getAttribute("href") ?? element.getAttributeNS(XLINK, "href") ?? "";
		if (!href.trim().startsWith("#")) {
			element.removeAttribute("href");
			element.removeAttributeNS(XLINK, "href");
		}
	}
	return page.head.innerHTML + page.body.innerHTML;
}

export function html_document(content, title) {
	return (
		"<!doctype html><html><head><meta charset=\"utf-8\">" +
		`<meta http-equiv="Content-Security-Policy" content="${POLICY}">` +
		'<meta name="referrer" content="no-referrer">' +
		`<title>${frappe.utils.escape_html(title || "")}</title>` +
		"<style>body{font-family:system-ui,-apple-system,sans-serif;margin:16px;color:#1f2937;background:#fff}</style>" +
		`</head><body>${within_page(content)}</body></html>`
	);
}

export function svg_image(svg) {
	// btoa needs bytes, not UTF-16.
	return "data:image/svg+xml;base64," + btoa(unescape(encodeURIComponent(svg)));
}

// frappe.Chart writes labels and series names into its hover tooltip as HTML, and colours
// into a style attribute. The server checks both, and this checks them again.
const plain = (text) => String(text ?? "").replace(/</g, "‹").replace(/>/g, "›");
const COLOR = /^#[0-9a-fA-F]{6}$/;

export function draw_chart(element, spec, height = 260) {
	const datasets = spec.datasets.map((dataset) => ({
		name: plain(dataset.name),
		values: dataset.values.map((value) => Number(value) || 0),
		...(dataset.chartType ? { chartType: dataset.chartType === "line" ? "line" : "bar" } : {}),
	}));
	let type = spec.type;
	const kinds = new Set(datasets.map((dataset) => dataset.chartType).filter(Boolean));
	if ((type === "bar" || type === "line") && kinds.size > 1) type = "axis-mixed";
	if (type === "axis-mixed") datasets.forEach((dataset) => (dataset.chartType = dataset.chartType || "bar"));
	const colors = (spec.colors || []).filter((color) => COLOR.test(color));

	return new frappe.Chart(element, {
		title: plain(spec.title),
		type,
		height,
		colors: colors.length ? colors : undefined,
		data: { labels: spec.labels.map(plain), datasets },
		truncateLegends: true,
	});
}

// The few drawing styles a chart takes from the page's stylesheet, which a picture of it
// would otherwise lose.
const DRAWING_STYLES = [
	"fill",
	"fill-opacity",
	"stroke",
	"stroke-width",
	"stroke-opacity",
	"stroke-dasharray",
	"opacity",
	"font-family",
	"font-size",
	"font-weight",
	"text-anchor",
	"dominant-baseline",
];

function inline_styles(source, target) {
	const computed = getComputedStyle(source);
	target.setAttribute(
		"style",
		DRAWING_STYLES.map((property) => `${property}:${computed.getPropertyValue(property)}`).join(";")
	);
	for (let index = 0; index < source.children.length; index++) {
		inline_styles(source.children[index], target.children[index]);
	}
}

// The colour behind an element, so a picture of it reads the same in a dark theme.
function backdrop(element) {
	for (let node = element; node && node !== document.documentElement; node = node.parentElement) {
		const color = getComputedStyle(node).backgroundColor;
		if (color && color !== "transparent" && !/rgba\(.*,\s*0\)$/.test(color)) return color;
	}
	return "#fff";
}

// A drawn chart as a PNG at twice its size on screen, on the background it is shown on.
export function chart_png(element, title) {
	const svg = element.querySelector("svg");
	if (!svg) return Promise.reject(new Error("no chart"));
	const box = svg.getBoundingClientRect();
	const width = Number(svg.getAttribute("width")) || box.width;
	const height = Number(svg.getAttribute("height")) || box.height;

	const clone = svg.cloneNode(true);
	inline_styles(svg, clone);
	clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
	clone.setAttribute("width", width);
	clone.setAttribute("height", height);

	return new Promise((resolve, reject) => {
		const image = new Image();
		image.onerror = reject;
		image.onload = () => {
			const canvas = document.createElement("canvas");
			canvas.width = width * 2;
			canvas.height = height * 2;
			const context = canvas.getContext("2d");
			context.fillStyle = backdrop(element);
			context.fillRect(0, 0, canvas.width, canvas.height);
			context.drawImage(image, 0, 0, canvas.width, canvas.height);
			canvas.toBlob((blob) => {
				if (!blob) return reject(new Error("no image"));
				save_blob(blob, `${file_name(title)}.png`);
				resolve();
			}, "image/png");
		};
		image.src = svg_image(new XMLSerializer().serializeToString(clone));
	});
}

export function table_csv(table) {
	const cell = (value) => {
		const text = value === null || value === undefined ? "" : String(value);
		return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
	};
	const lines = [table.columns.map((column) => cell(column.label))];
	for (const row of table.rows) lines.push(row.map(cell));
	return lines.map((line) => line.join(",")).join("\n");
}

export function chart_csv(spec) {
	return table_csv({
		columns: [{ label: "Label" }, ...spec.datasets.map((dataset) => ({ label: dataset.name || "Value" }))],
		rows: spec.labels.map((label, index) => [label, ...spec.datasets.map((dataset) => dataset.values[index])]),
	});
}

const EXTENSIONS = {
	python: "py", javascript: "js", typescript: "ts", sql: "sql", json: "json", html: "html", css: "css",
	bash: "sh", shell: "sh", sh: "sh", java: "java", go: "go", ruby: "rb", php: "php", "c#": "cs",
	csharp: "cs", cpp: "cpp", c: "c", rust: "rs", yaml: "yml", xml: "xml", markdown: "md",
};

// Prints a copy of what the panel shows, alone on the page. The copy is this page's own
// drawing (charts, tables, cleaned Markdown), so nothing from the model runs here.
export function print_copy(element, title) {
	const sheet = document.createElement("div");
	sheet.className = "ai-chat-print";
	const heading = document.createElement("h2");
	heading.textContent = title || "";
	sheet.append(heading, element.cloneNode(true));
	document.body.appendChild(sheet);
	document.body.classList.add("ai-chat-printing");

	const done = () => {
		window.removeEventListener("afterprint", done);
		sheet.remove();
		document.body.classList.remove("ai-chat-printing");
	};
	window.addEventListener("afterprint", done);
	window.print();
}

function file_name(title) {
	return (title || "artifact").replace(/[^\w\- ]+/g, "").trim().replace(/\s+/g, "-") || "artifact";
}

// What a download of each type is: [file name, media type, text].
export function download_parts(artifact) {
	const name = file_name(artifact.title);
	const content = artifact.content || "";
	switch (artifact.artifact_type) {
		case "chart":
			return [`${name}.csv`, "text/csv", chart_csv(JSON.parse(content))];
		case "table":
			return [`${name}.csv`, "text/csv", table_csv(JSON.parse(content))];
		case "dashboard":
			return [`${name}.json`, "application/json", JSON.stringify(JSON.parse(content), null, 2)];
		case "document":
			return [`${name}.md`, "text/markdown", content];
		case "html":
			return [`${name}.html`, "text/html", html_document(content, artifact.title)];
		case "svg":
			return [`${name}.svg`, "image/svg+xml", content];
		default:
			return [`${name}.${EXTENSIONS[artifact.language] || "txt"}`, "text/plain", content];
	}
}

export function download(artifact) {
	const [filename, type, text] = download_parts(artifact);
	save_blob(new Blob([text], { type }), filename);
}

function save_blob(blob, filename) {
	const url = URL.createObjectURL(blob);
	const link = document.createElement("a");
	link.href = url;
	link.download = filename;
	document.body.appendChild(link);
	link.click();
	link.remove();
	setTimeout(() => URL.revokeObjectURL(url), 1000);
}
