<script setup lang="ts">
import { nextTick, ref } from "vue";
import { api, type ChatAnswer } from "../api/client";
import { useFiltersStore } from "../stores/filters";
import { ANSWER_TYPE_COLOR, ANSWER_TYPE_LABEL } from "../utils/format";
import CitationChip from "./CitationChip.vue";
import EvidenceDrawer from "./EvidenceDrawer.vue";
import TraceOverlay from "./TraceOverlay.vue";

interface Turn {
  question: string;
  answer: ChatAnswer;
}

const filters = useFiltersStore();
const turns = ref<Turn[]>([]);
const draft = ref("");
const sending = ref(false);
const listEl = ref<HTMLDivElement>();
const traceId = ref("");
const traceType = ref<string>();

/** 刷新不断会话、新标签不串线（呼应 D14：会话按 session_id 隔离）。 */
const sessionId =
  sessionStorage.getItem("moneki-session") ??
  (() => {
    const id = crypto.randomUUID();
    sessionStorage.setItem("moneki-session", id);
    return id;
  })();

const ISO_RANGE = /(20\d{2}-\d{2}-\d{2})\s*[至到~—-]\s*(20\d{2}-\d{2}-\d{2})/;
const ISO_DAY = /(20\d{2}-\d{2}-\d{2})/;

/** 图表联动：回答里提到日期窗口时，让趋势图高亮那一段。 */
function linkChart(answer: string) {
  const range = answer.match(ISO_RANGE);
  if (range) {
    filters.highlight([range[1], range[2]]);
    return;
  }
  const day = answer.match(ISO_DAY);
  if (day) filters.highlight([day[1], day[1]]);
}

async function send() {
  const question = draft.value.trim();
  if (!question || sending.value) return;
  sending.value = true;
  draft.value = "";
  try {
    const answer = await api.chat(sessionId, question);
    turns.value.push({ question, answer });
    linkChart(answer.answer);
  } catch {
    turns.value.push({
      question,
      answer: {
        answer: "这次请求失败了（网络或服务异常），请重试。",
        answer_type: "refusal",
        citations: [],
        data_evidence: [],
        trace_id: "",
      },
    });
  } finally {
    sending.value = false;
    await nextTick();
    listEl.value?.scrollTo({ top: listEl.value.scrollHeight, behavior: "smooth" });
  }
}

const SAMPLES = [
  "618 当天 S02 的牛肉poke 卖了多少份？达到目标了吗？",
  "8 月 3 日 S05 的现金支付占比是多少？为什么会这样？",
  "外卖订单多久内可以申请退款？",
  "三文鱼那次断供，供应商最后赔了我们多少钱？",
];
</script>

<template>
  <aside class="chat">
    <header>
      <strong>经营助手</strong>
      <span class="muted" style="font-size:12px">会话 {{ sessionId.slice(0, 8) }}</span>
    </header>

    <div ref="listEl" class="turns">
      <div v-if="!turns.length" class="welcome">
        <p class="muted">可以问经营数字、公司制度，或两者混着问：</p>
        <button v-for="s in SAMPLES" :key="s" class="sample" @click="draft = s">{{ s }}</button>
      </div>
      <div v-for="(turn, i) in turns" :key="i" class="turn">
        <div class="q">{{ turn.question }}</div>
        <div class="a">
          <div class="a-head">
            <span class="badge" :style="{ background: ANSWER_TYPE_COLOR[turn.answer.answer_type] }">
              {{ ANSWER_TYPE_LABEL[turn.answer.answer_type] ?? turn.answer.answer_type }}
            </span>
            <button
              v-if="turn.answer.trace_id"
              class="trace-btn"
              @click="traceId = turn.answer.trace_id; traceType = turn.answer.answer_type"
            >调试面板</button>
          </div>
          <p class="a-text">{{ turn.answer.answer }}</p>
          <div v-if="turn.answer.citations.length" class="chips">
            <CitationChip v-for="c in turn.answer.citations" :key="c.doc_id + c.quote.slice(0, 12)" :citation="c" />
          </div>
          <EvidenceDrawer :evidence="turn.answer.data_evidence" />
        </div>
      </div>
      <div v-if="sending" class="thinking">思考中…</div>
    </div>

    <footer>
      <input
        type="text"
        v-model="draft"
        placeholder="问点什么，例如：7 月 S02 的净营业额是多少？"
        @keyup.enter="send"
      />
      <button class="primary" :disabled="sending || !draft.trim()" @click="send">发送</button>
    </footer>

    <TraceOverlay v-if="traceId" :trace-id="traceId" :answer-type="traceType" @close="traceId = ''" />
  </aside>
</template>

<style scoped>
.chat {
  border-left: 1px solid var(--border);
  background: var(--panel);
  display: flex;
  flex-direction: column;
  height: 100vh;
}
header {
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}
.turns { flex: 1; overflow-y: auto; padding: 14px 16px; }
.welcome .sample {
  display: block;
  width: 100%;
  text-align: left;
  margin: 6px 0;
  padding: 8px 10px;
  line-height: 1.5;
}
.turn { margin-bottom: 16px; }
.q {
  background: #eef2ff;
  border-radius: 8px;
  padding: 8px 12px;
  margin-bottom: 8px;
  font-size: 13px;
}
.a { font-size: 13px; }
.a-head { display: flex; gap: 8px; align-items: center; margin-bottom: 4px; }
.badge { color: #fff; border-radius: 4px; padding: 1px 8px; font-size: 12px; }
.trace-btn { font-size: 12px; padding: 1px 8px; }
.a-text { margin: 4px 0; line-height: 1.8; white-space: pre-wrap; }
.chips { display: flex; gap: 6px; flex-wrap: wrap; margin: 4px 0; }
.thinking { color: var(--text-dim); font-size: 13px; padding: 4px 0; animation: blink 1.2s infinite; }
@keyframes blink { 50% { opacity: 0.35; } }
footer {
  border-top: 1px solid var(--border);
  padding: 10px 12px;
  display: flex;
  gap: 8px;
}
footer input { flex: 1; }
</style>
