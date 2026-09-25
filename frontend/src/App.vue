<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api, type Health, type MetaOptions } from "./api/client";
import { useFiltersStore } from "./stores/filters";
import FilterBar from "./components/FilterBar.vue";
import KpiCards from "./components/KpiCards.vue";
import TrendChart from "./components/TrendChart.vue";
import TopProducts from "./components/TopProducts.vue";
import DataQuality from "./components/DataQuality.vue";
import ChatPanel from "./components/ChatPanel.vue";

const filters = useFiltersStore();
const health = ref<Health | null>(null);
const meta = ref<MetaOptions | null>(null);
const loadError = ref("");

onMounted(async () => {
  try {
    [health.value, meta.value] = await Promise.all([api.health(), api.metaOptions()]);
    filters.setRange(meta.value.data_period.start, meta.value.data_period.end);
  } catch (err) {
    loadError.value = "连不上后端服务：请先按 README 起服务（默认 http://localhost:8000）。";
  }
});
</script>

<template>
  <div class="layout">
    <main class="dashboard">
      <header style="display:flex; align-items:baseline; gap:12px; margin-bottom:14px">
        <h1 style="font-size:18px; margin:0">Moneki.ai 经营看板</h1>
        <span v-if="health" class="muted" style="font-size:12px">
          今天 {{ health.today }} ｜ 数据 {{ health.data_period.start }} ~ {{ health.data_period.end }}
          ｜ 文档 {{ health.kb_docs }} 篇 / {{ health.kb_chunks }} 块
          ｜ 有效明细 {{ health.valid_sales_rows.toLocaleString() }} 行
          ｜ 模型 {{ health.llm_mode === "live" ? "已接入" : "mock 降级" }}
        </span>
      </header>
      <p v-if="loadError" style="color:var(--up)">{{ loadError }}</p>
      <template v-if="meta">
        <FilterBar :meta="meta" />
        <KpiCards />
        <TrendChart />
        <TopProducts />
        <DataQuality />
      </template>
    </main>
    <ChatPanel />
  </div>
</template>
