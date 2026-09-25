<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { api, type TracePayload } from "../api/client";
import { ANSWER_TYPE_COLOR, ANSWER_TYPE_LABEL } from "../utils/format";

const props = defineProps<{ traceId: string; answerType?: string }>();
const emit = defineEmits<{ (e: "close"): void }>();

const trace = ref<TracePayload | null>(null);
const error = ref("");
const expanded = ref<Set<number>>(new Set());

/** 契约 §6 要求的要素在 steps 里的呈现顺序。 */
const STEP_LABELS: Record<string, string> = {
  guard: "安全闸",
  plan: "规划（意图 / 槽位 / 窗口）",
  period_gate: "区间闸",
  intent: "意图复核",
  search: "检索",
  answer_mock: "作答（mock 规则管线）",
  answer_live: "作答（live 模型编排）",
  answer_live_failed: "live 失败回退",
  number_lock: "数字槽位锁定",
  response: "响应",
};

const steps = computed(() => trace.value?.steps ?? []);

function label(name: string): string {
  return STEP_LABELS[name] ?? name;
}

function toggle(index: number) {
  const next = new Set(expanded.value);
  if (next.has(index)) next.delete(index);
  else next.add(index);
  expanded.value = next;
}

function pretty(value: unknown): string {
  const text = JSON.stringify(value, null, 2);
  return text.length > 4000 ? text.slice(0, 4000) + "\n…（截断展示，完整内容在 trace 接口里）" : text;
}

onMounted(async () => {
  try {
    trace.value = await api.trace(props.traceId);
  } catch {
    error.value = "取不到这条 trace。";
  }
});
</script>

<template>
  <div class="overlay" @click.self="emit('close')">
    <div class="panel">
      <header>
        <div>
          <strong>调试面板</strong>
          <code class="muted">{{ traceId }}</code>
          <span
            v-if="answerType"
            class="badge"
            :style="{ background: ANSWER_TYPE_COLOR[answerType] ?? '#888' }"
          >{{ ANSWER_TYPE_LABEL[answerType] ?? answerType }}</span>
        </div>
        <div style="display:flex; gap:10px; align-items:center">
          <span v-if="trace" class="muted">总耗时 {{ trace.total_ms }} ms</span>
          <button @click="emit('close')">关闭</button>
        </div>
      </header>

      <p v-if="error" style="color:var(--up)">{{ error }}</p>
      <p v-if="trace" class="question">问：{{ trace.question }}</p>

      <section v-if="trace" class="timeline">
        <div v-for="(step, i) in steps" :key="i" class="step">
          <button class="step-head" @click="toggle(i)">
            <span class="dot"></span>
            <span class="step-name">{{ label(step.step) }}</span>
            <span class="muted">+{{ step.at_ms }} ms</span>
            <span v-if="step.took_ms !== null" class="muted">耗时 {{ step.took_ms }} ms</span>
            <span class="muted">{{ expanded.has(i) ? "▲" : "▼" }}</span>
          </button>
          <pre v-if="expanded.has(i)">{{ pretty(step.detail) }}</pre>
        </div>
      </section>

      <section v-if="trace && trace.llm_calls.length" class="llm">
        <h4>模型调用（{{ trace.llm_calls.length }} 次，完整提示词与原始输出）</h4>
        <details v-for="(call, i) in trace.llm_calls" :key="i">
          <summary>第 {{ i + 1 }} 次调用</summary>
          <pre>{{ pretty(call) }}</pre>
        </details>
      </section>

      <section v-if="trace && trace.errors.length" class="errors">
        <h4 style="color:var(--up)">错误（{{ trace.errors.length }}）</h4>
        <div v-for="(err, i) in trace.errors" :key="i" class="error-item">
          <strong>{{ err.where }} / {{ err.type }}</strong>：{{ err.message }}
          <pre>{{ err.traceback }}</pre>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.overlay {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  z-index: 100;
  display: flex;
  justify-content: flex-end;
}
.panel {
  width: min(760px, 92vw);
  height: 100%;
  background: var(--bg);
  overflow-y: auto;
  padding: 18px 22px 60px;
}
header { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
header code { margin-left: 10px; font-size: 12px; }
.badge {
  margin-left: 10px;
  color: #fff;
  border-radius: 4px;
  padding: 1px 8px;
  font-size: 12px;
}
.question { margin: 12px 0; font-size: 13px; }
.timeline { display: flex; flex-direction: column; }
.step { border-left: 2px solid var(--border); margin-left: 6px; }
.step-head {
  display: flex;
  gap: 10px;
  align-items: center;
  width: 100%;
  border: none;
  background: transparent;
  padding: 6px 10px;
  text-align: left;
  font-size: 13px;
}
.step-head:hover { background: #eceef1; }
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--accent);
  flex: none;
  margin-left: -15px;
  border: 2px solid #fff;
}
.step-name { font-weight: 600; }
pre {
  margin: 0 10px 10px 18px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px;
  font-size: 12px;
  max-height: 320px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
.llm, .errors { margin-top: 18px; }
.error-item { font-size: 13px; margin-bottom: 10px; }
</style>
