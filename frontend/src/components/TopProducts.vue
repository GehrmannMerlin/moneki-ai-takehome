<script setup lang="ts">
import { ref, watch } from "vue";
import { api, type TopProduct } from "../api/client";
import { useFiltersStore } from "../stores/filters";
import { money, count } from "../utils/format";

const filters = useFiltersStore();
const products = ref<TopProduct[]>([]);

watch(
  () => [filters.start, filters.end, filters.store_id],
  async () => {
    if (!filters.start || !filters.end) return;
    products.value = (await api.topProducts(filters, 10)).products;
  },
  { immediate: true },
);
</script>

<template>
  <div class="card">
    <h3>商品排行榜（按净营业额）</h3>
    <table>
      <thead>
        <tr><th>#</th><th>商品</th><th>品类</th><th style="text-align:right">净营业额</th><th style="text-align:right">订单</th><th style="text-align:right">销量</th></tr>
      </thead>
      <tbody>
        <tr v-for="(p, i) in products" :key="p.product_id">
          <td class="muted">{{ i + 1 }}</td>
          <td>{{ p.product_name }}</td>
          <td class="muted">{{ p.product_category }}</td>
          <td style="text-align:right">{{ money(p.net_revenue) }}</td>
          <td style="text-align:right">{{ count(p.orders) }}</td>
          <td style="text-align:right">{{ count(p.qty) }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
