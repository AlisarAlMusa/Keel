# Keel Phase 2 — TASKS

Ordered by dependency. `[GATE]` = CI gate added the moment the thing it tests
exists. Goal: one message → router → agent → a verifier-valid plan.

---

### T0 — Schemas & client
- [ ] Pydantic schemas for `/intent`, `/grad_risk`, tool inputs, router result,
      `StudentPreference`, and the per-turn context envelope.
- [ ] `infra/model_client.py` (short timeout, fail-safe to agent).
- [ ] `infra/llm.py`: `get_llm(role ∈ {agent, lite, judge})` factory — lifespan
      singleton, pinned model/version/temperature in `Settings`, per-call structured
      logging + fallback hook.
- **Done when:** schemas import; both clients have a typed, timeout-bounded interface.

### T1 — Model server  *(critical path)*
- [ ] **Pydantic request models** for both endpoints (`IntentRequest`,
      `GradRiskRequest`); validation failure → structured `INVALID_INPUT` error,
      never a 500.
- [ ] `/intent` loads the TF-IDF + LogReg joblib (text-in → label + confidence).
- [ ] `/grad_risk` loads the winner (**HistGradBoost, joblib**; 9-vector in →
      label + prob). Both winners are joblib → no torch/ONNX in the container.
- [ ] Boot **SHA-256 vs model card**; mismatch → refuse to boot. `/health`.
- **Done when:** both endpoints respond on seed inputs; corrupt-SHA refuses boot.

### T2a — DB reseed (3 programs) + corpus reseed + ingestion  *(critical path)*
- [ ] **Migration (additive):** `Program` table + `program_id` FK on
      `ProgramRequirement`/`Student` + `Student.has_hold`/`hold_reason`
      (spec §9.1).
- [ ] **DB reseed both tenants** (`SEED_RESET=1` cascades the delete). 44 courses,
      3 programs (BSCS/BSDS/BSCHEM), new prereqs + coreqs, per-tenant sections +
      students with all edge cases. Data is fixed in **spec §9.2–§9.6**. Insert
      order: Tenant → Program → Course → Prereq/Coreq → Section →
      ProgramRequirement → Student → Transcript → holds.
- [ ] DB courses + prereqs + programs **identical across tenants**; differ only on
      policies, sections, students.
- [ ] **Corpus (MinIO):** upload `{slug}/catalog.md` (per-tenant copy, enriched, all
      44 courses) + `{slug}/policies.md` (per-tenant, incl. Program/Major-Change);
      delete stale `{slug}/catalog.txt`; `keel-mlflow` untouched. Keep `SEED_RESET=1`
      idempotency.
- [ ] Policy docs differ on advisory numbers (Northane vs Summit); the 18-credit
      cap matches the DB in both. Recommended-prep worded as advisory.
- [ ] Ingestion is **one reusable tenant-scoped service** `load → clean → chunk →
      embed → upsert(pgvector + FTS)`: course = 1 chunk (overlap 0); policy = 1 chunk
      per heading. Stable `chunk_id` → re-ingest **upserts changed + deletes removed**
      chunks (no orphans). Two triggers: seed (now) + admin upload (Day 6 — upload →
      MinIO → RQ job → ingest under tenant_id → bust tenant caches → outbox notify).
      Build it callable now.
- [ ] Embed via Cohere `embed-multilingual-v3.0` (1024-dim); pgvector column
      `vector(1024)`. Async batched, timeout + retry.
- [ ] Metadata: `tenant_id`, `type`, `code`|`doc`+`section`, `source`, `lang`,
      `chunk_id`.
- **Done when:** both tenants reseeded (3 programs); CS→DS audit ≈3 yr while
      CS→Chem is 3+ yr; the at-risk student scores at-risk; ≥1 full section/tenant;
      cross-tenant query returns nothing; reseed idempotent (no dupes).

### T2b — Hybrid retrieval + rerank  *(critical path)*
- [ ] `rag_search(query, tenant_id, k)`: redact query → dense (top 20) + FTS
      (top 20) → RRF fuse (k=60) → Cohere rerank top ~12 → top 5. Tenant filter in
      the query itself.
- [ ] Degradation: rerank down → fused order; embed down → FTS-only.
- [ ] Typed `args_schema` + structured `ToolError`; clients are lifespan
      singletons via `Depends()`; all knobs in `Settings` (`extra="forbid"`).
- [ ] Grounding: prereq/eligibility facts from the engine, not prose.
- [ ] Test on **5 hand-written queries**; record chunk rule + numbers (k's, RRF
      60, embed dim) in docs/DECISIONS.md.
- **Done when:** 5 queries return correct chunks; cross-tenant query returns
      nothing.

### T3 — Guardrails
- [ ] `infra/guardrails.py`: input rails (injection + cross-tenant refusal),
      output `redact()` applied to response **and** logger **and** tracer.
- [ ] Hardcoded platform rails; PII patterns (keys, email, national IDs).
- [ ] `[GATE]` Red-team: injection + cross-tenant probes all refused.
- [ ] `[GATE]` PII: fake key in chat → never unredacted in logs/traces.
- **Done when:** both gates green and enforced.

### T4 — Router  *(complete and final from this phase)*
- [ ] `proba = intent(text); route, conf = argmax, max`. If
      `conf >= FALLBACK_THRESHOLD` → `FLOWS[route]` (trust classifier). Else →
      `agent.run` (agent decides the intent + handles). Classifier error → agent.
- [ ] Wire all **15 labels** now. 5 real flows; 10 stubs (one fixed
      "not available yet" string per label, no LLM). Stubs replaced phase by phase
      — no router changes needed after today:
      ```
      advise       → RAG advise flow
      audit        → engine audit flow
      plan         → propose_plan flow
      out_of_scope → LLM-direct lite (gemini-2.0-flash-lite, 50-token cap)
      chitchat     → LLM-direct lite (gemini-2.0-flash-lite, 50-token cap)
      whatif / predict / register / waitlist / plans_manage /
      my_info / grad_apply / major_change / petition / escalate → stub
      ```
- [ ] `GEMINI_LITE_MODEL=gemini-2.0-flash-lite` in `.env.example`.
- [ ] Load `FALLBACK_THRESHOLD` from `router_config.json` (≈0.512). F1 gate
      threshold stays separate in `eval_thresholds.yaml`.
- [ ] `[GATE]` Intent classifier **macro-F1** on the held-out set.
- **Done when:** confident labels hit their flows; low-confidence / multi-step /
      ambiguous go to the agent; chitchat/out_of_scope return natural lite-LLM
      responses; Day-4+ labels return stubs; F1 gate green.

### T5 — Agent + first tools  *(critical path)*
- [ ] **Single bounded** LangGraph agent (no multi-agent): allowlist, **6**-iter +
      token cap, Pydantic tool inputs, distinct LLM node + tool node. Tools
      namespaced (planning / advising / action).
- [ ] **Memory, all in `lifespan` / `app.state` / `Depends` (never at import):**
      Redis session memory (last **N**, 30-min sliding TTL) · **Postgres
      `AsyncPostgresSaver` checkpointer** (durable from the start) · Postgres
      approval record (`request_queue` + audit + outbox).
- [ ] **Migration:** `StudentPreference` table (closed schema, RLS); run
      `AsyncPostgresSaver.setup()` for the checkpointer tables.
- [ ] **Per-turn context envelope:** current message + last N turns (bounded) +
      structured preferences + engine student snapshot + active plan ref. Policy via
      `rag_search` only. Redacted at every egress; preferences never relax a constraint.
- [ ] **Snapshot cache:** cache the engine snapshot + active-plan ref per student in
      Redis (`snapshot:{tenant}:{student}`, short TTL). Invalidation hooks on the
      write tools land in Phase 3 (enrollment, plan activate/swap, hold, grade, major
      change); Phase 2 is read-only so the cache is trivially correct now.
- [ ] **Answer-or-tool:** direct answer only for meta/chitchat; academic questions
      must use a grounding tool.
- [ ] Tools: `audit_degree`, `rag_search`, `propose_plan` — each typed
      `args_schema` + structured `ToolError` (`{error, retryable, category}`); never
      silent; no 4xx retry.
- [ ] `propose_plan` (feasibility-only): LLM propose → verifier → repair (≤ **3**)
      → greedy fallback → clean fail. **No risk/ranking.**
- [ ] **Cost circuit breaker** (per-tenant hard cap) + **idempotent on resume**
      (idempotency keys, so a resumed run never double-writes).
- [ ] **Tests:** node tests (mock LLM → assert `Command`) · trajectory snapshots
      (node visit order) · deterministic LLM mocks (record/replay). Pin model
      version; every prompt carries a `prompt_version`.
- **Done when:** the agent returns a verifier-valid plan from a NL request; node +
      trajectory tests green.

### T6 — End-to-end + traces
- [ ] One NL message flows router → agent → `propose_plan` → valid plan.
- [ ] `request_id`/`trace_id` minted at the FastAPI entry and propagated; every
      LLM/tool/retrieval call is a span with model+version, prompt_version, tokens,
      latency, outcome. Log `prompt_hash` + `content_hash`, never full text; no PII
      in traces.
- **Done when:** the end-to-end smoke passes and the trace is clean.

---

## Build order (dependency)
`T0 → T1 → T2 → T3 → T4 → T5 → T6`  (T1/T2/T3 can overlap once T0 lands)

## Phase milestone
One plain-language message returns a verifier-valid plan, with the intent-F1,
red-team, and PII gates all green.

## Never cut from this phase
The tenant filter, the redaction-at-every-egress, the bounded agent, and the
three CI gates. The router/agent niceties can be trimmed; these are the floor.

## Claude Code notes
- Record deferred ideas in `docs/STRETCH.md`: the NeMo guardrails sidecar and admin
  catalog editing. In-process guardrails + admin **policy** upload ship now.
- Log the router design in `docs/DECISIONS.md`: argmax = route, max_prob = trust gate,
  agent = fallback; `FALLBACK_THRESHOLD ≈ 0.512` in `router_config.json`
  (≥90% accuracy on covered); keep 15 labels for routing + analytics + priming.