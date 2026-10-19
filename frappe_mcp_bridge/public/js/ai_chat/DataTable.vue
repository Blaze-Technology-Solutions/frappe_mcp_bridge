<template>
	<div class="ai-chat-table">
		<h5 v-if="table.title">{{ table.title }}</h5>
		<div class="ai-chat-table__scroll">
			<table>
				<thead>
					<tr>
						<th
							v-for="(column, index) in table.columns"
							:key="index"
							:class="{ 'is-number': column.type === 'number' }"
							@click="sort_by(index)"
						>
							{{ column.label }}
							<span v-if="sort.index === index">{{ sort.ascending ? "▲" : "▼" }}</span>
						</th>
					</tr>
				</thead>
				<tbody>
					<tr v-for="(row, rowIndex) in rows" :key="rowIndex">
						<td
							v-for="(cell, index) in row"
							:key="index"
							:class="{ 'is-number': table.columns[index].type === 'number' }"
						>
							{{ cell }}
						</td>
					</tr>
				</tbody>
			</table>
		</div>
		<p class="ai-chat-table__count text-muted">{{ __("{0} rows", [table.rows.length]) }}</p>
	</div>
</template>

<script setup>
import { computed, reactive } from "vue";

const props = defineProps({ table: { type: Object, required: true } });
const sort = reactive({ index: null, ascending: true });

const rows = computed(() => {
	if (sort.index === null) return props.table.rows;
	const index = sort.index;
	const direction = sort.ascending ? 1 : -1;
	return [...props.table.rows].sort((a, b) => {
		const x = a[index];
		const y = b[index];
		if (typeof x === "number" && typeof y === "number") return (x - y) * direction;
		return String(x ?? "").localeCompare(String(y ?? ""), undefined, { numeric: true }) * direction;
	});
});

function sort_by(index) {
	sort.ascending = sort.index === index ? !sort.ascending : true;
	sort.index = index;
}
</script>

<style>
.ai-chat-table h5 {
	margin: 0 0 8px;
}

.ai-chat-table__scroll {
	overflow: auto;
	max-height: 480px;
	border: 1px solid var(--border-color, #e5e7eb);
	border-radius: 6px;
}

.ai-chat-table table {
	width: 100%;
	border-collapse: collapse;
	font-size: var(--text-sm, 12px);
}

.ai-chat-table th,
.ai-chat-table td {
	padding: 6px 10px;
	border-bottom: 1px solid var(--border-color, #e5e7eb);
	text-align: left;
	white-space: nowrap;
}

.ai-chat-table th {
	position: sticky;
	top: 0;
	cursor: pointer;
	user-select: none;
	background: var(--subtle-fg, #f3f4f6);
}

.ai-chat-table .is-number {
	text-align: right;
	font-variant-numeric: tabular-nums;
}

.ai-chat-table__count {
	margin: 4px 0 0;
	font-size: var(--text-xs, 11px);
}
</style>
