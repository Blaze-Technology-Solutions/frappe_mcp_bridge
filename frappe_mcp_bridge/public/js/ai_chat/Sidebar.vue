<template>
	<aside class="ai-chat-sidebar">
		<div class="ai-chat-sidebar__top">
			<button class="btn btn-primary btn-sm btn-block" @click="$emit('new')">{{ __("New chat") }}</button>
			<div class="ai-chat-sidebar__tabs" role="tablist">
				<button
					v-for="option in TABS"
					:key="option.value"
					type="button"
					role="tab"
					class="btn btn-xs"
					:class="tab === option.value ? 'btn-default is-active' : 'btn-link'"
					:aria-selected="tab === option.value ? 'true' : 'false'"
					@click="$emit('tab', option.value)"
				>
					{{ option.label }}
				</button>
			</div>
			<input
				class="form-control input-sm"
				type="search"
				:placeholder="tab === 'artifacts' ? __('Search artifacts') : __('Search chats')"
				:value="query"
				@input="on_search"
			/>
			<div v-if="isManager" class="ai-chat-sidebar__audit">
				<select
					class="form-control input-sm"
					:value="viewUser ? 'all' : ''"
					:aria-label="__('Whose chats')"
					@change="(event) => ((who = ''), $emit('view-user', event.target.value))"
				>
					<option value="">{{ __("My chats") }}</option>
					<option value="all">{{ __("Everyone's chats (read-only)") }}</option>
				</select>
				<input
					v-if="viewUser"
					v-model="who"
					class="form-control input-sm"
					type="search"
					:placeholder="__('Only this user (email)')"
					@change="$emit('view-user', who.trim() || 'all')"
				/>
			</div>
		</div>

		<nav v-if="tab === 'artifacts'" class="ai-chat-sidebar__list">
			<a
				v-for="row in shown_artifacts"
				:key="row.name"
				class="ai-chat-sidebar__item"
				:class="{ 'is-active': row.name === activeArtifact }"
				href="#"
				@click.prevent="$emit('open-artifact', row)"
			>
				<span class="ai-chat-sidebar__item-title">{{ ICONS[row.artifact_type] }} {{ row.title }}</span>
				<span class="ai-chat-sidebar__item-meta">
					<template v-if="viewUser">{{ row.user }} · </template>
					{{ __(row.artifact_type) }}
					<template v-if="row.version > 1"> · {{ __("Version {0}", [row.version]) }}</template>
					· {{ when(row.creation) }}
				</span>
			</a>
			<p v-if="!shown_artifacts.length" class="ai-chat-sidebar__none text-muted">
				{{ query ? __("No artifacts match.") : __("No artifacts yet. Ask for a chart, table or document.") }}
			</p>
		</nav>

		<nav v-else class="ai-chat-sidebar__list">
			<a
				v-for="row in conversations"
				:key="row.name"
				class="ai-chat-sidebar__item"
				:class="{ 'is-active': row.name === active }"
				href="#"
				@click.prevent="$emit('open', row.name)"
			>
				<span class="ai-chat-sidebar__item-title">
					<span v-if="row.status === 'Running'" class="ai-chat-sidebar__live" :title="__('Replying')"></span>
					{{ row.title || __("New chat") }}
				</span>
				<span class="ai-chat-sidebar__item-meta">
					<template v-if="viewUser">{{ row.user }} · </template>
					{{ when(row.last_activity) }}
					<template v-if="row.cost_usd"> · {{ money(row.cost_usd, "USD") }}</template>
				</span>
			</a>
			<p v-if="!conversations.length" class="ai-chat-sidebar__none text-muted">
				{{ archived ? __("No archived chats.") : __("No chats yet.") }}
			</p>
		</nav>

		<div class="ai-chat-sidebar__bottom">
			<label v-if="tab === 'chats'" class="ai-chat-sidebar__toggle">
				<input type="checkbox" :checked="archived" @change="$emit('toggle-archived', $event.target.checked)" />
				{{ __("Show archived") }}
			</label>

			<div v-if="usage" class="ai-chat-usage">
				<div class="ai-chat-usage__line">
					<span>{{ __("This month") }}</span>
					<strong>
						{{ money(usage.spent_usd, "USD") }}
						<template v-if="usage.budget_usd"> / {{ money(usage.budget_usd, "USD") }}</template>
					</strong>
				</div>
				<div v-if="usage.currency !== 'USD'" class="ai-chat-usage__local text-muted">
					{{ money(usage.spent_display, usage.currency) }}
					<template v-if="usage.budget_usd"> / {{ money(usage.budget_display, usage.currency) }}</template>
				</div>
				<div
					v-if="usage.budget_usd"
					class="ai-chat-usage__bar"
					role="progressbar"
					:aria-valuenow="usage.percent"
					aria-valuemin="0"
					aria-valuemax="100"
				>
					<span :style="{ width: Math.min(usage.percent || 0, 100) + '%' }" :class="level"></span>
				</div>
				<div v-if="usage.daily_limit" class="ai-chat-usage__local text-muted">
					{{ __("{0} of {1} messages today", [usage.messages_today, usage.daily_limit]) }}
				</div>
			</div>
		</div>
	</aside>
</template>

<script setup>
import { computed, ref } from "vue";
import { ICONS } from "./artifacts";
import { money, when } from "./format";

const TABS = [
	{ value: "chats", label: __("Chats") },
	{ value: "artifacts", label: __("Artifacts") },
];

const props = defineProps({
	conversations: { type: Array, default: () => [] },
	artifacts: { type: Array, default: () => [] },
	tab: { type: String, default: "chats" },
	active: { type: String, default: null },
	activeArtifact: { type: String, default: null },
	archived: { type: Boolean, default: false },
	isManager: { type: Boolean, default: false },
	viewUser: { type: String, default: "" },
	usage: { type: Object, default: null },
});
const emit = defineEmits(["new", "open", "open-artifact", "search", "tab", "toggle-archived", "view-user"]);

const query = ref("");
// Artifacts are few enough to filter here; chats are searched on the server.
const shown_artifacts = computed(() => {
	const text = query.value.trim().toLowerCase();
	return text ? props.artifacts.filter((row) => row.title.toLowerCase().includes(text)) : props.artifacts;
});
const who = ref("");
let timer = null;

const level = computed(() => {
	const percent = (props.usage && props.usage.percent) || 0;
	return percent >= 100 ? "is-over" : percent >= 80 ? "is-near" : "";
});

function on_search(event) {
	query.value = event.target.value;
	clearTimeout(timer);
	timer = setTimeout(() => emit("search", query.value.trim()), 300);
}
</script>

<style>
.ai-chat-sidebar {
	width: 280px;
	flex-shrink: 0;
	display: flex;
	flex-direction: column;
	border-right: 1px solid var(--border-color, #e5e7eb);
	background: var(--card-bg, #fff);
}

.ai-chat-sidebar__top {
	padding: 12px;
	display: flex;
	flex-direction: column;
	gap: 8px;
	border-bottom: 1px solid var(--border-color, #e5e7eb);
}

.ai-chat-sidebar__tabs {
	display: flex;
	gap: 4px;
}

.ai-chat-sidebar__tabs .btn {
	flex: 1;
}

.ai-chat-sidebar__audit {
	display: flex;
	flex-direction: column;
	gap: 6px;
}

.ai-chat-sidebar__list {
	flex: 1;
	overflow-y: auto;
	padding: 6px;
}

.ai-chat-sidebar__item {
	display: flex;
	flex-direction: column;
	padding: 8px 10px;
	border-radius: 6px;
	color: inherit;
	text-decoration: none !important;
}

.ai-chat-sidebar__item:hover {
	background: var(--subtle-fg, #f3f4f6);
}

.ai-chat-sidebar__item.is-active {
	background: var(--subtle-accent, #e5e7eb);
}

.ai-chat-sidebar__item-title {
	font-size: var(--text-md, 13px);
	white-space: nowrap;
	overflow: hidden;
	text-overflow: ellipsis;
}

.ai-chat-sidebar__item-meta {
	font-size: var(--text-xs, 11px);
	color: var(--text-muted, #6b7280);
	white-space: nowrap;
	overflow: hidden;
	text-overflow: ellipsis;
}

.ai-chat-sidebar__live {
	display: inline-block;
	width: 7px;
	height: 7px;
	margin-right: 4px;
	border-radius: 50%;
	background: var(--green-500, #22c55e);
}

.ai-chat-sidebar__none {
	padding: 12px;
	font-size: var(--text-sm, 12px);
}

.ai-chat-sidebar__bottom {
	padding: 12px;
	border-top: 1px solid var(--border-color, #e5e7eb);
	display: flex;
	flex-direction: column;
	gap: 10px;
	font-size: var(--text-sm, 12px);
}

.ai-chat-sidebar__toggle {
	display: flex;
	align-items: center;
	gap: 6px;
	margin: 0;
	font-weight: normal;
}

.ai-chat-usage__line {
	display: flex;
	justify-content: space-between;
	gap: 8px;
}

.ai-chat-usage__local {
	font-size: var(--text-xs, 11px);
}

.ai-chat-usage__bar {
	margin-top: 6px;
	height: 6px;
	border-radius: 3px;
	background: var(--subtle-accent, #e5e7eb);
	overflow: hidden;
}

.ai-chat-usage__bar span {
	display: block;
	height: 100%;
	background: var(--primary, #2563eb);
}

.ai-chat-usage__bar span.is-near {
	background: var(--orange-500, #f97316);
}

.ai-chat-usage__bar span.is-over {
	background: var(--red-500, #ef4444);
}
</style>
