<script setup lang="ts">
import { useFiltersStore } from "../stores/filters";
import type { MetaOptions } from "../api/client";

defineProps<{ meta: MetaOptions }>();
const filters = useFiltersStore();

const quickRanges: { label: string; months: string }[] = [
  { label: "5 月", months: "05" },
  { label: "6 月", months: "06" },
  { label: "7 月", months: "07" },
  { label: "8 月", months: "08" },
];

function lastDayOf(month: string): string {
  return { "05": "31", "06": "30", "07": "31", "08": "31" }[month] ?? "31";
}

function pick(month: string) {
  filters.setRange(`2026-${month}-01`, `2026-${month}-${lastDayOf(month)}`);
}
</script>

<template>
  <div class="card" style="display:flex; flex-wrap:wrap; gap:10px; align-items:center">
    <label>开始 <input type="date" v-model="filters.start" :min="meta.data_period.start" :max="meta.data_period.end" /></label>
    <label>结束 <input type="date" v-model="filters.end" :min="meta.data_period.start" :max="meta.data_period.end" /></label>
    <label>门店
      <select v-model="filters.store_id">
        <option value="">全部</option>
        <option v-for="s in meta.stores" :key="s.store_id" :value="s.store_id">
          {{ s.store_id }} {{ s.store_name }}
        </option>
      </select>
    </label>
    <label>商品
      <select v-model="filters.product_id">
        <option value="">全部</option>
        <option v-for="p in meta.products" :key="p.product_id" :value="p.product_id">
          {{ p.product_name }}
        </option>
      </select>
    </label>
    <span style="display:flex; gap:6px">
      <button v-for="q in quickRanges" :key="q.months" @click="pick(q.months)">{{ q.label }}</button>
      <button @click="filters.setRange(meta.data_period.start, meta.data_period.end)">全区间</button>
    </span>
  </div>
</template>
