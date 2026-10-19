<template>
	<div class="ai-chat-chart">
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

defineExpose({ png: () => chart_png(element.value, props.spec.title) });
</script>

<style>
.ai-chat-chart {
	min-height: 120px;
}
</style>
