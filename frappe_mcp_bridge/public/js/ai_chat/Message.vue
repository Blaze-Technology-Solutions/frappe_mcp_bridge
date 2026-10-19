<template>
	<!-- The user's own words: shown as typed, never rendered as HTML. -->
	<div v-if="message.role === 'User' && !message.is_note" class="ai-chat-msg ai-chat-msg--user">
		<div class="ai-chat__bubble ai-chat__bubble--user">
			<div class="ai-chat-msg__text">{{ message.content }}</div>
			<div v-if="message.attachments.length" class="ai-chat-msg__files">
				<a
					v-for="file in message.attachments"
					:key="file.name"
					:href="file.file_url"
					target="_blank"
					rel="noopener"
					class="ai-chat-msg__file"
					>{{ file.file_name }}</a
				>
			</div>
		</div>
		<div class="ai-chat-msg__meta">
			<span v-if="message.has_context">{{ __("Sent with the open document") }}</span>
			<span v-if="message.redacted" class="ai-chat-msg__warn">
				{{ __("Removed before sending: {0}", [message.redacted]) }}
			</span>
		</div>
	</div>

	<!-- Approve / Reject notes: written as the user, read by the model. -->
	<div v-else-if="message.role === 'User'" class="ai-chat-msg ai-chat-msg--note">
		<details>
			<summary>{{ first_line }}</summary>
			<pre>{{ message.content }}</pre>
		</details>
	</div>

	<div v-else-if="message.role === 'Assistant'" class="ai-chat-msg ai-chat-msg--assistant">
		<div
			v-if="message.content"
			class="ai-chat__bubble ai-chat__bubble--assistant"
			:class="{ 'is-excluded': message.excluded }"
		>
			<div class="ai-chat__markdown" v-html="html"></div>
		</div>
		<div v-if="message.tool_calls.length" class="ai-chat-msg__calls">
			<span v-for="tool_call in message.tool_calls" :key="tool_call.id" class="ai-chat-msg__call">
				{{ tool_call.name }}<template v-if="summary(tool_call)"> · {{ summary(tool_call) }}</template>
			</span>
		</div>
		<div class="ai-chat-msg__meta">
			<span>{{ message.model }}</span>
			<a href="#" @click.prevent="details = !details">
				{{ tokens(message.input_tokens + message.cached_input_tokens + message.cache_write_tokens) }}
				{{ __("in") }} · {{ tokens(message.output_tokens) }} {{ __("out") }} ·
				{{ money(message.cost_usd, "USD") }}
				<template v-if="showLocal"> · {{ money(message.cost_display, message.display_currency) }}</template>
			</a>
			<button v-if="message.content" class="btn btn-link btn-xs ai-chat-msg__copy" @click="copy">
				{{ __("Copy") }}
			</button>
		</div>
		<div v-if="details" class="ai-chat-msg__details">
			<span>{{ __("Input") }}: {{ tokens(message.input_tokens) }}</span>
			<span>{{ __("Cached") }}: {{ tokens(message.cached_input_tokens) }}</span>
			<span>{{ __("Cache write") }}: {{ tokens(message.cache_write_tokens) }}</span>
			<span>{{ __("Output") }}: {{ tokens(message.output_tokens) }}</span>
			<span v-if="message.reasoning_tokens">{{ __("Reasoning") }}: {{ tokens(message.reasoning_tokens) }}</span>
			<span>{{ __("Time") }}: {{ (message.latency_ms / 1000).toFixed(1) }}s</span>
			<span v-if="message.finish_reason">{{ __("Stop") }}: {{ message.finish_reason }}</span>
			<span v-if="message.usage_estimated" class="ai-chat-msg__warn">{{ __("Estimated usage") }}</span>
			<span v-if="message.excluded">{{ __("Declined; answered by the fallback model") }}</span>
		</div>
	</div>

	<div v-else-if="message.role === 'Tool'" class="ai-chat-msg ai-chat-msg--tool">
		<div v-if="action" class="ai-chat-approval" :class="`is-${action.status.toLowerCase()}`">
			<div class="ai-chat-approval__head">
				<strong>{{ heading }}</strong>
				<span class="indicator-pill" :class="pill">{{ __(action.status) }}</span>
			</div>
			<p v-if="action.status === 'Pending'" class="ai-chat-approval__hint">
				{{ __("Nothing has been saved yet. This is what the change would do:") }}
			</p>
			<details :open="action.status === 'Pending'">
				<summary>{{ __("Preview") }}</summary>
				<pre>{{ action.preview }}</pre>
			</details>
			<details>
				<summary>{{ __("Exact parameters") }}</summary>
				<pre>{{ action.params }}</pre>
			</details>
			<details v-if="action.result || action.error">
				<summary>{{ action.error ? __("Error") : __("Result") }}</summary>
				<pre>{{ action.error || action.result }}</pre>
			</details>
			<div v-if="action.status === 'Pending' && !readOnly" class="ai-chat-approval__actions">
				<button class="btn btn-primary btn-sm" :disabled="busy || deciding" @click="decide(true)">
					{{ __("Approve") }}
				</button>
				<button class="btn btn-default btn-sm" :disabled="busy || deciding" @click="decide(false)">
					{{ __("Reject") }}
				</button>
				<span class="text-muted">{{ __("Expires at {0}", [at(action.expires_at)]) }}</span>
			</div>
		</div>

		<details v-else class="ai-chat-tool" :class="{ 'is-error': message.is_error }">
			<summary>
				{{ message.is_error ? __("{0} failed", [message.tool_name]) : __("Result of {0}", [message.tool_name]) }}
				<template v-if="call && summary(call)"> · {{ summary(call) }}</template>
			</summary>
			<pre>{{ shown }}</pre>
			<a v-if="message.content.length > LIMIT && !full" href="#" @click.prevent="full = true">{{
				__("Show all")
			}}</a>
			<div v-if="message.mcp_bridge_log" class="ai-chat-msg__meta">
				<a :href="`/app/mcp-bridge-log/${message.mcp_bridge_log}`" target="_blank">
					{{ __("MCP Bridge Log {0}", [message.mcp_bridge_log]) }}
				</a>
			</div>
		</details>
	</div>

	<div v-else class="ai-chat-msg ai-chat-msg--notice" :class="{ 'is-error': message.is_error }">
		{{ message.content }}
	</div>
</template>

<script setup>
import { computed, ref } from "vue";
import { at, money, render_markdown, summarise_args, tokens } from "./format";

const LIMIT = 4000;

const props = defineProps({
	message: { type: Object, required: true },
	call: { type: Object, default: null },
	readOnly: { type: Boolean, default: false },
	busy: { type: Boolean, default: false },
	showLocal: { type: Boolean, default: false },
	// Returns a promise: the buttons stay disabled until the server has answered.
	onDecide: { type: Function, default: null },
});

const details = ref(false);
const full = ref(false);
const deciding = ref(false);

const html = computed(() => render_markdown(props.message.content));
const action = computed(() => props.message.action);
const first_line = computed(() => (props.message.content || "").split("\n")[0]);
const shown = computed(() =>
	full.value ? props.message.content : props.message.content.slice(0, LIMIT)
);
const heading = computed(() => {
	const a = action.value;
	const target = [a.reference_doctype, a.reference_name].filter(Boolean).join(" ");
	return target ? __("{0} on {1}", [a.tool, target]) : a.tool;
});
const pill = computed(
	() =>
		({ Pending: "orange", Approved: "green", Rejected: "gray", Expired: "gray", Failed: "red" })[
			action.value.status
		] || "gray"
);

function summary(tool_call) {
	return summarise_args(tool_call.args);
}

async function decide(approve) {
	if (deciding.value || !props.onDecide) return;
	deciding.value = true;
	try {
		await props.onDecide(action.value.name, approve);
	} catch (e) {
		// frappe.call has already shown the server's reason.
	} finally {
		deciding.value = false;
	}
}

function copy() {
	frappe.utils.copy_to_clipboard(props.message.content);
}
</script>

<style>
.ai-chat-msg {
	display: flex;
	flex-direction: column;
	gap: 4px;
}

.ai-chat-msg--user {
	align-items: flex-end;
}

.ai-chat__bubble--user {
	background: var(--primary, #2563eb);
	color: var(--white, #fff);
}

.ai-chat__bubble--user a {
	color: inherit;
	text-decoration: underline;
}

.ai-chat__bubble.is-excluded {
	opacity: 0.6;
}

.ai-chat-msg__text {
	white-space: pre-wrap;
}

.ai-chat-msg__files {
	display: flex;
	flex-wrap: wrap;
	gap: 6px;
	margin-top: 6px;
	font-size: var(--text-xs, 11px);
}

.ai-chat-msg__meta {
	display: flex;
	flex-wrap: wrap;
	align-items: center;
	gap: 8px;
	font-size: var(--text-xs, 11px);
	color: var(--text-muted, #6b7280);
}

.ai-chat-msg__meta a {
	color: inherit;
}

.ai-chat-msg__copy {
	padding: 0;
	font-size: inherit;
	color: inherit;
}

.ai-chat-msg__warn {
	color: var(--orange-600, #ea580c);
}

.ai-chat-msg__details {
	display: flex;
	flex-wrap: wrap;
	gap: 4px 12px;
	font-size: var(--text-xs, 11px);
	color: var(--text-muted, #6b7280);
}

.ai-chat-msg__calls {
	display: flex;
	flex-wrap: wrap;
	gap: 6px;
}

.ai-chat-msg__call {
	font-size: var(--text-xs, 11px);
	font-family: var(--font-stack-monospace, monospace);
	padding: 2px 8px;
	border-radius: 10px;
	background: var(--subtle-accent, #e5e7eb);
}

.ai-chat-msg--note,
.ai-chat-msg--notice {
	align-self: center;
	max-width: 92%;
	font-size: var(--text-sm, 12px);
	color: var(--text-muted, #6b7280);
}

.ai-chat-msg--notice.is-error {
	color: var(--red-600, #dc2626);
}

.ai-chat-msg--note summary,
.ai-chat-tool summary {
	cursor: pointer;
}

.ai-chat-msg pre {
	white-space: pre-wrap;
	overflow-wrap: anywhere;
	max-height: 360px;
	overflow-y: auto;
	margin: 6px 0 0;
	padding: 8px 10px;
	font-size: var(--text-xs, 11px);
	background: var(--control-bg, #f3f4f6);
	border-radius: 6px;
}

.ai-chat-tool {
	align-self: flex-start;
	max-width: min(780px, 92%);
	font-size: var(--text-sm, 12px);
	color: var(--text-muted, #6b7280);
	border-left: 2px solid var(--border-color, #e5e7eb);
	padding-left: 10px;
}

.ai-chat-tool.is-error {
	border-left-color: var(--red-400, #f87171);
}

.ai-chat-approval {
	align-self: flex-start;
	width: min(640px, 100%);
	padding: 12px 14px;
	border: 1px solid var(--border-color, #e5e7eb);
	border-left: 3px solid var(--orange-500, #f97316);
	border-radius: 8px;
	display: flex;
	flex-direction: column;
	gap: 8px;
}

.ai-chat-approval.is-approved {
	border-left-color: var(--green-500, #22c55e);
}

.ai-chat-approval.is-failed {
	border-left-color: var(--red-500, #ef4444);
}

.ai-chat-approval.is-rejected,
.ai-chat-approval.is-expired {
	border-left-color: var(--gray-400, #9ca3af);
}

.ai-chat-approval__head {
	display: flex;
	justify-content: space-between;
	align-items: center;
	gap: 8px;
}

.ai-chat-approval__hint {
	margin: 0;
	font-size: var(--text-sm, 12px);
}

.ai-chat-approval__actions {
	display: flex;
	align-items: center;
	gap: 8px;
	font-size: var(--text-xs, 11px);
}
</style>
