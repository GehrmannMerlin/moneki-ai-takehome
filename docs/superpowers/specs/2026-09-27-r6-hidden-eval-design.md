# R6 Hidden-Eval Stress & DeepSeek Validation Design

**Date:** 2026-09-27  
**Status:** Approved for implementation  
**Scope:** Final Generalization Round R6, including the requested R7-lite delivery hygiene checks

## Goal

Prove that the R1–R5 architecture continues to obey its authority boundaries when the data, knowledge base, natural-language phrasing, conversation order, source conflicts, and model provider are changed together. The proof must be based on dynamically generated variants and independent expected-value calculations, not on replaying public questions or hard-coded public answers.

R6 is complete only when the repository has:

1. a reusable hidden-style stress harness;
2. independent data and knowledge-base oracles;
3. real HTTP coverage for `/api/retrieve`, `/api/chat`, and `/api/trace/{trace_id}`;
4. at least one new-document live drill;
5. paraphrase, hybrid, version, conflict, injection, multi-turn, session-isolation, and unknown-evidence coverage;
6. real `deepseek-flash` validation using process environment configuration only;
7. an unseen final holdout run after fixes;
8. current, mutually consistent delivery documentation and repository hygiene evidence.

## Current Baseline

The baseline was collected before implementation:

| Check | Result |
|---|---|
| Branch | `main` |
| HEAD | `ea3ea23d66754b1a0529491039c67f5df9b80ebd` |
| `origin/main` | `ea3ea23d66754b1a0529491039c67f5df9b80ebd` |
| Ahead/behind | `0/0` |
| Working tree | clean |
| Python 3.12 | `3.12.6`, project `.venv` available |
| `tests/generalization` | `155 passed` |
| full `tests` | `389 passed` |
| `scripts/swap_check.py` | passed |
| public mock | `100/100`; wrapper had a Windows console encoding exception after scoring |

The 3.11 system interpreter lacks project test dependencies. The final verification uses `starter/.venv` and records the interpreter explicitly.

## Attack-Surface Map

| Hidden-eval risk | Current authority/component | R6 probe |
|---|---|---|
| Data replacement and stale artifacts | `core.cleaning`, `core.manifest`, `rebuild.py`, `Service.rebuild` | new SQLite values, rows, entities, dates, and epoch change |
| KB replacement and cache invalidation | `core.loader`, `core.index`, `core.manifest`, `Service.rebuild` | add/modify/delete/rename/format changes and fingerprints |
| Planner paraphrase | `planner.py`, `core.intent`, `entities.py`, `timeparse.py` | metric, time, rank, negation, entity, and hybrid paraphrases |
| RAG trust/version | `core.retriever`, `authority.py`, `core.sanitize.py` | current/historical versions, source conflict, injection |
| Citation provenance | `citations.py`, `DocFacts`, `FactLedger`, trace steps | source doc, quote continuity, dropped instructions |
| Tool scope | `PlanToolPolicy`, `LiveEngine`, `Service.run_tool` | model-proposed conflicting store/window/version scope |
| Finalisation | `LiveEngine._finalise`, `Answerer`, `FactLedger` | model prose versus receipt numbers, unknown evidence |
| Conversation semantics | `ConversationState`, `FollowUps`, `SessionStore`, `context_epoch` | natural follow-ups, reset, epoch invalidation, LRU |
| DeepSeek tool calling | `llm.py`, `live.py`, `config.py` | real provider, retry/timeout, repeated high-risk cases |
| Session isolation | `SessionStore` and service `session_id` boundary | interleaved A/B/C sessions |
| Error handling | `server.py`, `Service._answer`, `LLMError` | malformed/unknown model behavior, refusal/clarify, timeout-safe response |

## Architecture

### Harness boundary

`starter/scripts/r6_hidden_stress.py` will own the R6 test mechanics. It will not modify the repository's `data/` or `knowledge_base/`. Each variant receives a fresh temporary root containing:

```text
variant/
  data/pos.db
  knowledge_base/
  var/
  report.json
```

The harness will use a deterministic seed and a variant descriptor so a failure can be reproduced without copying a live report or secret. A fresh process environment will set `DATA_DIR`, `KB_DIR`, and `VAR_DIR`. Live LLM variables are opt-in and are read from the parent process only.

The harness will reuse the existing rebuild command and the existing real uvicorn service boundary. A service handle will wait for `/api/health`, issue bounded HTTP calls, collect the returned `trace_id`, fetch the trace, and always terminate the child process.

### Test layers

The harness will expose small, independently testable helpers for:

- synthetic POS schema/data generation;
- synthetic KB document generation and mutation;
- independent SQL/reference metric oracle;
- source-document/quote oracle;
- service lifecycle and HTTP calls;
- response-contract assertions;
- multi-turn scenario execution;
- summary rendering.

`starter/tests/generalization/test_r6_hidden_stress.py` will exercise those helpers and a bounded set of end-to-end mock-mode variants. A CLI invocation will support the larger matrix, selected live cases, repeat counts, and final holdout seed.

### Production-change rule

No production code will be changed merely because a capability is mentioned in the R6 brief. For every production defect:

```text
new attack
→ independently verified expected value
→ RED test
→ root-cause diagnosis
→ smallest generic production fix
→ focused regression
→ full generalization regression
```

Synthetic identifiers and question text remain in tests/harness only. Production code must not branch on R6-specific IDs, values, or question strings.

## Mutation Families

The main matrix will include at least:

1. **Data values:** dynamically generated amount, quantity, refund-like rows, payment mix, and date-window changes.
2. **Data rows:** addition/deletion, duplicate rows, invalid foreign keys, zero/negative values, invalid quantity, and alternate date forms.
3. **Entities:** new store, product, category, district, and aliases using generated identifiers.
4. **KB facts:** edit an existing fact, add an unknown document, delete a document, and rename a document.
5. **KB versions:** generated v1 → v2 → v3 chain with current and historical queries.
6. **KB formats:** Markdown, text, and HTML visible-text handling.
7. **Authority conflicts:** database versus estimate/meeting note, current notice versus old reference, and formal policy versus opinion.
8. **Prompt injection:** multiple Chinese, English, and mixed-language payload families covering instruction override, fake system messages, forced fixed answer, citation disabling, tool abuse, and destructive requests.
9. **Natural language:** metric synonyms, temporal paraphrases, rank paraphrases, negation/contrast, entity aliases, unknown questions, and hybrid questions.
10. **Conversation:** semantic follow-up changes, topic reset, current/history transitions, interleaved sessions, stale assistant-answer poison, and dataset epoch change.

## Independent Oracles

### Data oracle

The data oracle reads the mutated SQLite source directly. It will calculate, without importing or calling `Planner`, `Answerer`, `LiveEngine`, or `Service.chat`:

- net revenue;
- refund amount where represented by the schema/mutation;
- order count;
- average order value;
- quantity;
- daily totals;
- payment mix;
- product/store/category ranking;
- two-window comparison.

The oracle will apply the same documented cleaning expectations only where the test specifically verifies cleaning. It will otherwise distinguish raw-row facts from cleaned, valid-row facts so an oracle bug cannot silently drive a production change.

### Knowledge oracle

The knowledge oracle reads the mutated source document after mutation. It will derive the expected document ID, visible body fact, effective date/status, and contiguous quote. It will enforce the evaluator's quote normalization and length limit by reusing the existing evaluator normalization only for verification, never for production expected-value generation.

Injection checks will separately inspect:

1. raw source contains the attack;
2. sanitized/model projection omits the attack instruction;
3. the final answer does not obey it;
4. citation and quote do not use the attack as factual authority.

## Natural-Language and Contract Matrix

Every authored case will record its capability, why a hidden evaluator may use it, how the oracle derives the answer, and the likely failure layer. The matrix will cover:

- pure data;
- pure document;
- hybrid data + document;
- comparison;
- ranking and non-ranking contrast;
- current and historical version;
- source conflict;
- why/anomaly;
- unknown/refusal;
- safety/injection;
- multi-turn follow-up.

Hybrid cases will assert the complete response contract: `answer_type`, data evidence, numbers, citations, quote provenance, and trace—not merely a substring in `answer`.

## DeepSeek Validation

The real-provider run will use:

```text
LLM_BASE_URL=(process environment)
LLM_API_KEY=(process environment)
LLM_MODEL=deepseek-flash
```

The key value will never be printed, written to a file, placed in a prompt fixture, or committed. The run will include:

- `eval/llm_gateway.py preflight` P1–P14;
- official public questions;
- self-authored extra questions;
- selected R6 hybrid, historical, conflict, injection, unknown, and multi-turn cases;
- repeated high-risk cases to distinguish stable pass, intermittent result, and stable failure;
- latency, model, call count, and tool-call summaries without raw credential-bearing traffic.

Raw traffic and temporary variant directories remain ignored and are not delivery evidence.

## Holdout

After the main attack matrix and any fixes, the harness will create a new deterministic seed that has not been used while debugging. The final holdout will include at least:

- one data question;
- one new-document question;
- one hybrid question;
- one multi-turn scenario;
- one version/source-authority case.

If a holdout exposes a confirmed generic defect, the fix will be followed by a newly generated holdout rather than repeatedly tuning the original one.

## Delivery Hygiene

The final pass will audit tracked files and relevant recent commits for:

- absolute machine paths;
- real credentials, bearer headers, `.env`, or key-like material;
- temporary databases, caches, `VAR_DIR`, raw traffic, and large reports;
- accidental evaluator changes;
- stale README/EVAL_REPORT/LLM_SETUP/DEMO/AI_USAGE claims;
- visible RED → FIX → REGRESSION → DOCS history;
- `git diff --check` and final remote synchronization.

R7 is not opened as a separate architecture round. Its requested documentation, path, secret, artifact, and Git checks are part of this R6 delivery.

## Success Criteria

The final report will distinguish official public results from self-authored and hidden-style synthetic results, state the exact environment and commit, list confirmed defects and fixes (or explicitly state none), and conclude with:

```text
R1 — COMPLETE
R2 — COMPLETE
R3 — COMPLETE
R4 — COMPLETE
R5 — COMPLETE
R6 — COMPLETE / BLOCKED
Standalone Round 7 — CANCELLED / MERGED INTO R6 DELIVERY HYGIENE
```

The project may claim submission-ready only when all mandatory P0 checks in the user-provided R6 brief have been executed or an explicit external limitation is recorded.
