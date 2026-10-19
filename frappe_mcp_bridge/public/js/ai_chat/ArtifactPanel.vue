<template>
	<aside class="ai-chat-artifact" :aria-label="__('Artifact')">
		<header class="ai-chat-artifact__head">
			<div class="ai-chat-artifact__title">
				<span>{{ ICONS[artifact ? artifact.artifact_type : "document"] }}</span>
				<strong>{{ artifact ? artifact.title : __("Loading…") }}</strong>
			</div>
			<div class="ai-chat-artifact__actions">
				<select
					v-if="artifact && artifact.versions.length > 1"
					class="form-control input-xs"
					:value="artifact.name"
					:aria-label="__('Version')"
					@change="load($event.target.value)"
				>
					<option v-for="version in artifact.versions" :key="version.name" :value="version.name">
						{{ __("Version {0}", [version.version]) }}
					</option>
				</select>
				<button class="btn btn-default btn-xs" :disabled="!artifact" @click="tab = tab === 'preview' ? 'source' : 'preview'">
					{{ tab === "preview" ? __("Source") : __("Preview") }}
				</button>
				<button class="btn btn-default btn-xs" :disabled="!artifact" @click="download(artifact)">
					{{ kind === "chart" || kind === "table" ? __("CSV") : __("Download") }}
				</button>
				<button
					v-if="kind === 'chart' && data && tab === 'preview'"
					class="btn btn-default btn-xs"
					@click="png"
				>
					{{ __("PNG") }}
				</button>
				<div class="ai-chat-artifact__more">
					<button
						class="btn btn-default btn-xs"
						:disabled="!artifact"
						:title="__('More')"
						aria-haspopup="menu"
						:aria-expanded="menu ? 'true' : 'false'"
						@click.stop="menu = !menu"
					>
						⋯
					</button>
					<div v-if="menu" class="ai-chat-artifact__menu" role="menu">
						<button type="button" role="menuitem" @click="choose(copy)">{{ __("Copy source") }}</button>
						<!-- A page is shown in a sandbox this page can't print; its download prints from the browser. -->
						<button v-if="kind !== 'html'" type="button" role="menuitem" @click="choose(print)">
							{{ __("Print or save as PDF") }}
						</button>
						<button v-if="can_keep" type="button" role="menuitem" @click="choose(keep)">
							{{ __("Save as Note") }}
						</button>
					</div>
				</div>
				<button class="btn btn-default btn-xs" :title="__('Close')" @click="$emit('close')">×</button>
			</div>
		</header>

		<div v-if="error" class="ai-chat-artifact__body text-muted">{{ error }}</div>

		<div v-else-if="artifact" ref="body" class="ai-chat-artifact__body">
			<pre v-if="tab === 'source'" class="ai-chat-artifact__source">{{ source }}</pre>

			<ChartBox v-else-if="kind === 'chart' && data" ref="chart_box" :spec="data" :height="320" />

			<DataTable v-else-if="kind === 'table' && data" :table="data" />

			<div v-else-if="kind === 'dashboard' && data" class="ai-chat-dashboard">
				<h4 v-if="data.title">{{ data.title }}</h4>
				<div v-if="data.tiles.length" class="ai-chat-dashboard__tiles">
					<div v-for="(tile, index) in data.tiles" :key="index" class="ai-chat-dashboard__tile">
						<span class="ai-chat-dashboard__label">{{ tile.label }}</span>
						<span class="ai-chat-dashboard__value">{{ tile.value }}</span>
						<span v-if="tile.note" class="ai-chat-dashboard__note">{{ tile.note }}</span>
					</div>
				</div>
				<div v-for="(chart, index) in data.charts" :key="'c' + index" class="ai-chat-dashboard__card">
					<ChartBox :spec="chart" png-button />
				</div>
				<div v-for="(table, index) in data.tables" :key="'t' + index" class="ai-chat-dashboard__card">
					<DataTable :table="table" />
				</div>
			</div>

			<div v-else-if="kind === 'document'" class="ai-chat__markdown ai-chat-artifact__document" v-html="document_html"></div>

			<!-- Scripts off, no forms, no popups, no network: see html_document. -->
			<iframe
				v-else-if="kind === 'html'"
				class="ai-chat-artifact__frame"
				sandbox=""
				referrerpolicy="no-referrer"
				:title="artifact.title"
				:srcdoc="html_document(artifact.content, artifact.title)"
			></iframe>

			<div v-else-if="kind === 'svg'" class="ai-chat-artifact__svg">
				<img :src="svg_image(artifact.content)" :alt="artifact.title" />
			</div>

			<pre v-else class="ai-chat-artifact__source"><code>{{ artifact.content }}</code></pre>
		</div>

		<footer v-if="artifact" class="ai-chat-artifact__foot text-muted">
			{{ __("Version {0}", [artifact.version]) }} · {{ artifact.artifact_type }} · {{ when(artifact.creation) }}
		</footer>
	</aside>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { call } from "./api";
import { ICONS, download, html_document, print_copy, svg_image } from "./artifacts";
import { render_markdown, when } from "./format";
import ChartBox from "./ChartBox.vue";
import DataTable from "./DataTable.vue";

const props = defineProps({ name: { type: String, required: true } });
defineEmits(["close"]);

const artifact = ref(null);
const error = ref("");
const tab = ref("preview");
const chart_box = ref(null);
const body = ref(null);
const menu = ref(false);

const kind = computed(() => artifact.value && artifact.value.artifact_type);
// Only the person who made it may keep a copy; managers reading a chat may not.
const can_keep = computed(
	() =>
		artifact.value &&
		["document", "html"].includes(kind.value) &&
		artifact.value.user === frappe.session.user
);
const data = computed(() => {
	if (!artifact.value || !["chart", "table", "dashboard"].includes(kind.value)) return null;
	try {
		return JSON.parse(artifact.value.content);
	} catch (e) {
		return null;
	}
});
const source = computed(() =>
	data.value ? JSON.stringify(data.value, null, 2) : (artifact.value && artifact.value.content) || ""
);
const document_html = computed(() => render_markdown(artifact.value ? artifact.value.content : ""));

async function load(name) {
	error.value = "";
	try {
		artifact.value = await call("get_artifact", { name });
	} catch (e) {
		artifact.value = null;
		error.value = __("This artifact could not be opened.");
	}
}

function copy() {
	frappe.utils.copy_to_clipboard(source.value);
}

function png() {
	chart_box.value.png().catch(() => frappe.show_alert({ message: __("The picture could not be made."), indicator: "red" }));
}


function print() {
	print_copy(body.value, artifact.value.title);
}

function keep() {
	const { name, title } = artifact.value;
	// frappe.confirm shows HTML, and the title is the model's.
	frappe.confirm(__("Keep a copy of {0} as a private Note?", [frappe.utils.escape_html(title)]), async () => {
		try {
			const note = await call("save_artifact_as_note", { name });
			frappe.show_alert(
				{ message: __("Saved as Note {0}", [frappe.utils.get_form_link("Note", note.name, true)]), indicator: "green" },
				8
			);
		} catch (e) {
			// frappe.call has already shown the server's reason.
		}
	});
}

function choose(action) {
	menu.value = false;
	action();
}

function close_menu() {
	menu.value = false;
}

onMounted(() => document.addEventListener("click", close_menu));
onBeforeUnmount(() => document.removeEventListener("click", close_menu));

watch(
	() => props.name,
	(name) => {
		tab.value = "preview";
		menu.value = false;
		load(name);
	},
	{ immediate: true }
);
</script>

<style>
.ai-chat-artifact {
	display: flex;
	flex-direction: column;
	min-width: 0;
	background: var(--card-bg, #fff);
	border-left: 1px solid var(--border-color, #e5e7eb);
}

.ai-chat-artifact__head {
	display: flex;
	align-items: center;
	justify-content: space-between;
	gap: 8px;
	padding: 10px 12px;
	border-bottom: 1px solid var(--border-color, #e5e7eb);
}

.ai-chat-artifact__title {
	display: flex;
	align-items: center;
	gap: 6px;
	min-width: 0;
}

.ai-chat-artifact__title strong {
	white-space: nowrap;
	overflow: hidden;
	text-overflow: ellipsis;
}

.ai-chat-artifact__actions {
	display: flex;
	align-items: center;
	gap: 6px;
	flex-shrink: 0;
}

.ai-chat-artifact__actions select {
	width: auto;
}

.ai-chat-artifact__more {
	position: relative;
}

.ai-chat-artifact__menu {
	position: absolute;
	right: 0;
	top: calc(100% + 4px);
	z-index: 10;
	min-width: 150px;
	display: flex;
	flex-direction: column;
	padding: 4px;
	background: var(--card-bg, #fff);
	border: 1px solid var(--border-color, #e5e7eb);
	border-radius: 6px;
	box-shadow: var(--shadow-md, 0 4px 12px rgba(0, 0, 0, 0.12));
}

.ai-chat-artifact__menu button {
	padding: 6px 10px;
	text-align: left;
	font-size: var(--text-sm, 12px);
	color: inherit;
	background: none;
	border: none;
	border-radius: 4px;
}

.ai-chat-artifact__menu button:hover {
	background: var(--subtle-fg, #f3f4f6);
}

/* Printing an artifact shows only its copy; see print_copy. */
.ai-chat-print {
	display: none;
}

@media print {
	body.ai-chat-printing > *:not(.ai-chat-print) {
		display: none !important;
	}

	body.ai-chat-printing .ai-chat-print {
		display: block;
		padding: 0 8px;
	}

	.ai-chat-print .ai-chat-table__scroll {
		max-height: none;
		overflow: visible;
	}

	.ai-chat-print .ai-chat-artifact__source {
		max-height: none;
		overflow: visible;
	}

	.ai-chat-print .ai-chat-chart__bar {
		display: none;
	}
}

.ai-chat-artifact__body {
	flex: 1;
	overflow: auto;
	padding: 14px;
}

.ai-chat-artifact__frame {
	width: 100%;
	height: 100%;
	min-height: 480px;
	border: 1px solid var(--border-color, #e5e7eb);
	border-radius: 6px;
	background: #fff;
}

.ai-chat-artifact__svg {
	display: flex;
	justify-content: center;
	padding: 12px;
	background: #fff;
	border-radius: 6px;
}

.ai-chat-artifact__svg img {
	max-width: 100%;
	height: auto;
}

.ai-chat-artifact__source {
	margin: 0;
	white-space: pre-wrap;
	overflow-wrap: anywhere;
	font-size: var(--text-xs, 11px);
}

.ai-chat-artifact__document {
	max-width: 760px;
}

.ai-chat-artifact__foot {
	padding: 6px 12px;
	font-size: var(--text-xs, 11px);
	border-top: 1px solid var(--border-color, #e5e7eb);
}

.ai-chat-dashboard {
	display: flex;
	flex-direction: column;
	gap: 14px;
}

.ai-chat-dashboard h4 {
	margin: 0;
}

.ai-chat-dashboard__tiles {
	display: grid;
	grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
	gap: 10px;
}

.ai-chat-dashboard__tile {
	display: flex;
	flex-direction: column;
	gap: 2px;
	padding: 12px;
	border: 1px solid var(--border-color, #e5e7eb);
	border-radius: 8px;
}

.ai-chat-dashboard__label,
.ai-chat-dashboard__note {
	font-size: var(--text-xs, 11px);
	color: var(--text-muted, #6b7280);
}

.ai-chat-dashboard__value {
	font-size: var(--text-xl, 18px);
	font-weight: 600;
}

.ai-chat-dashboard__card {
	padding: 10px;
	border: 1px solid var(--border-color, #e5e7eb);
	border-radius: 8px;
}
</style>
