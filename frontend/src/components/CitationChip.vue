<script setup lang="ts">
import { ref } from "vue";
import type { Citation } from "../api/client";

defineProps<{ citation: Citation }>();
const open = ref(false);
</script>

<template>
  <span class="chip-wrap">
    <button class="chip" @click="open = !open">[{{ citation.doc_id }}]</button>
    <div v-if="open" class="chip-quote">{{ citation.quote }}</div>
  </span>
</template>

<style scoped>
.chip-wrap { position: relative; display: inline-block; }
.chip {
  border: 1px solid #c7d2fe;
  background: #eef2ff;
  color: #3730a3;
  border-radius: 4px;
  padding: 0 6px;
  font-size: 12px;
  line-height: 20px;
}
.chip-quote {
  position: absolute;
  z-index: 30;
  bottom: 26px;
  left: 0;
  width: 320px;
  max-height: 200px;
  overflow-y: auto;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.12);
  padding: 10px 12px;
  font-size: 12px;
  line-height: 1.7;
  white-space: pre-wrap;
}
</style>
