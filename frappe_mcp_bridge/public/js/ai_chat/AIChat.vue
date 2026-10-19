<template>
	<div class="ai-chat" :class="[`ai-chat--${mode}`, { 'ai-chat--sidebar-open': sidebar_open }]">
		<div v-if="error" class="ai-chat__blocked">
			<p>{{ error }}</p>
		</div>

		<template v-else-if="boot">
			<Sidebar
				v-if="mode === 'page'"
				:conversations="conversations"
				:artifacts="artifact_rows"
				:tab="sidebar_tab"
				:active="conversation && conversation.name"
				:active-artifact="artifact_open"
				:archived="show_archived"
				:is-manager="boot.is_manager"
				:view-user="view_user"
				:usage="usage"
				@new="new_chat"
				@open="select"
				@open-artifact="open_listed_artifact"
				@search="(text) => ((search = text), load_list())"
				@tab="(value) => ((sidebar_tab = value), load_list())"
				@toggle-archived="(value) => ((show_archived = value), load_list())"
				@view-user="(user) => ((view_user = user), load_list())"
			/>

			<section class="ai-chat__main">
				<header class="ai-chat__header">
					<button
						v-if="mode === 'page'"
						class="btn btn-default btn-xs ai-chat__sidebar-toggle"
						@click="sidebar_open = !sidebar_open"
					>
						{{ __("Chats") }}
					</button>
					<div class="ai-chat__title" :title="title">
						<span class="ai-chat__title-text" @click="rename">{{ title }}</span>
						<span v-if="conversation && conversation.context_doctype" class="ai-chat__context">
							{{ conversation.context_doctype }} · {{ conversation.context_name }}
						</span>
						<span v-else-if="!conversation && context" class="ai-chat__context">
							{{ context.doctype }} · {{ context.name }}
						</span>
					</div>
					<div class="ai-chat__header-meta">
						<span v-if="conversation" class="ai-chat__total" :title="total_title">
							{{ money(conversation.cost_usd, "USD") }}
							<template v-if="show_local(conversation.display_currency)">
								· {{ money(conversation.cost_display, conversation.display_currency) }}
							</template>
						</span>
						<span v-if="mode === 'drawer' && usage" class="ai-chat__total">
							{{ __("Month") }}: {{ money(usage.spent_usd, "USD") }}
							<template v-if="usage.budget_usd">/ {{ money(usage.budget_usd, "USD") }}</template>
						</span>
						<button
							v-if="conversation && !read_only && mode === 'page'"
							class="btn btn-default btn-xs"
							:disabled="running"
							@click="toggle_archive"
						>
							{{ conversation.status === "Archived" ? __("Unarchive") : __("Archive") }}
						</button>
						<button
							v-if="mode === 'drawer' && conversation"
							class="btn btn-default btn-xs"
							@click="open_full_page"
						>
							{{ __("Open in AI Chat") }}
						</button>
						<button v-if="mode === 'drawer'" class="btn btn-default btn-xs" @click="$emit('close')">
							{{ __("Close") }}
						</button>
					</div>
				</header>

				<div v-if="read_only" class="ai-chat__banner">
					{{ __("You are viewing {0}'s chat. It is read-only.", [conversation.user_name]) }}
				</div>
				<div v-else-if="conversation && conversation.near_limit" class="ai-chat__banner">
					{{ __("This chat is nearly as long as the model can read.") }}
					<button class="btn btn-primary btn-xs" :disabled="running" @click="continue_chat">
						{{ __("Continue in a new chat") }}
					</button>
				</div>
				<div v-else-if="conversation && conversation.continued_from" class="ai-chat__banner">
					{{ __("This chat continues an earlier one, whose recent messages were carried over.") }}
				</div>

				<div ref="scroller" class="ai-chat__messages" @scroll="on_scroll">
					<div v-if="!messages.length && !running" class="ai-chat__empty">
						<h4>{{ __("How can I help, {0}?", [first_name]) }}</h4>
						<p v-if="model_info && model_info.erp_tools" class="text-muted">
							{{
								model_info.can_write
									? __("I can look up records and reports, and propose changes for you to approve.")
									: __("I can look up records and reports for you.")
							}}
						</p>
						<p v-else-if="model_info" class="text-muted">{{ model_info.erp_note }}</p>
					</div>

					<Message
						v-for="message in messages"
						:key="message.name"
						:message="message"
						:call="calls[message.tool_call_id]"
						:read-only="read_only"
						:busy="running"
						:show-local="show_local(message.display_currency)"
						:on-decide="decide"
						:open-artifact="artifact_open"
						@open-artifact="open_artifact"
					/>

					<div v-if="running" class="ai-chat__streaming">
						<div v-if="stream_text" class="ai-chat__bubble ai-chat__bubble--assistant">
							<div class="ai-chat__markdown" v-html="render(stream_text)"></div>
						</div>
						<div class="ai-chat__working">
							<span class="ai-chat__dots"><i></i><i></i><i></i></span>
							{{ tool_status ? __("Using {0}…", [tool_status]) : __("Thinking…") }}
						</div>
					</div>
				</div>

				<Composer
					v-if="!read_only"
					:models="boot.models"
					:model="selected_model"
					:running="running"
					:templates="boot.templates"
					:attachments="boot.attachments"
					:context="context_for_templates"
					:ensure-conversation="ensure_conversation"
					:submit="send"
					@change-model="(name) => (selected_model = name)"
					@stop="stop"
				/>
			</section>

			<ArtifactPanel
				v-if="artifact_open"
				:name="artifact_open"
				class="ai-chat__artifact"
				@close="artifact_open = null"
			/>
		</template>

		<div v-else class="ai-chat__loading text-muted">{{ __("Loading…") }}</div>
	</div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { call, EVENT } from "./api";
import { money, render_markdown } from "./format";
import ArtifactPanel from "./ArtifactPanel.vue";
import Composer from "./Composer.vue";
import Message from "./Message.vue";
import Sidebar from "./Sidebar.vue";

const props = defineProps({
	mode: { type: String, default: "page" },
	initialConversation: { type: String, default: null },
	context: { type: Object, default: null },
});
const emit = defineEmits(["close", "title"]);

const boot = ref(null);
const error = ref("");
const usage = ref(null);
const conversations = ref([]);
const conversation = ref(null);
const messages = ref([]);
const running = ref(false);
const stream_text = ref("");
const tool_status = ref("");
const selected_model = ref(null);
const search = ref("");
const show_archived = ref(false);
const view_user = ref("");
const sidebar_open = ref(false);
const artifact_open = ref(null);
const sidebar_tab = ref("chats");
const artifact_rows = ref([]);
const scroller = ref(null);
let stick_to_bottom = true;
// An artifact picked from the sidebar's list, opened once its chat has loaded.
let pending_artifact = null;
let last_event = 0;
let watchdog = null;

const read_only = computed(() => Boolean(conversation.value && conversation.value.read_only));
const title = computed(() =>
	conversation.value ? conversation.value.title || __("New chat") : __("New chat")
);
const first_name = computed(() => (boot.value ? boot.value.full_name.split(" ")[0] : ""));
const model_info = computed(() =>
	boot.value ? boot.value.models.find((m) => m.name === selected_model.value) : null
);
const context_for_templates = computed(() => {
	if (conversation.value && conversation.value.context_doctype) {
		return { doctype: conversation.value.context_doctype, name: conversation.value.context_name };
	}
	return props.context;
});
const total_title = computed(() => {
	const c = conversation.value;
	if (!c) return "";
	return __("{0} input · {1} cached · {2} output tokens", [
		format_number(c.input_tokens, null, 0),
		format_number(c.cached_input_tokens, null, 0),
		format_number(c.output_tokens, null, 0),
	]);
});

// Every assistant tool call, by id, so a tool result can say what it answered.
const calls = computed(() => {
	const map = {};
	for (const message of messages.value) {
		for (const tool_call of message.tool_calls || []) map[tool_call.id] = tool_call;
	}
	return map;
});

function render(text) {
	return render_markdown(text);
}

function show_local(currency) {
	return currency && currency !== "USD";
}

onMounted(async () => {
	frappe.realtime.on(EVENT, on_event);
	try {
		boot.value = await call("bootstrap", { doctype: props.context ? props.context.doctype : null });
	} catch (e) {
		error.value = __("AI Chat is not available to you. Ask an administrator for the AI Chat User role.");
		return;
	}
	usage.value = boot.value.usage;
	selected_model.value = boot.value.default_model;
	if (!boot.value.models.length) {
		error.value = __("No AI model is available to you yet. Ask an administrator to set one up.");
		return;
	}
	if (props.mode === "page") load_list();
	if (props.initialConversation) open(props.initialConversation);
	else emit("title", __("AI Chat"));
});

onBeforeUnmount(() => {
	frappe.realtime.off(EVENT, on_event);
	clearInterval(watchdog);
});

async function load_list() {
	if (sidebar_tab.value === "artifacts") {
		artifact_rows.value = await call("list_artifacts", { user: view_user.value || null });
		return;
	}
	conversations.value = await call("list_conversations", {
		search: search.value || null,
		archived: show_archived.value ? 1 : 0,
		user: view_user.value || null,
	});
}

async function open(name) {
	if (!name) return new_chat(false);
	try {
		const data = await call("get_conversation", { name });
		conversation.value = data.conversation;
		messages.value = data.messages;
		artifact_open.value = pending_artifact;
		pending_artifact = null;
		selected_model.value = data.conversation.model || selected_model.value;
		running.value = data.conversation.status === "Running";
		stream_text.value = "";
		emit("title", conversation.value.title);
		if (running.value) start_watchdog();
		scroll_down(true);
	} catch (e) {
		new_chat(false);
	}
}

function open_from_route(name) {
	if ((conversation.value && conversation.value.name) === name) return;
	open(name);
}

function select(name) {
	sidebar_open.value = false;
	frappe.set_route("ai-chat", name);
}

function new_chat(route = true) {
	conversation.value = null;
	messages.value = [];
	running.value = false;
	stream_text.value = "";
	sidebar_open.value = false;
	artifact_open.value = null;
	pending_artifact = null;
	emit("title", __("AI Chat"));
	if (route && props.mode === "page") frappe.set_route("ai-chat");
}

async function ensure_conversation() {
	if (conversation.value) return conversation.value.name;
	const created = await call("create_conversation", {
		model: selected_model.value,
		context_doctype: props.context ? props.context.doctype : null,
		context_name: props.context ? props.context.name : null,
	});
	conversation.value = created;
	if (props.mode === "page") {
		// Keep the address bar in step without reopening what is already open.
		frappe.route_flags.replace_route = true;
		frappe.set_route("ai-chat", created.name);
	}
	return created.name;
}

async function send({ text, files }) {
	const name = await ensure_conversation();
	const result = await call("send_message", {
		conversation: name,
		text,
		attachment_names: files.length ? JSON.stringify(files.map((f) => f.name)) : null,
		model: selected_model.value,
	});
	upsert(result.message);
	conversation.value = { ...conversation.value, ...result.conversation };
	emit("title", conversation.value.title);
	if (result.removed && result.removed.length) {
		frappe.show_alert(
			{ message: __("Removed before sending: {0}", [result.removed.join(", ")]), indicator: "orange" },
			8
		);
	}
	begin_running();
	if (props.mode === "page") load_list();
}

function begin_running() {
	running.value = true;
	stream_text.value = "";
	tool_status.value = "";
	last_event = Date.now();
	scroll_down(true);
	start_watchdog();
}

async function stop() {
	if (!conversation.value) return;
	await call("stop", { conversation: conversation.value.name });
}

// Awaited by the approval card, which keeps its buttons disabled until this settles.
async function decide(action, approve) {
	try {
		const result = await call("decide_action", { action, approve: approve ? 1 : 0 });
		if (result.running) begin_running();
	} finally {
		await refresh();
	}
}

function on_event(data) {
	if (!data || !conversation.value || data.conversation !== conversation.value.name) {
		if (data && data.type === "done" && props.mode === "page") load_list();
		return;
	}
	last_event = Date.now();

	if (data.type === "delta") {
		stream_text.value += data.text;
		scroll_down();
	} else if (data.type === "reset") {
		stream_text.value = "";
	} else if (data.type === "tool") {
		tool_status.value = data.name;
	} else if (data.type === "message") {
		upsert(data.message);
		if (data.message.role === "Assistant") stream_text.value = "";
		if (data.message.role === "Tool") tool_status.value = "";
		scroll_down();
	} else if (data.type === "done") {
		finish();
	}
}

async function finish() {
	running.value = false;
	stream_text.value = "";
	tool_status.value = "";
	clearInterval(watchdog);
	await refresh();
	usage.value = await call("my_usage");
	if (props.mode === "page") load_list();
}

// full reloads every message, to pick up changed approval cards; otherwise only what
// arrived after the last message held is fetched and added.
async function refresh(full = true) {
	if (!conversation.value) return;
	const after = messages.value.length ? Math.max(...messages.value.map((m) => m.seq)) : 0;
	const data = await call("poll", { name: conversation.value.name, after_seq: full ? 0 : after });
	conversation.value = data.conversation;
	if (full) messages.value = data.messages;
	else data.messages.forEach(upsert);
	emit("title", conversation.value.title);
	if (data.messages.length && data.messages[data.messages.length - 1].seq > after) scroll_down();
	return data;
}

function start_watchdog() {
	clearInterval(watchdog);
	watchdog = setInterval(async () => {
		if (!running.value) return clearInterval(watchdog);
		if (Date.now() - last_event < 4000) return;
		// No realtime events for a while: the socket may be down. Poll for what is new.
		const data = await refresh(false).catch(() => null);
		if (data && data.conversation.status !== "Running") finish();
	}, 3000);
}

async function continue_chat() {
	const created = await call("continue_in_new_chat", { conversation: conversation.value.name });
	if (props.mode === "page") {
		frappe.set_route("ai-chat", created.name);
	} else {
		conversation.value = created;
		messages.value = [];
		artifact_open.value = null;
		emit("title", created.title);
	}
}

function upsert(message) {
	const index = messages.value.findIndex((m) => m.name === message.name);
	if (index >= 0) messages.value.splice(index, 1, message);
	else {
		messages.value.push(message);
		messages.value.sort((a, b) => a.seq - b.seq);
		// An artifact made during this reply opens as soon as it arrives.
		if (running.value && message.artifact) open_artifact(message.artifact.name);
	}
}

function open_artifact(name) {
	artifact_open.value = name;
}

function open_listed_artifact(row) {
	if (conversation.value && conversation.value.name === row.conversation) {
		sidebar_open.value = false;
		return open_artifact(row.name);
	}
	pending_artifact = row.name;
	select(row.conversation);
}

function rename() {
	if (!conversation.value || read_only.value || running.value) return;
	frappe.prompt(
		{ fieldname: "title", fieldtype: "Data", label: __("Title"), default: conversation.value.title, reqd: 1 },
		async ({ title }) => {
			conversation.value = await call("rename_conversation", {
				conversation: conversation.value.name,
				title,
			});
			emit("title", conversation.value.title);
			load_list();
		},
		__("Rename Chat")
	);
}

async function toggle_archive() {
	const archived = conversation.value.status !== "Archived";
	conversation.value = await call("set_archived", { conversation: conversation.value.name, archived: archived ? 1 : 0 });
	frappe.show_alert({ message: archived ? __("Chat archived") : __("Chat restored"), indicator: "green" });
	load_list();
}

function open_full_page() {
	frappe.set_route("ai-chat", conversation.value.name);
}

function on_scroll() {
	const el = scroller.value;
	if (el) stick_to_bottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
}

function scroll_down(force = false) {
	if (!force && !stick_to_bottom) return;
	nextTick(() => {
		const el = scroller.value;
		if (el) el.scrollTop = el.scrollHeight;
	});
}

defineExpose({ open_from_route });
</script>

<style>
.ai-chat-page-section {
	padding: 0 !important;
}

.ai-chat {
	--ai-chat-gap: 12px;
	display: flex;
	height: calc(100vh - 140px);
	min-height: 480px;
	background: var(--card-bg, #fff);
	border: 1px solid var(--border-color, #e5e7eb);
	border-radius: var(--border-radius-md, 8px);
	overflow: hidden;
	color: var(--text-color, #1f2937);
}

.ai-chat--drawer {
	height: 100%;
	min-height: 0;
	border-radius: 0;
	border: none;
}

.ai-chat-drawer {
	position: fixed;
	top: 0;
	right: 0;
	bottom: 0;
	width: min(520px, 100vw);
	z-index: 1030;
	background: var(--card-bg, #fff);
	border-left: 1px solid var(--border-color, #e5e7eb);
	box-shadow: var(--shadow-lg, -8px 0 24px rgba(0, 0, 0, 0.12));
}

.ai-chat__blocked,
.ai-chat__loading {
	margin: auto;
	padding: 24px;
	text-align: center;
	max-width: 420px;
}

.ai-chat__main {
	flex: 1;
	min-width: 0;
	display: flex;
	flex-direction: column;
}

.ai-chat__artifact {
	width: min(48%, 720px);
	flex-shrink: 0;
}

/* The drawer has no room beside the chat, so the artifact covers it until closed. */
.ai-chat--drawer {
	position: relative;
}

.ai-chat--drawer .ai-chat__artifact {
	position: absolute;
	inset: 0;
	z-index: 6;
	width: auto;
	border-left: none;
}

.ai-chat__header {
	display: flex;
	align-items: center;
	gap: 8px;
	padding: 10px 16px;
	border-bottom: 1px solid var(--border-color, #e5e7eb);
}

.ai-chat__sidebar-toggle {
	display: none;
}

/* The drawer is narrow: title on its own line, costs and buttons beneath it. */
.ai-chat--drawer .ai-chat__header {
	flex-wrap: wrap;
}

.ai-chat--drawer .ai-chat__title {
	flex-basis: 100%;
}

.ai-chat--drawer .ai-chat__header-meta {
	flex-basis: 100%;
	justify-content: flex-end;
}

.ai-chat__title {
	flex: 1;
	min-width: 0;
	display: flex;
	flex-direction: column;
}

.ai-chat__title-text {
	font-weight: 600;
	white-space: nowrap;
	overflow: hidden;
	text-overflow: ellipsis;
	cursor: pointer;
}

.ai-chat__context {
	font-size: var(--text-xs, 11px);
	color: var(--text-muted, #6b7280);
}

.ai-chat__header-meta {
	display: flex;
	align-items: center;
	gap: 8px;
	flex-shrink: 0;
}

.ai-chat__total {
	font-size: var(--text-sm, 12px);
	color: var(--text-muted, #6b7280);
	white-space: nowrap;
}

.ai-chat__banner {
	padding: 8px 16px;
	font-size: var(--text-sm, 12px);
	background: var(--subtle-fg, #f3f4f6);
	color: var(--text-muted, #6b7280);
	border-bottom: 1px solid var(--border-color, #e5e7eb);
}

.ai-chat__messages {
	flex: 1;
	overflow-y: auto;
	padding: 16px;
	display: flex;
	flex-direction: column;
	gap: var(--ai-chat-gap);
}

.ai-chat__empty {
	margin: auto;
	text-align: center;
	max-width: 460px;
}

.ai-chat__bubble {
	max-width: min(780px, 92%);
	padding: 10px 14px;
	border-radius: 12px;
	line-height: 1.5;
	overflow-wrap: anywhere;
}

.ai-chat__bubble--assistant {
	align-self: flex-start;
	background: var(--subtle-fg, #f3f4f6);
}

.ai-chat__markdown > :first-child {
	margin-top: 0;
}

.ai-chat__markdown > :last-child {
	margin-bottom: 0;
}

.ai-chat__markdown table {
	border-collapse: collapse;
	margin: 8px 0;
	font-size: var(--text-sm, 12px);
	display: block;
	overflow-x: auto;
}

.ai-chat__markdown th,
.ai-chat__markdown td {
	border: 1px solid var(--border-color, #e5e7eb);
	padding: 4px 8px;
}

.ai-chat__markdown pre {
	background: var(--control-bg, #f3f4f6);
	padding: 8px 10px;
	border-radius: 6px;
	overflow-x: auto;
	font-size: var(--text-sm, 12px);
}

.ai-chat__streaming {
	display: flex;
	flex-direction: column;
	gap: 8px;
}

.ai-chat__working {
	font-size: var(--text-sm, 12px);
	color: var(--text-muted, #6b7280);
	display: flex;
	align-items: center;
	gap: 8px;
}

.ai-chat__dots i {
	display: inline-block;
	width: 6px;
	height: 6px;
	margin-right: 3px;
	border-radius: 50%;
	background: currentColor;
	animation: ai-chat-pulse 1.2s infinite ease-in-out;
}

.ai-chat__dots i:nth-child(2) {
	animation-delay: 0.2s;
}

.ai-chat__dots i:nth-child(3) {
	animation-delay: 0.4s;
}

@keyframes ai-chat-pulse {
	0%,
	80%,
	100% {
		opacity: 0.25;
	}
	40% {
		opacity: 1;
	}
}

@media (prefers-reduced-motion: reduce) {
	.ai-chat__dots i {
		animation: none;
		opacity: 0.6;
	}
}

@media (max-width: 768px) {
	.ai-chat {
		height: calc(100vh - 110px);
	}

	.ai-chat__sidebar-toggle {
		display: inline-block;
	}

	.ai-chat--page .ai-chat-sidebar {
		display: none;
	}

	.ai-chat--sidebar-open .ai-chat-sidebar {
		display: flex;
		position: absolute;
		z-index: 5;
		top: 0;
		bottom: 0;
		left: 0;
		width: min(300px, 85vw);
		box-shadow: var(--shadow-lg, 8px 0 24px rgba(0, 0, 0, 0.12));
	}

	.ai-chat--page {
		position: relative;
	}

	.ai-chat--page .ai-chat__artifact {
		position: absolute;
		inset: 0;
		z-index: 6;
		width: auto;
		border-left: none;
	}

	.ai-chat__total {
		display: none;
	}
}
</style>
