<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import * as echarts from "echarts";
import { api, type DailyPoint } from "../api/client";
import { useFiltersStore } from "../stores/filters";
import { shortDate } from "../utils/format";

const filters = useFiltersStore();
const el = ref<HTMLDivElement>();
let chart: echarts.ECharts | null = null;
let days: DailyPoint[] = [];

function render() {
  if (!chart) return;
  const markArea =
    filters.highlightWindow
      ? {
          itemStyle: { color: "rgba(37, 99, 235, 0.12)" },
          data: [[{ xAxis: filters.highlightWindow[0] }, { xAxis: filters.highlightWindow[1] }]],
        }
      : undefined;
  chart.setOption({
    grid: { left: 60, right: 20, top: 30, bottom: 30 },
    tooltip: {
      trigger: "axis",
      valueFormatter: (v: unknown) =>
        v === null || v === undefined ? "—" : "¥" + Number(v).toLocaleString("zh-CN"),
    },
    xAxis: { type: "category", data: days.map((d) => d.date), axisLabel: { formatter: shortDate } },
    yAxis: { type: "value", name: "净营业额" },
    series: [
      {
        name: "净营业额",
        type: "line",
        data: days.map((d) => d.net_revenue),
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 2, color: "#2563eb" },
        areaStyle: { color: "rgba(37, 99, 235, 0.08)" },
        markArea,
      },
    ],
  });
}

async function reload() {
  if (!filters.start || !filters.end) return;
  days = (await api.daily(filters)).days;
  render();
}

onMounted(() => {
  chart = echarts.init(el.value!);
  reload();
  window.addEventListener("resize", resize);
});
function resize() {
  chart?.resize();
}
onBeforeUnmount(() => {
  window.removeEventListener("resize", resize);
  chart?.dispose();
});

watch(
  () => [filters.start, filters.end, filters.store_id, filters.product_id],
  reload,
);
watch(() => filters.highlightWindow, render);
</script>

<template>
  <div class="card">
    <h3>每日净营业额趋势 <span v-if="filters.highlightWindow" class="muted" style="font-weight:400">（蓝底 = 对话中提到的区间）</span></h3>
    <div ref="el" style="height:260px"></div>
  </div>
</template>
