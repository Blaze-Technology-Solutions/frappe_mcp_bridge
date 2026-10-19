<template>
	<div class="ai-chat-composer">
		<div v-if="note" class="ai-chat-composer__note text-muted">{{ note }}</div>

		<div v-if="files.length || uploading" class="ai-chat-composer__files">
			<span v-for="(file, index) in files" :key="file.name" class="ai-chat-composer__file">
				{{ file.file_name }}
				<button class="btn btn-link btn-xs" :title="__('Remove')" @click="files.splice(index, 1)">×</button>
			</span>
			<span v-if="uploading" class="text-muted">{{ __("Uploading…") }}</span>
		</div>

		<textarea
			ref="input"
			v-model="text"
			class="form-control ai-chat-composer__input"
			rows="1"
			:placeholder="running ? __('Replying…') : __('Message the AI. Enter to send, Shift+Enter for a new line.')"
			:aria-label="__('Message')"
			@keydown.enter.exact.prevent="send"
			@input="grow"
		></textarea>

		<div class="ai-chat-composer__bar">
			<select
				class="form-control input-xs ai-chat-composer__model"
				:value="model"
				:disabled="running"
				:aria-label="__('Model')"
				@change="$emit('change-model', $event.target.value)"
			>
				<option v-for="option in models" :key="option.name" :value="option.name">
					{{ option.label }}
				</option>
			</select>

			<select
				v-if="templates.length"
				class="form-control input-xs ai-chat-composer__templates"
				:aria-label="__('Templates')"
				@change="use_template"
			>
				<option value="">{{ __("Templates…") }}</option>
				<option v-for="template in templates" :key="template.name" :value="template.name">
					{{ template.title }}
				</option>
			</select>

			<template v-if="can_attach">
				<button class="btn btn-default btn-xs" :disabled="running || uploading" @click="$refs.picker.click()">
					{{ __("Attach") }}
				</button>
				<input ref="picker" type="file" multiple hidden :accept="accept" @change="pick" />
			</template>

			<span class="ai-chat-composer__spacer"></span>

			<button v-if="running" class="btn btn-default btn-sm" @click="$emit('stop')">{{ __("Stop") }}</button>
			<button v-else class="btn btn-primary btn-sm" :disabled="!ready" @click="send">{{ __("Send") }}</button>
		</div>
	</div>
</template>

<script setup>
import { computed, nextTick, ref } from "vue";
import { call, upload } from "./api";

const props = defineProps({
	models: { type: Array, default: () => [] },
	model: { type: String, default: null },
	running: { type: Boolean, default: false },
	templates: { type: Array, default: () => [] },
	attachments: { type: Object, default: () => ({}) },
	context: { type: Object, default: null },
	ensureConversation: { type: Function, required: true },
	submit: { type: Function, required: true },
});
const emit = defineEmits(["stop", "change-model"]);

const text = ref("");
const files = ref([]);
const uploading = ref(false);
const sending = ref(false);
const input = ref(null);

const current = computed(() => props.models.find((m) => m.name === props.model) || {});
const can_attach = computed(() => props.attachments.allowed && current.value.may_read_erp_data);
const accept = computed(() =>
	[".pdf", ".csv", ".tsv", ".txt", ".md", ".json", ".xml", ".log", ".yaml", ".yml", ".html"]
		.concat(current.value.vision ? [".png", ".jpg", ".jpeg", ".gif", ".webp"] : [])
		.join(",")
);
const ready = computed(
	() => !props.running && !uploading.value && !sending.value && (text.value.trim() || files.value.length)
);
const note = computed(() => (current.value.erp_tools ? "" : current.value.erp_note));

async function send() {
	if (!ready.value) return;
	sending.value = true;
	try {
		// Awaited, so a refused message (over budget, say) stays in the box to retry.
		await props.submit({ text: text.value.trim(), files: [...files.value] });
		text.value = "";
		files.value = [];
		nextTick(grow);
	} catch (e) {
		// frappe.call has already shown the server's reason.
	} finally {
		sending.value = false;
	}
}

async function pick(event) {
	const picked = [...event.target.files];
	event.target.value = "";
	if (!picked.length) return;

	const limit = props.attachments.max_count || 5;
	if (files.value.length + picked.length > limit) {
		frappe.msgprint(__("Attach at most {0} files to one message.", [limit]));
		return;
	}

	uploading.value = true;
	try {
		const conversation = await props.ensureConversation();
		for (const file of picked) {
			if (file.size > (props.attachments.max_mb || 10) * 1024 * 1024) {
				frappe.msgprint(__("{0} is larger than {1} MB.", [file.name, props.attachments.max_mb]));
				continue;
			}
			const uploaded = await upload(conversation, file);
			files.value.push(uploaded);
			if (uploaded.removed && uploaded.removed.length) {
				frappe.show_alert(
					{
						message: __("Before sending, this will be taken out of {0}: {1}", [
							frappe.utils.escape_html(uploaded.file_name),
							uploaded.removed.join(", "),
						]),
						indicator: "orange",
					},
					10
				);
			}
		}
	} catch (e) {
		frappe.msgprint(e.message || __("The file could not be uploaded."));
	} finally {
		uploading.value = false;
	}
}

async function use_template(event) {
	const template = event.target.value;
	event.target.value = "";
	if (!template) return;

	const result = await call("render_template", {
		template,
		doctype: props.context ? props.context.doctype : null,
		name: props.context ? props.context.name : null,
	});
	text.value = result.text;
	if (result.model && props.models.some((m) => m.name === result.model)) emit("change-model", result.model);
	nextTick(() => {
		grow();
		input.value && input.value.focus();
	});
}

function grow() {
	const el = input.value;
	if (!el) return;
	el.style.height = "auto";
	el.style.height = Math.min(el.scrollHeight, 220) + "px";
}
</script>

<style>
.ai-chat-composer {
	border-top: 1px solid var(--border-color, #e5e7eb);
	padding: 10px 16px 12px;
	display: flex;
	flex-direction: column;
	gap: 8px;
}

.ai-chat-composer__note {
	font-size: var(--text-xs, 11px);
}

.ai-chat-composer__files {
	display: flex;
	flex-wrap: wrap;
	gap: 6px;
	font-size: var(--text-xs, 11px);
}

.ai-chat-composer__file {
	padding: 2px 4px 2px 8px;
	border-radius: 10px;
	background: var(--subtle-accent, #e5e7eb);
}

.ai-chat-composer__file .btn {
	padding: 0 4px;
	line-height: 1;
}

.ai-chat-composer__input {
	resize: none;
	min-height: 40px;
	max-height: 220px;
}

.ai-chat-composer__bar {
	display: flex;
	align-items: center;
	gap: 8px;
	flex-wrap: wrap;
}

.ai-chat-composer__model,
.ai-chat-composer__templates {
	width: auto;
	max-width: 220px;
}

.ai-chat-composer__spacer {
	flex: 1;
}
</style>
