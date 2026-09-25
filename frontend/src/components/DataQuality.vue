<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { api, type CleaningReport } from "../api/client";
import { count } from "../utils/format";

const RULE_LABELS: Record<string, string> = {
  "1_unparseable_date": "① 日期无法解析 / 日历非法",
  "2_empty_amount": "② amount 为空",
  "3_qty_le_zero": "③ qty ≤ 0",
  "4_store_not_in_stores": "④ 脏门店外键",
  "5_product_not_in_products": "⑤ 脏商品外键",
  "6_duplicate_row": "⑥ 七字段完全重复",
};

const report = ref<CleaningReport | null>(null);
const warnings = ref<string[]>([]);

const rules = computed(() => {
  if (!report.value) return [];
  const entries = Object.entries(report.value.removed).filter(([k]) => !k.startsWith("note_"));
  const max = Math.max(...entries.map(([, v]) => v), 1);
  return entries.map(([key, value]) => ({
    label: RULE_LABELS[key] ?? key,
    value,
    width: Math.max(2, (value / max) * 100),
  }));
});

const removedTotal = computed(() => rules.value.reduce((sum, r) => sum + r.value, 0));
const conserved = computed(
  () => report.value && report.value.raw_rows === report.value.kept_rows + removedTotal.value,
);

onMounted(async () => {
  const payload = await api.dataQuality();
  report.value = payload.cleaning_report;
  warnings.value = payload.kb_warnings;
});
</script>

<template>
  <div class="card" v-if="report">
    <h3>数据质量（清洗六规则，按序首因归因）</h3>
    <p style="margin:0 0 10px" :style="{ color: conserved ? 'var(--down)' : 'var(--up)' }">
      守恒校验：保留 {{ count(report.kept_rows) }} + 剔除 {{ count(removedTotal) }}
      = 原始 {{ count(report.raw_rows) }} {{ conserved ? "✓" : "✗ 不守恒" }}
      <span class="muted">（其中销售 {{ count(report.kept_sales_rows) }} 行、退款 {{ count(report.kept_refund_rows) }} 行）</span>
    </p>
    <div v-for="rule in rules" :key="rule.label" style="display:flex; align-items:center; gap:10px; margin:5px 0">
      <span style="width:200px; flex:none">{{ rule.label }}</span>
      <div style="flex:1; background:#f0f1f3; border-radius:4px; height:14px">
        <div :style="{ width: rule.width + '%', background: 'var(--accent)', height: '100%', borderRadius: '4px' }"></div>
      </div>
      <span style="width:56px; text-align:right">{{ count(rule.value) }}</span>
    </div>
    <p v-for="w in warnings" :key="w" class="muted" style="margin:6px 0 0; font-size:12px">⚠ {{ w }}</p>
  </div>
</template>
