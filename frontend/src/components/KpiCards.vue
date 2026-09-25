<script setup lang="ts">
import { ref, watch } from "vue";
import { api, type Summary } from "../api/client";
import { useFiltersStore } from "../stores/filters";
import { money, count } from "../utils/format";

const filters = useFiltersStore();
const summary = ref<Summary | null>(null);

watch(
  () => [filters.start, filters.end, filters.store_id, filters.product_id],
  async () => {
    if (!filters.start || !filters.end) return;
    summary.value = await api.summary(filters);
  },
  { immediate: true },
);
</script>

<template>
  <div class="kpi-row" v-if="summary">
    <div class="kpi"><div class="label">净营业额</div><div class="value">{{ money(summary.net_revenue) }}</div></div>
    <div class="kpi"><div class="label">有效订单数</div><div class="value">{{ count(summary.orders) }}</div></div>
    <div class="kpi"><div class="label">客单价</div><div class="value">{{ money(summary.aov) }}</div></div>
    <div class="kpi"><div class="label">销量</div><div class="value">{{ count(summary.qty) }}</div></div>
    <div class="kpi"><div class="label">退款金额</div><div class="value">{{ money(summary.refund_amount) }}</div></div>
  </div>
</template>
