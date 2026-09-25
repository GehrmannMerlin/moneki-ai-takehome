<script setup lang="ts">
import { ref } from "vue";
import type { DataEvidence } from "../api/client";

defineProps<{ evidence: DataEvidence[] }>();
const open = ref(false);

function pretty(value: unknown): string {
  return JSON.stringify(value, null, 2);
}
</script>

<template>
  <div v-if="evidence.length" style="margin-top:6px">
    <button @click="open = !open" style="font-size:12px">
      {{ open ? "收起" : "查看" }}数据证据（{{ evidence.length }} 次查询）
    </button>
    <div v-if="open" class="evidence-list">
      <div v-for="(item, i) in evidence" :key="i" class="evidence-item">
        <div class="evidence-head">
          <code>{{ item.tool ?? "SQL" }}</code>
          <span class="muted">{{ item.params ? pretty(item.params) : item.sql }}</span>
        </div>
        <pre>{{ pretty(item.result) }}</pre>
      </div>
    </div>
  </div>
</template>

<style scoped>
.evidence-list { margin-top: 6px; display: flex; flex-direction: column; gap: 8px; }
.evidence-item {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px 10px;
  background: #fafbfc;
}
.evidence-head { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
.evidence-head code { color: var(--accent); font-weight: 600; }
.evidence-head span { font-size: 12px; word-break: break-all; }
pre {
  margin: 6px 0 0;
  font-size: 12px;
  max-height: 180px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
