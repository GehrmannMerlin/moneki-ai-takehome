# Generalization Round 5 — Semantic Conversation State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace raw-history/string-rewrite follow-up handling with durable, epoch-bound semantic conversation state while preserving R1–R4 authority boundaries and public API behavior.

**Architecture:** Introduce a serializable `ConversationState` plus `ContextPatch` in `starter/kbqa/conversation.py`. `SessionStore` persists the semantic state, turns, retention, and LRU metadata in SQLite; `Service` loads state before planning and commits state with a successful turn only. `FollowUps` becomes a deterministic resolver that merges explicit current slots over state slots; `Planner` accepts state as its primary context, and LiveEngine receives structured state and the current canonical Plan without prior assistant answer text.

**Tech Stack:** Python 3.11-compatible production code, dataclasses, SQLite, pytest, FastAPI service, existing mock/live engines and R1–R4 receipt/trace infrastructure.

**Spec:** `C:\Users\韩吉衍\.codex\attachments\68e7f83e-75f6-4a3f-a6c3-a3d2f45eaad0\已粘贴的文本.txt`

## Global Constraints

- Conversation transcript is for debug/UI/trace only and is never business-planning authority.
- Conversation state stores semantics only; it must not persist answer numbers, raw business facts, or document contents.
- Canonical Plan outranks ConversationState; ToolReceipt/KnowledgeReceipt remain current-turn factual authority.
- Explicit current values override inherited state; a new topic must clear stale entity/time/metric scope.
- Default windows never enter `recent_windows`; explicit, derived, or execution-resolved windows may.
- Every persisted state is bound to the current data/KB context epoch; epoch changes invalidate old state.
- `session_id=None` is ephemeral and must not read or write persisted conversation state.
- `max_turns` physically prunes SQLite rows; `max_sessions` performs real LRU eviction without deleting traces.
- Same-turn DeepSeek assistant/tool/reasoning messages remain intact; only cross-turn assistant answers are removed.
- Do not add entity-specific production branches, evaluator edits, gold answers, API keys, or Round 6/7 work.

## Review Focus

- A successful turn must durably save state, while refusal/clarify/pipeline failure must leave the prior state unchanged.
- A dataset/KB replacement with the same session ID must invalidate old entity/topic state rather than silently reuse it.
- A follow-up must independently override one slot (store, metric, or window) without discarding other inherited slots.
- A live model must not see an earlier assistant business number as factual context, even when the transcript contains it.
- Effective execution scope must come from validated evidence parameters, not from parsing answer prose or Plan defaults.

---

### Task 1: R5 RED suite and confirmed defect ledger

**Files:**
- Create: `starter/tests/generalization/test_conversation_state.py`
- Create: `docs/_r5_red.txt`
- Modify: `DEBUG_LOG.md` with only confirmed R5 defects after the probe

**Interfaces:**
- Consumes: current `Service`, `Planner`, `FollowUps`, `SessionStore`, `LiveEngine` behavior.
- Produces: failing tests covering persistence, semantic merge, lifecycle, live context isolation, epoch invalidation, and public multi-turn shapes.

- [ ] **Step 1: Write failing tests** for the actual candidate defects, using synthetic entities where possible and existing public data only for regression shapes.
- [ ] **Step 2: Run `pytest tests/generalization/test_conversation_state.py -q`** and capture the real failure/pass counts in `docs/_r5_red.txt`.
- [ ] **Step 3: Classify every failure** as a production gap or test-construction problem; fix only construction problems before committing the RED suite.
- [ ] **Step 4: Commit** `test(session): reproduce R5 semantic-state and follow-up failures`.

### Task 2: ConversationState model and SQLite lifecycle

**Files:**
- Create: `starter/kbqa/conversation.py`
- Modify: `starter/kbqa/core/store.py`
- Test: `starter/tests/generalization/test_conversation_state.py`

**Interfaces:**
- Consumes: current SQLite `sessions`, `turns`, and `traces` schema.
- Produces: `ConversationState.from_dict/to_dict`, `ContextPatch`, `SessionStore.load_state(session_id, epoch)`, `save_state`, atomic turn/state save, physical turn pruning, and max-session LRU eviction.

- [ ] **Step 1: Add focused storage/model assertions** for restart persistence, malformed/legacy state normalization, no-session ephemerality, epoch invalidation, row-count retention, and LRU eviction.
- [ ] **Step 2: Run the focused tests** and confirm they fail because the new state/lifecycle APIs are absent or unused.
- [ ] **Step 3: Implement** schema-versioned semantic state serialization, epoch checks, bounded storage, and an atomic save path without changing trace retention.
- [ ] **Step 4: Run the focused storage tests and existing store regressions**; keep public response schema unchanged.
- [ ] **Step 5: Commit** `feat(session): add durable semantic ConversationState`.

### Task 3: Semantic resolver and Planner migration

**Files:**
- Modify: `starter/kbqa/conversation.py`
- Modify: `starter/kbqa/followup.py`
- Modify: `starter/kbqa/planner.py`
- Test: `starter/tests/generalization/test_conversation_state.py`, `starter/tests/generalization/test_planner_authority.py`

**Interfaces:**
- Consumes: `ConversationState`, `ContextPatch`, dynamic catalog, existing time/entity/intent parsers.
- Produces: deterministic resolution of explicit/inherited/derived/default slots, `continuation` metadata, safe new-topic reset, topic anchors, discourse operators, and `Planner.plan(question, state)` as the main API. Legacy list input may remain a compatibility adapter for existing tests only.

- [ ] **Step 1: Add failing semantic follow-up assertions** for time/store/metric overrides, two recent windows, actual/by-store/why operators, current↔historical switches, event topic continuity without stale time, ambiguity clarification, and default-window exclusion.
- [ ] **Step 2: Run the semantic test subset** and verify failures reflect string reconstruction/turn-slot dependence.
- [ ] **Step 3: Implement** state-based resolution without re-feeding previous standalone text into the parser; render standalone only for trace/debug compatibility.
- [ ] **Step 4: Run R3 planner regressions and the new semantic suite**; ensure canonical Plan remains the only planning authority.
- [ ] **Step 5: Commit** `refactor(followup): replace string reconstruction with semantic state merge`.

### Task 4: Service state transition and execution-derived scope

**Files:**
- Modify: `starter/kbqa/service.py`
- Modify: `starter/kbqa/schemas.py`
- Modify: `starter/kbqa/answerer.py` or finalisation boundary as needed
- Test: `starter/tests/generalization/test_conversation_state.py`

**Interfaces:**
- Consumes: state-aware Plan, validated `Answer.data_evidence`, `Answer.citations`, and current manifest fingerprints.
- Produces: `state before → resolved Plan → Answer → ContextPatch → state after` flow, durable commit only for successful data/doc/hybrid answers, and trace entries for state load/invalidation/transition.

- [ ] **Step 1: Add failing service integration tests** for state persistence after success, refusal/clarify/error preservation, execution-derived first-month windows, source-anchor non-authority, and fact-number exclusion.
- [ ] **Step 2: Run them RED** against the current service.
- [ ] **Step 3: Implement** epoch calculation, state loading, context-patch extraction from code-owned plan/evidence/citation metadata, atomic turn/state persistence, and trace visibility.
- [ ] **Step 4: Verify** no answer prose is parsed and no session state is persisted for `session_id=None`.
- [ ] **Step 5: Commit** `feat(chat): propagate semantic session context to planner and persist transitions`.

### Task 5: Live structured context migration

**Files:**
- Modify: `starter/kbqa/live.py`
- Modify: `starter/kbqa/service.py`
- Test: `starter/tests/generalization/test_conversation_state.py`, existing live authority tests

**Interfaces:**
- Consumes: canonical Plan and semantic state context from Service.
- Produces: LiveEngine messages containing structured session context and current Plan, with no cross-turn assistant answer text; same-turn reasoning/tool messages remain unchanged.

- [ ] **Step 1: Add failing scripted-client assertions** for prior-answer poisoning, structured state/Plan context, current Plan precedence, and re-query/source-anchor behavior.
- [ ] **Step 2: Run the scripted live tests RED**.
- [ ] **Step 3: Implement** `_initial_messages(plan, state)` and update orchestration while preserving DeepSeek same-turn protocol history.
- [ ] **Step 4: Run all live/R2/R3/R4 tests plus the R5 live subset**.
- [ ] **Step 5: Commit** `feat(live): replace cross-turn transcript facts with structured session context`.

### Task 6: Regression, documentation, and release verification

**Files:**
- Modify: `README.md`
- Modify: `DEBUG_LOG.md`
- Modify: `AI_USAGE.md`
- Modify: `EVAL_REPORT.md`
- Modify: `LLM_SETUP.md`
- Modify: `DEMO.md`
- Optionally create: `eval/preflight_driver_r5.py` only if the existing driver cannot be reused without broad refactoring

- [ ] **Step 1: Run the full backend, generalization R1–R5, public mock, preflight, swap, epoch, restart, isolation, and retention matrix; record NOT RUN where environment prevents execution.**
- [ ] **Step 2: Update docs** to separate Transcript/Semantic State/Facts and document the new live context contract and Round 5 boundary.
- [ ] **Step 3: Run `git diff --check`, secret/generated-artifact scans, and the complete required verification matrix.**
- [ ] **Step 4: Commit** `docs: record Generalization R5 semantic conversation state`.
- [ ] **Step 5: Re-fetch origin, verify no unknown remote commits, and push `main` only if all required checks are green or explicitly documented as environment-blocked.**

