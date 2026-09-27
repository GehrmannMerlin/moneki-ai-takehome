# R6 Hidden-Eval Stress & DeepSeek Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and execute a dynamic R6 hidden-style stress harness that proves the R1–R5 authorities under novel data, KB, language, conversation, injection, source-conflict, and real DeepSeek inputs, then finish delivery hygiene.

**Architecture:** Keep production behavior unchanged until an independently verified attack produces a real failure. Put deterministic variant builders, independent SQL/document oracles, HTTP lifecycle helpers, and concise reporting in `starter/scripts/r6_hidden_stress.py`; exercise them through focused generalization tests and a CLI. Any confirmed production defect follows RED → generic FIX → regression, while synthetic IDs and question text remain outside production.

**Tech Stack:** Python 3.12 project venv, SQLite, FastAPI/uvicorn, pytest, standard-library HTTP client, existing `kbqa.rebuild`, existing `eval/run_eval.py`, Markdown/TXT/HTML KB fixtures.

**Spec:** `docs/superpowers/specs/2026-09-27-r6-hidden-eval-design.md`

## Global Constraints

- Never modify repository `data/` or `knowledge_base/`; every R6 variant lives under an independent temporary directory.
- Expected data values come from direct SQL/reference calculations, not `Service.chat`, `Planner`, `Answerer`, or `LiveEngine`.
- Expected KB facts and quotes come from the mutated source document, not hard-coded public KB answers.
- Synthetic IDs, values, question text, and mutation rules are allowed only in R6 tests/harness, never in production branches.
- Read `LLM_API_KEY` only from the current process environment; never print, persist, commit, or include its value in reports or traffic logs.
- Do not modify `eval/run_eval.py`, official question files, or official scoring semantics.
- Use project `starter/.venv` Python 3.12 for verification; record system-environment limitations separately.
- Keep raw traffic, temporary variants, databases, caches, and generated reports ignored/untracked; retain only concise evidence.
- Preserve visible RED → FIX → REGRESSION → DOCS history; do not squash or force-push.

## Review Focus

- Independent oracle accidentally reuses production semantics — pin with direct SQLite aggregates and source-document assertions in Tasks 1–2.
- A new document is indexed but not reachable through the live path — pin with `/api/retrieve`, `/api/chat`, `/api/trace` assertions in Task 3.
- Injection is absent from the final answer but still reaches model projection or citation — pin raw/sanitized/model/answer/citation layers in Task 4.
- Natural paraphrase changes scope, ranking, version, or hybrid classification — pin canonical Plan and response-contract assertions in Task 4.
- State from another session or dataset epoch leaks into a follow-up — pin interleaved sessions and rebuild/epoch tests in Task 5.

---

### Task 1: R6 Variant Model and Independent Data Oracle

**Files:**
- Create: `starter/scripts/r6_hidden_stress.py`
- Test: `starter/tests/generalization/test_r6_hidden_stress.py`

**Interfaces:**
- Produces `DataVariant`, `KBVariant`, `QuestionCase`, `ScenarioResult`, `R6Report` dataclasses (or equivalent typed records).
- Produces `make_data_variant(root: Path, seed: int, family: str) -> VariantInputs`.
- Produces `data_oracle(source_db: Path, start: str, end: str, ...) -> dict`.
- Consumes existing `tests/generalization/synth.py` schema conventions only as fixture knowledge; it must not call product answer code.

- [ ] **Step 1: Write failing tests for dynamically generated data and oracle independence**

  Add tests that create generated stores/products/sales with non-public IDs, mutate amount/qty/rows/dates and dirty rows, then assert the oracle returns the expected aggregate while the fixture contains no public IDs or public gold values. Include a test that monkeypatches `Service.chat`/`Planner` to raise and proves the oracle still computes.

- [ ] **Step 2: Run the focused tests and verify they fail for missing R6 interfaces**

  Run from the worktree:

  ```powershell
  & ..\..\starter\.venv\Scripts\python.exe -m pytest starter/tests/generalization/test_r6_hidden_stress.py -q
  ```

  Expected: collection or interface failures because the R6 module/tests do not yet exist.

- [ ] **Step 3: Implement data variant generation and direct-SQL oracle**

  Generate deterministic IDs from seed, create a full POS schema under the variant root, apply at least value/row/entity/time/dirty mutations, and calculate valid-row aggregates directly from the source/cleaning reference tables as appropriate. Keep expected results in the returned descriptor, not production modules.

- [ ] **Step 4: Run focused tests and verify data oracle behavior**

  Run the focused test command. Expected: PASS for deterministic seed reproducibility, different-seed divergence, direct-oracle independence, and no repository input mutation.

- [ ] **Step 5: Commit the harness foundation**

  ```powershell
  git add starter/scripts/r6_hidden_stress.py starter/tests/generalization/test_r6_hidden_stress.py
  git commit -m "test(r6): add dynamic variant and data oracle foundation"
  ```

### Task 2: KB Mutation Families and Document Oracle

**Files:**
- Modify: `starter/scripts/r6_hidden_stress.py`
- Modify: `starter/tests/generalization/test_r6_hidden_stress.py`

**Interfaces:**
- Produces `make_kb_variant(root: Path, seed: int, family: str) -> VariantInputs`.
- Produces `document_oracle(kb_root: Path, doc_id: str, expected_fact: str) -> dict`.
- Produces injection metadata containing raw payload, expected safe fact, and expected citation policy.

- [ ] **Step 1: Write failing tests for KB add/edit/delete/version/format/conflict/injection families**

  Assert content fingerprints change when indexed documents change, new generated documents use generated IDs, deletion removes old facts, v1/v2/v3 metadata is present, HTML script/style content is excluded from visible text, and raw injection payloads are recorded only as test metadata.

- [ ] **Step 2: Run the focused KB tests and verify failure**

  Run the focused test selection. Expected: missing `make_kb_variant`/oracle interfaces or failing family assertions.

- [ ] **Step 3: Implement KB builders and source-document oracle**

  Reuse only generic fixture-writing helpers; create `.md`, `.txt`, and `.html` documents with generated facts, authority metadata, version chains, source conflicts, and multiple injection payload forms. The oracle must derive expected facts/quotes from files after mutation and normalize only for validation.

- [ ] **Step 4: Run focused KB tests and verify pass**

  Expected: PASS for all family metadata, content-key changes, format handling, visible-body extraction, and quote continuity/length checks.

- [ ] **Step 5: Commit KB stress infrastructure**

  ```powershell
  git add starter/scripts/r6_hidden_stress.py starter/tests/generalization/test_r6_hidden_stress.py
  git commit -m "test(r6): add knowledge mutation and document oracle"
  ```

### Task 3: Rebuild, HTTP Lifecycle, and New-Document E2E

**Files:**
- Modify: `starter/scripts/r6_hidden_stress.py`
- Modify: `starter/tests/generalization/test_r6_hidden_stress.py`

**Interfaces:**
- Produces `ServiceHandle(env: dict, port: int)` with `get`, `post`, `trace`, and guaranteed shutdown.
- Produces `rebuild_variant(inputs: VariantInputs) -> BuildSnapshot`.
- Produces `run_http_case(handle: ServiceHandle, case: QuestionCase) -> CaseResult`.

- [ ] **Step 1: Write failing E2E tests for rebuild and new-document flow**

  Create a KB document absent before the test, rebuild in a fresh `VAR_DIR`, start uvicorn, then assert `/api/health` fingerprint/doc count, `/api/retrieve` hit, `/api/chat` fact and citation, and `/api/trace/{trace_id}` contains retrieval/citation steps. Also assert the repository source directories remain unchanged.

- [ ] **Step 2: Run the E2E test to verify failure**

  Run the focused E2E test. Expected: missing lifecycle/variant interfaces before implementation.

- [ ] **Step 3: Implement bounded rebuild/service helpers**

  Invoke `python -m kbqa.rebuild` with fresh environment values, wait for health with a bounded deadline, use a proxy-free localhost opener, capture JSON responses, and terminate/kill child processes in `finally`. Use mock mode for deterministic E2E tests unless a live case explicitly opts in.

- [ ] **Step 4: Run the E2E test and verify the full path**

  Expected: PASS with a reportable new-document path: create → rebuild → index metadata → retrieve → KnowledgeReceipt/citation → answer → trace.

- [ ] **Step 5: Commit the HTTP/E2E layer**

  ```powershell
  git add starter/scripts/r6_hidden_stress.py starter/tests/generalization/test_r6_hidden_stress.py
  git commit -m "test(r6): exercise rebuilt variants through HTTP"
  ```

### Task 4: Natural Language, Hybrid, Version, Authority, and Injection Matrix

**Files:**
- Modify: `starter/scripts/r6_hidden_stress.py`
- Modify: `starter/tests/generalization/test_r6_hidden_stress.py`

**Interfaces:**
- Produces `question_matrix(seed: int, inputs: VariantInputs) -> list[QuestionCase]`.
- Produces `assert_case_contract(response: dict, expected: ExpectedCase) -> None`.
- Produces `run_matrix(..., mode: str = "mock") -> R6Report`.

- [ ] **Step 1: Write failing tests for new paraphrases and complete contracts**

  Include metric/time/rank/negation paraphrases, data-only, doc-only, hybrid, source conflict, current/historical, why/anomaly, unknown/refusal, and every injection family. Assert `answer_type`, numbers in answer and evidence, citation IDs, contiguous safe quote, trace presence, and safe model projection where available.

- [ ] **Step 2: Run targeted tests and classify every failure**

  Run only the new matrix tests. For each failure, verify the oracle and response expectation independently, then classify it as test/harness issue, environment/provider issue, or confirmed production defect. Do not edit production code before a RED test exists.

- [ ] **Step 3: Implement matrix generation and contract assertions**

  Keep question text and synthetic facts in the harness. Use the canonical plan/trace as diagnostics, but derive pass/fail from public API contract and independent expected values. For injection, inspect raw source, sanitized output, ledger/model projection, answer, and citations separately.

- [ ] **Step 4: If and only if a production defect is confirmed, add a focused RED test**

  Commit each defect reproduction separately with a descriptive `test(r6): reproduce ...` message. The RED test must fail against the pre-fix production path and use a generic shape rather than the one synthetic ID.

- [ ] **Step 5: Implement the smallest generic production fix and regress**

  Change only the responsible production component, run the focused RED test, the R6 matrix, `tests/generalization`, and full `tests`. Commit as `fix(...): ...`; do not add case-specific branches.

- [ ] **Step 6: Commit matrix coverage/regressions**

  ```powershell
  git add starter/scripts/r6_hidden_stress.py starter/tests/generalization/test_r6_hidden_stress.py
  git commit -m "test(r6): seal mutation paraphrase and authority regressions"
  ```

### Task 5: Multi-Turn, Session Isolation, Poison, and Epoch Tests

**Files:**
- Modify: `starter/scripts/r6_hidden_stress.py`
- Modify: `starter/tests/generalization/test_r6_hidden_stress.py`

**Interfaces:**
- Produces `scenario_bank(seed: int, inputs: VariantInputs) -> list[Scenario]`.
- Produces `run_scenario(handle: ServiceHandle, scenario: Scenario) -> ScenarioResult`.
- Produces `run_interleaved_sessions(handle, sessions: dict[str, list[QuestionCase]]) -> list[CaseResult]`.

- [ ] **Step 1: Write failing tests for natural multi-turn scenarios**

  Cover only-time, only-store, only-product, only-metric, target→actual, total→by-store, data→why, current→historical, historical→current, two-window comparison, event→follow-up, topic reset, clarify→continue, refusal→continue, assistant-answer poison, A/B/C interleaving, and same-session dataset epoch replacement.

- [ ] **Step 2: Run focused multi-turn tests and verify failures are real**

  Verify response numbers against fresh receipts and inspect `session_state_before/after`, `plan`, `retrieval_scope`, and epoch traces. Reject failures caused only by stale test state or a bad expectation.

- [ ] **Step 3: Implement scenario execution and state assertions**

  Use natural Chinese utterances, fresh session IDs, and a separate `VAR_DIR` per dataset epoch. Never treat previous assistant prose as expected factual context. Assert state semantics, not raw history text.

- [ ] **Step 4: If a generic state defect is confirmed, RED → FIX → regression**

  Follow Task 4's commit and verification rules, with a reproduction that uses generated entities and windows.

- [ ] **Step 5: Commit multi-turn coverage**

  ```powershell
  git add starter/scripts/r6_hidden_stress.py starter/tests/generalization/test_r6_hidden_stress.py
  git commit -m "test(r6): cover multi-turn state and session isolation"
  ```

### Task 6: CLI Reports, Holdout, and Real DeepSeek Driver

**Files:**
- Modify: `starter/scripts/r6_hidden_stress.py`
- Create: `eval/r6_hidden_style_questions.jsonl` (only static/self-authored metadata; no secrets)
- Modify: `.gitignore`

**Interfaces:**
- CLI supports `--seed`, `--family`, `--mode mock|live`, `--repeat`, `--holdout`, `--out`, and bounded timeouts.
- Produces a concise summary with counts for data variants, KB variants, natural-language cases, paraphrases, multi-turn scenarios, injections, conflicts, holdout, latency, LLM calls, and tool calls.
- Live mode checks environment presence without printing values and uses `LLM_MODEL=deepseek-flash` unless explicitly overridden for diagnostics.

- [ ] **Step 1: Write failing tests for report schema, holdout freshness, and secret-safe output**

  Assert reports distinguish mock/live/official/self-authored/holdout, contain no key value or authorization header, record seed and commit, and reject reusing the main debug seed as final holdout metadata.

- [ ] **Step 2: Implement CLI/report/live driver**

  Add deterministic seed selection, report rendering, final-holdout generation, preflight command integration, public/extra selected runners, bounded repeat execution, and secret-safe summaries. Keep raw outputs under ignored paths only.

- [ ] **Step 3: Run mock CLI and inspect the concise report**

  Expected: all deterministic R6 mandatory categories execute, report paths are repository-relative or omitted, and no temporary directory is tracked.

- [ ] **Step 4: Run real DeepSeek preflight and selected cases**

  Use the process environment only. Record P1–P14, public live, extra live, selected R6 live, repeats, latency, and stable/intermittent classification without printing the key.

- [ ] **Step 5: Generate and run an unseen holdout**

  Use a fresh deterministic seed after all fixes. If it exposes a confirmed generic defect, fix it and generate a different holdout seed before calling holdout complete.

- [ ] **Step 6: Commit R6 driver and evidence scaffolding**

  ```powershell
  git add starter/scripts/r6_hidden_stress.py eval/r6_hidden_style_questions.jsonl .gitignore
  git commit -m "test(r6): add hidden stress reports and holdout driver"
  ```

### Task 7: Final Regression, Documentation, and Delivery Hygiene

**Files:**
- Modify: `README.md`
- Modify: `EVAL_REPORT.md`
- Modify: `LLM_SETUP.md`
- Modify: `DEMO.md`
- Modify: `AI_USAGE.md`
- Modify: `DEBUG_LOG.md` only for confirmed production defects
- Modify: relevant `docs/*.md` when stale current claims are found

**Interfaces:**
- Produces final R6 report sections required by the spec and user brief.
- Produces an auditable command/result table for tests, mock/live, preflight, holdout, path/secret/artifact audits, and Git checks.

- [ ] **Step 1: Run the final verification matrix**

  Execute R1–R5/generalization tests, R6 deterministic matrix, full pytest, swap check, public mock, public DeepSeek, extra DeepSeek, selected repeated R6 live cases, preflight, and `git diff --check`.

- [ ] **Step 2: Run documentation consistency and anti-hardcode audits**

  Scan tracked files for stale counts/SHAs/models, old architecture claims, R5 raw-history/R4 raw-KB claims, synthetic IDs in production, fixed public answer text, absolute paths, secrets, temp artifacts, and evaluator modifications. Fix only actual current inconsistencies.

- [ ] **Step 3: Update documents from actual evidence**

  Put current final status at the README top; distinguish historical results; include real R6 natural-language examples, new-document drill, trace debugging order, DeepSeek configuration method, exact current test results, remaining risks, and no secret values.

- [ ] **Step 4: Verify repository hygiene and Git history**

  Run `git status --short`, `git ls-files`, recent commit graph, tracked-file secret/path scans, `git diff --check`, and final `git fetch origin`. If `origin/main` changes unexpectedly, stop before pushing. No force push.

- [ ] **Step 5: Commit final docs/hygiene and report the branch**

  ```powershell
  git add README.md EVAL_REPORT.md LLM_SETUP.md DEMO.md AI_USAGE.md DEBUG_LOG.md docs
  git commit -m "docs: finalize R6 hidden-eval and submission evidence"
  ```

  Then report the final SHA, verification results, confirmed defects/fixes, holdout seed/result, audit results, and whether the branch is ready for the user's requested integration/push action.

## Self-Review

The plan covers every mandatory R6 P0 requirement from the approved spec:

- baseline and existing R1–R5 protection: Task 7;
- dynamic data and KB mutation: Tasks 1–2;
- independent data/KB oracles: Tasks 1–2;
- real new-document E2E and trace path: Task 3;
- paraphrase, ranking/negation, hybrid, version and authority: Task 4;
- injection raw/safe/model/final/citation checks: Task 4;
- multi-turn, isolation, poison and epoch: Task 5;
- real DeepSeek preflight/public/extra/selected repeats: Task 6;
- unseen holdout after fixes: Task 6;
- docs, paths, secrets, artifacts, anti-hardcode and Git history: Task 7.

No task requires an evaluator semantic change. Production changes are conditional and gated by an independently verified RED test. Interfaces are defined before consumers use them, and every task ends with a focused verification or commit.
