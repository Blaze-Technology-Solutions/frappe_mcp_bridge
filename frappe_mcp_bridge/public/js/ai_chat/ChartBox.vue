<template>
	<div class="ai-chat-chart">
		<!-- In its own row: laid over the chart, the chart's title took the clicks. -->
		<div v-if="pngButton && !failed" class="ai-chat-chart__bar">
			<button type="button" class="btn btn-default btn-xs ai-chat-chart__png" @click="save_png">
				{{ __("PNG") }}
			</button>
		</div>
		<div ref="element"></div>
		<p v-if="failed" class="text-muted">{{ __("This chart could not be drawn.") }}</p>
	</div>
</template>

<script setup>
import { nextTick, onMounted, ref, watch } from "vue";
import { chart_png, draw_chart } from "./artifacts";

const props = defineProps({
	spec: { type: Object, required: true },
	height: { type: Number, default: 260 },
	// Its own PNG button, for charts inside a dashboard.
	pngButton: { type: Boolean, default: false },
});

const element = ref(null);
const failed = ref(false);

function draw() {
	nextTick(() => {
		if (!element.value) return;
		element.value.innerHTML = "";
		try {
			draw_chart(element.value, props.spec, props.height);
			failed.value = false;
		} catch (e) {
			failed.value = true;
		}
	});
}

onMounted(draw);
watch(() => props.spec, draw);

function png() {
	return chart_png(element.value, props.spec.title);
}

function save_png() {
	png().catch(() => frappe.show_alert({ message: __("The picture could not be made."), indicator: "red" }));
}

defineExpose({ png });
</script>

<style>
.ai-chat-chart {
	min-height: 120px;
}

.ai-chat-chart__bar {
	display: flex;
	justify-content: flex-end;
}
</style>
