# Keel Phase 2 — Model Server · RAG · Guardrails · Router + Agent — SPEC

> Goal of this phase: make the AI layer real and safe, and get **one
> plain-language message to flow end-to-end**: message → router → agent →
> `propose_plan` → a verifier-valid plan returned.

Builds on Phase 1 (the deterministic engine + the two trained models). Consumes
both; adds no new feasibility logic.

Principle held throughout: **Intelligence proposes. Deterministic systems verify.
Models predict. Execution requires approval.**

---

## 1. Scope

**In (this phase):**
Model server (serves intent + grad-risk) · RAG pipeline (pgvector, hybrid +
rerank, DAG-grounded) · in-process guardrails · classifier router · bounded
LangGraph agent with the first 3 tools · the first end-to-end loop.

**Out (later phases):**
- Risk **injection + ranking** inside `propose_plan`, and the `predict_risk`
  tool + mitigation (Phase 3 / Day 4).
- Registration / enrollment write + outbox (Day 4).
- What-if, save/load/activate, swap, replanning (Day 4+).
- Advising suite C2–C4, institutional requests (Day 5), frontends (Day 6).

`propose_plan` here is **feasibility-only**. The grad-risk model is **served and
tested** but not yet called by any tool.

---

## 2. Invariants

1. The **model server is pure inference** — it runs artifacts on inputs. No
   domain/feature logic lives there.
2. **Tenant filter is mandatory** on every retrieval and every DB read — not
   optional, enforced at the repository layer. Isolation is the grade.
3. **Redaction runs at every egress** — response, log line, and trace span —
   through one shared function. A leak must not reach a trace.
4. The **agent is bounded** — tool allowlist, loop cap, token budget, all tool
   inputs validated. Never an open loop.
5. **Facts come from the engine, prose comes from RAG.** The LLM may not state a
   prerequisite or eligibility fact that didn't come from the engine.
6. **Fail safe, not fail open.** When in doubt the router sends the message to
   the agent; the agent never writes (no write tools exist yet anyway).

---

## 3. Contracts

### 3.1 Model server (separate lean service)

`onnxruntime` + `joblib`, **no torch**. Two endpoints:

```
POST /intent     { text }                  -> { intent, confidence, all_scores }
POST /grad_risk  { features: [9 floats] }  -> { label, label_name, prob_at_risk }
GET  /health     -> { status, model_shas }
```

- **Input validation — first layer of defense (before inference):**
  Every endpoint validates its payload with a **Pydantic model** before touching
  the artifact. Validation failure → structured error response
  `{ error, code: "INVALID_INPUT" }`, never a 500, never a raw exception.

  ```python
  class IntentRequest(BaseModel):
      text: str = Field(min_length=1, max_length=2000)

  class GradRiskRequest(BaseModel):
      features: list[float] = Field(min_length=9, max_length=9)
      # caller guarantees FEATURE_ORDER; server enforces count only
  ```

  "Pure inference" means **no domain logic** (no `compute_features`, no business
  rules) — not "skip validation." Validation is infrastructure, not domain.

- **`/intent`** takes **raw text**. The production model is **TF-IDF + Logistic
  Regression** (joblib); the TF-IDF vectorizer is the preprocessing and is
  **bundled inside the artifact**. Returns the predicted label of the ~15-label
  set + a calibrated-ish probability.
- **`/grad_risk`** takes the **pre-computed 9-vector** in `FEATURE_ORDER`. The
  backend builds the vector via `grad_risk.compute_features` (the no-second-copy
  rule); the server only runs the model. Winner = **HistGradientBoosting
  (sklearn), joblib** — no DL/ONNX. Used by a tool in Phase 3 — stood up and
  tested now.

Both production models are **sklearn/joblib** (intent = TF-IDF+LR, grad-risk =
HistGradBoost), so the server needs **no torch and no ONNX runtime** — just
FastAPI + joblib + sklearn.
- **Boot integrity:** on startup, load each artifact and verify its **SHA-256
  against the model card**. Mismatch → **refuse to boot** (fail closed). The
  MLflow registry is the source of truth for the artifact path.
- **Degrade behavior:** if the server is unreachable, the backend routes the
  message to the **agent** (fail safe) and surfaces a trace warning.

### 3.2 Router

The intent model returns a **full probability vector** over the 15 labels.
`argmax` is the candidate route; `max_prob` decides whether to trust it.

```
proba       = model_server.intent(text)
route, conf = argmax(proba), max(proba)
if conf >= FALLBACK_THRESHOLD:
    return FLOWS[route](text, ctx)     # confident -> trust the classifier's label
return agent.run(text, ctx)            # not confident -> agent decides intent + handles
# classifier error/unreachable -> agent (fail safe)
```

- **Confident (conf ≥ threshold):** trust the label, dispatch straight to its
  flow, skip the agent's intent-decision step. A direct flow is *not* "no LLM" —
  it may still call the LLM/verify-repair internally; "direct" means we skipped
  re-deciding the intent.
- **Not confident (conf < threshold):** hand to the **bounded agent**, which
  decides the intent and handles the turn. Multi-step and ambiguous messages
  naturally score low confidence, so they land here **by design** — that is
  exactly what the agent is the fallback for. There is no third path.
- **Classifier input is the CURRENT message only — no history.** A context-
  dependent follow-up ("then do one for me") is ambiguous alone, so it scores low
  confidence and falls to the agent, which *has* the conversation history and
  resolves it. Feeding history to the classifier would let it wrongly inherit a
  previous turn's intent. General/meta messages ("what can you do?") either
  classify confidently as `chitchat`/`out_of_scope` (→ lite LLM) or score low and
  reach the agent, which answers them directly without tools.
- **`FLOWS` is a total map** — all 15 labels are mapped from Day 2, so the
  confident path never hits an unmapped route. Unbuilt capabilities map to a
  **stub** (one fixed "not available yet" string, no LLM). Whether reached via the
  confident path or the agent's decision, an unbuilt intent returns its stub.
  Stubs are replaced phase by phase — **no router changes after this phase.**

  | label | flow | built |
  |-------|------|-------|
  | `advise` | RAG advise | Phase 2 |
  | `audit` | engine audit | Phase 2 |
  | `plan` | propose_plan | Phase 2 |
  | `out_of_scope` | LLM-direct (lite model) | Phase 2 |
  | `chitchat` | LLM-direct (lite model) | Phase 2 |
  | `whatif` | what-if flow | Phase 3 (stub now) |
  | `predict` | predict_risk flow | Phase 3 (stub now) |
  | `register` | registration flow | Phase 3 (stub now) |
  | `waitlist` | waitlist flow | Phase 3 (stub now) |
  | `plans_manage` | save/load/activate | Phase 3 (stub now) |
  | `my_info` | student-record lookup | Phase 3 (stub now) |
  | `grad_apply` | graduation application | Phase 4 (stub now) |
  | `major_change` | major-change flow | Phase 4 (stub now) |
  | `petition` | petition flow | Phase 4 (stub now) |
  | `escalate` | escalation/handoff | Phase 4 (stub now) |

- **`chitchat` + `out_of_scope` — LLM-direct flows** (not the agent, not a canned
  string): one LLM call, tight system prompt, hard 50-token cap. Natural varied
  responses without an agent turn.

  ```
  system: "You are Keel, a friendly academic co-pilot.
  For greetings/chitchat: respond warmly in 1-2 sentences and pivot to
  what you can help with (planning, registration, advising, degree progress).
  For out-of-scope: acknowledge briefly and redirect to those four areas.
  Never invent academic information. Be concise."
  ```

- **Model routing:**
  - `GEMINI_MODEL` (`gemini-2.5-flash`) → all reasoning-heavy flows (plan, advise,
    audit, RAG, repair, mitigation, drafting) and the agent.
  - `GEMINI_LITE_MODEL` (`gemini-2.0-flash-lite`) → `chitchat` + `out_of_scope`
    only. Same API key, separate env var. If the lite call fails → canned fixed
    string (hard fallback, never silently to the agent).

- **`FALLBACK_THRESHOLD` is already computed** — `router_config.json`, value
  **≈ 0.512** (not in the artifact; change without retraining), the lowest
  threshold reaching **≥ 90% accuracy on the covered subset** in a
  coverage-vs-accuracy sweep. Across 15 classes (baseline ~0.067) a max_prob ≥
  0.51 means the top class holds the majority of the mass — meaningfully peaked.
- A misroute is **bounded by the action pattern**: any write still needs explicit
  approval, so a wrong route can never execute a write — worst case is a
  recoverable wrong answer. (Per-route thresholds for write capabilities are a
  possible later refinement; one global threshold is fine for MVP.)
- The 15 labels also feed per-intent analytics (admin cost dashboard) and agent
  priming, so they earn their place.

### 3.3 RAG pipeline (ingestion → retrieval)

**Catalog architecture — same source, two layers:** the DB table holds *facts*
(prereqs, credits, offering term, capacity); the RAG corpus holds *rich prose*
(descriptions, advising context, policy text). **Course codes in the corpus must
equal the DB codes** — one source, two representations.

#### 3.3.1 Corpus content (DB reseeded this phase — see §9 for the data blueprint)

The seed builds the DB with **44 courses across three programs** — Computer
Science (`BSCS`), Data Science (`BSDS`), and Chemistry (`BSCHEM`) — prereqs, and
two tenants (`northane` + `summit`). The DB courses, prereqs, and programs are
**identical across tenants**; the RAG corpus is **duplicated per tenant** (identical
content at seed, independently editable thereafter). Tenants differ on policies,
sections, and students. The old one-line MinIO catalog only restated DB facts and
made RAG redundant, so we replace it with enriched prose and reseed the DB for the
major-switch and edge-case demos:

- **Rich course descriptions — one `catalog.md` per tenant** (duplicated: identical
  content at seed, independently editable thereafter): description · topics · skills
  · typical work · **recommended preparation (advisory — NOT the hard prereqs)** ·
  career relevance · workload note. Source: `corpus/<slug>/catalog.md`.
- **Policy / advising documents — per tenant** (`corpus/northane/policies.md`,
  `corpus/summit/policies.md`): max-credits & overload · withdrawal · repeat ·
  petition · graduation · probation · waitlist. **Deliberately differentiated**
  on advisory numbers (withdrawal week, probation GPA, repeat/waitlist limits,
  overload GPA) so cross-tenant leakage is *visible* — a Summit policy query must
  return Summit's numbers, never Northane's.

Rules:
- **DB-backed numbers must match the DB.** The only hard number here is the
  **18-credit cap** (the seed's `max_credits_per_term=18`) — identical in both
  policy docs. Advisory numbers (not engine-enforced) may differ per tenant.
- Hard facts (prereqs, credits, term, capacity) stay authoritative **in the DB**;
  "recommended preparation" prose is advisory and worded as such.
- **Upload two objects per tenant** — `{slug}/catalog.md` (per-tenant copy, tenant
  name in the header) and `{slug}/policies.md` — replacing the single
  `catalog.txt`, so the ingester applies the right chunk rule + `type` metadata
  per file.
- **Cross-tenant isolation test targets a policy query** (where the numbers
  differ), since course descriptions are identical content across tenants.

#### 3.3.2 Chunking (every number defended)

- **Course = 1 chunk** (~150–300 tokens), **overlap 0**. A course description is a
  self-contained semantic unit well under the embedder's limit; splitting it would
  fragment description/skills/career across chunks and hurt retrieval. Overlap is
  for continuous prose — courses are discrete records, so 0. The defended "number"
  is *1 course = 1 chunk*, a semantic boundary, not an arbitrary token count.
- **Policy = 1 chunk per heading** (e.g. one chunk for "Overload", one for
  "Withdrawal"). A whole policy doc shorter than **~400 tokens** stays one chunk.
  Overlap **0** (clean heading boundaries) — only ~15% (~30–50 tokens) if clauses
  run together.
- **No parent-child.** With rerank + a ~30–40-chunk corpus, retrieve-top-k +
  rerank already surfaces both clauses for cross-section questions; parent storage
  + linking would be machinery with no measurable gain. (Justify in DECISIONS.md.)
- *Course sections (CS101-A/B) are DB scheduling rows, not RAG content — the
  corpus has one description per course code.*

#### 3.3.3 Embedding & store

- **Embeddings via API to stay no-torch:** `cohere embed-multilingual-v3.0`,
  **1024-dim** (multilingual covers the G2 stretch). pgvector column =
  `vector(1024)` — the dimension must match the model output exactly.
- **Vector store:** pgvector (server-based, tenant-tagged). **Sparse:** Postgres
  full-text (`tsvector` / `ts_rank`) — same DB, no extra dependency, no model.
- **Metadata persisted per chunk** (§13 RAG rule): `tenant_id` (isolation),
  `type` (`course`|`policy`), `code` (courses) | `doc`+`section` (policies),
  `source`, `lang`, `chunk_id` (stable hash → idempotent re-ingest).

#### 3.3.4 Ingestion pipeline (reusable service — seed + admin upload)

Source: the per-tenant MinIO objects — `{slug}/catalog.md` and `{slug}/policies.md`.
Pipeline: `load → clean → chunk → embed → upsert(pgvector + FTS index)`. Re-runnable:
stable `chunk_id = hash(tenant_id|source|section)` so re-ingest **upserts changed
chunks and deletes removed `chunk_id`s** (no orphan vectors), never duplicates.
Embedding calls are **async (`httpx.AsyncClient`), batched, with timeout +
`tenacity` retry** on transient errors.

**One service, two triggers.** The same tenant-scoped function backs both:
- **Seed (this phase):** ingests both tenants at reset time.
- **Admin doc upload (Day 6 admin console):** the admin uploads a policy doc →
  store in MinIO → **enqueue an RQ ingestion job** (not inline — embedding is slow,
  so the upload returns fast) → worker chunks (policy = 1 chunk per heading) →
  embeds → upserts under that `tenant_id` → **invalidates that tenant's
  retrieval/catalog caches** → notifies via the outbox.

**Scope decision (trade-off): admin upload is policies-only.** A policy doc is pure
RAG advisory with **no engine impact**, so live ingest is safe and cheap. **Catalog
editing is deferred to `STRETCH.md`** — a course catalog is part of the
**deterministic engine**, not just RAG content, so one edit would have to atomically
touch DB course rows, the prerequisite DAG (+ cycle check), the degree audit,
planning/registration eligibility, saved-plan validation + automatic replanning, and
cache invalidation, then re-validate every saved plan. Too much surface for now. The
ingestion service is still built callable, so the catalog trigger can be added later
without a rewrite.

#### 3.3.5 Retrieval (per query)

```
rag_search(query, tenant_id, k) -> [ { chunk, source, score, type, code|doc } ]
```

1. **Redact the query** (PII) before any external call — egress rail.
2. **Dense:** embed query → pgvector cosine, top **20**, tenant-filtered.
3. **Sparse:** Postgres FTS `ts_rank`, top **20**, tenant-filtered.
4. **Fuse:** Reciprocal Rank Fusion, constant **k=60** (the RRF paper default),
   dedupe → candidate set.
5. **Rerank:** `cohere rerank-multilingual-v3.0` on the top **~12** → return top
   **5** to the LLM. Few, high-precision chunks keep the prompt grounded.
6. **Degradation chain:** rerank down → fused order; embed API down → FTS-only.
   Never a hard fail (matches the fail-safe pattern).

- **Tenant filter is mandatory** at the repository layer (pgvector + FTS both
  filtered by `tenant_id`). A query for tenant A must never return tenant B's
  chunks — verified by the cross-tenant CI probe.
- **`rag_search` is a tool:** typed Pydantic `args_schema` (`query`, `tenant_id`,
  `k`), a clear docstring (the LLM reads it), and a **structured `ToolError`**
  (`{error, retryable}`) on failure — never an exception that crashes the agent.
- **Grounding rule:** prose answers "what a course covers / why it helps"; the
  engine answers "what it requires." Prereq/credit facts come from the DAG/audit,
  never from chunk prose. The LLM cites sources from chunk metadata.
- **No leakage** (§13): no eval ground-truth answers in the index; tenant
  isolation prevents cross-tenant leakage. Query rewriting is available but **off
  by default** for MVP — the corpus is small and queries are usually specific.

#### 3.3.6 Config & singletons (engineering standards)

- All RAG knobs live in the `Settings` class (`pydantic-settings`,
  `extra="forbid"`): `EMBED_MODEL`, `EMBED_DIM`, `RERANK_MODEL`, `DENSE_K`,
  `SPARSE_K`, `RRF_K`, `RERANK_TOP_N`, `COHERE_API_KEY`. No `os.getenv` scattered.
- The Cohere client, embed client, and pgvector pool are **lifespan singletons on
  `app.state`, exposed via `Depends()`** — never constructed inside a request.
- Structured logging via `structlog` (no `print`); every retrieval step is an
  OTel/LangSmith span.

#### 3.3.7 Seed redesign (DB reseed this phase — data blueprint in §9)

This phase **reseeds the DB**, not MinIO-only. Reason: the major-switch contrast
(A3/C4/F2) and the prediction/waitlist/recovery edge cases need data the original
single-program seed cannot produce.

- **Three first-class programs per tenant** via a new `Program` table: `BSCS`
  (unchanged), `BSDS` (reuses CS/MATH foundations plus a real Data Science core),
  `BSCHEM` (13 new chemistry courses in a deep prereq chain). Catalog grows 24 → 44.
- **DAG stays per-tenant and catalog-wide** (program-agnostic — confirmed in
  `graph.py`). The per-program difference is the degree audit over
  `ProgramRequirement`, not a per-program DAG. The chem chain joins the one shared
  DAG; Kahn's cycle check rejects a malformed chain at load.
- **Tenants differ only on policies, sections, and students;** catalog, prereqs,
  and programs are identical. The isolation test still targets policy queries.
- **Edge cases seeded:** at-risk student, probation student, near-graduation
  student, held student, the major-switch candidate, and ≥1 full section per
  tenant. Holds need a minimal schema field (`Student.has_hold` + `hold_reason`).
  The at-risk student must verify as at-risk on `/grad_risk` — tune if it does not.
- **Corpus:** `{slug}/catalog.md` (per-tenant copy, enriched, all 44 courses) +
  `{slug}/policies.md` (per-tenant, now including a Program/Major-Change section).
  Delete the stale `{slug}/catalog.txt`; the `keel-mlflow` bucket is never touched.

The full course / prereq / program / section / student data is fixed in **§9**.

### 3.4 Guardrails (in-process: `infra/guardrails.py`)

- **Input rails** (before the agent/handler): prompt-injection detection
  (pattern/heuristic + refusal) and cross-tenant probe refusal.
- **Output rails** (at every egress — response, log, trace): **PII redaction**
  of API keys, email patterns, national IDs, via one shared redaction function
  reused everywhere.
- **Platform rails are hardcoded** and cannot be weakened by tenant config.
- The real isolation boundary is the **DB tenant filter** (RLS + pgvector tag);
  the heuristics are defense-in-depth.

### 3.5 Agent + tools

- **Single bounded agent — not multi-agent.** One LangGraph agent behind the
  trained router; the router *is* the deterministic supervisor. A supervisor /
  writer-planner-advisor split is rejected: turns are short, sequential, and share
  one student context, so sub-agents add orchestration, latency, cost, and eval
  surface with **no correctness gain** (correctness lives in the engine, not the
  agent topology). Tools are **namespaced** (planning / advising / action) inside
  the one agent for readability.
- **Bounded:** tool **allowlist**, **loop cap** (max **6** iterations) + **token
  budget**, every tool input **Pydantic-validated**. The graph has a **distinct LLM
  node and tool node** (an LLM-only graph means the tools are decorative); tools
  registered at one line (`ToolNode(tools=[...])`).
- **Answer-or-tool:** the LLM node either answers directly or calls a tool. A
  **direct answer is allowed only for meta/chitchat**; any course / prereq / policy /
  plan question MUST go through a grounding tool (`rag_search` / `audit_degree`) —
  no ungrounded academic answers. The tool-selection golden set tests this ("right
  tool, or correctly none").

- **Memory model — three stores, each matched to its durability need. All built in
  `lifespan`, on `app.state`, via `Depends` — never at import time:**
  - **Session/chat memory → Redis**, last **N** turns, **30-min sliding TTL**
    (`Settings`). Short-term context only; losing it after idle is acceptable.
  - **Graph checkpointer → Postgres (`AsyncPostgresSaver`).** Durable from the
    start — survives a server restart and a long human-in-the-loop pause (a reviewer
    may approve hours/days later). Not Redis: a TTL eviction would lose an
    interrupted run's state.
  - **Approval / pending-write record → Postgres**, always (the institutional
    `request_queue` + `audit_log` + outbox). Audited business state, never Redis.
- **Per-turn context envelope** — assembled deterministically and passed to the
  agent:
  1. current message;
  2. last **N** turns (Redis, bounded — cap N or use a rolling summary to control
     tokens);
  3. **structured student preferences** (Postgres, §3.6);
  4. an **engine-computed student snapshot** (program, standing, current term,
     holds, GPA, completed-credit summary — a summary, not the full transcript);
  5. the **active plan reference** (id/name), so "do one for me" / "swap X" resolves.
  Policy text is **not** injected — it is retrieved via `rag_search` on demand. The
  envelope is **redacted at every egress** (response, logs, traces); a preference
  can **never** relax a deterministic constraint.
- **Snapshot cache (avoid recomputing the audit every turn):** the engine snapshot
  (4) and active-plan reference (5) are cached per student in Redis
  (`snapshot:{tenant}:{student}`, short TTL). **Invalidated** on any write that
  changes them — enrollment, plan save/activate/swap, hold change, grade/transcript
  change, major change. Phase 2 is read-only, so the cache is trivially correct now;
  the invalidation hooks land on the write tools in Phase 3. The cache holds
  engine-computed values only, so grounding holds — provided invalidation is correct
  (stale standing/credits would mis-ground the agent).

- **LLM client — thin provider layer, not a multi-provider SDK.** `get_llm(role)`
  (`role ∈ {agent, lite, judge}`) is a **lifespan singleton via `Depends`**; model
  names, temperatures, and **pinned versions** live in `Settings` (never "latest").
  Every call is wrapped with structured logging (request_id, model+version,
  prompt_version, tokens_in/out, latency_ms, outcome) and a fallback hook.

- **Each tool has:** a clear name, a docstring (what the LLM reads to choose it), a
  typed Pydantic `args_schema`, and a **structured `ToolError`**
  (`{error, retryable, category}`) return — a tool failure must not crash the agent.
  **Error taxonomy:** transient → retry w/ backoff; rate-limit → slow retry;
  validation/4xx → fail fast (no retry); content-policy → fallback. **Never silent** —
  every error / fallback / degradation is logged.
- **First 3 tools:** `audit_degree`, `rag_search`, `propose_plan`.
- **`propose_plan` (feasibility-only) loop:** LLM proposes → engine **verifier**
  validates → LLM **repairs** from the structured violations → re-validate, up to
  **3** attempts → **greedy fallback planner** (engine) → clean "no valid plan"
  message. **No risk, no ranking** (Phase 3).
- **Cost circuit breaker:** the 6-iter + token caps bound a single turn; a
  **per-tenant hard cost cap** bounds the day (ties to the admin cost dashboard) so
  a stuck loop cannot run up a bill.
- **Idempotent on resume:** when the Postgres checkpointer resumes a run after a
  restart, tools must be idempotent (idempotency keys on writes) so re-execution
  never double-writes.
- **Constraint/preference extraction** from free text → typed Pydantic model
  (validate at the boundary; trust types inside). External calls carry a timeout +
  `tenacity` retry; agent and tools log via `structlog`.

### 3.6 Student preferences (structured, persisted)

- **Fixed schema, not open-ended memory.** A `StudentPreference` row (tenant-scoped,
  RLS) with a **closed set** of product fields: `response_style` (brief|detailed),
  `language` (en|fr|ar — feeds G2), `difficulty_preference`, `career_interest`
  (feeds E1/E2), notification settings. No free-form "remember anything" — that is a
  PII surface with no product value.
- **Persisted in Postgres** (durable, queryable), **injected as deterministic
  context** in the per-turn envelope — never a tool the LLM must call, and never
  able to relax a constraint.

---

## 4. Deterministic / model / LLM split (this phase)

- **Deterministic:** router threshold logic, the verifier inside `propose_plan`,
  tenant filtering, guardrail rules, retrieval mechanics.
- **Model:** intent classification (grad-risk served, not yet used by a tool).
- **LLM:** plan proposal + repair, RAG answer generation from retrieved context,
  conversational phrasing.

This is the first request where all three layers meet.

---

## 5. CI gates added this phase

- **Intent classifier macro-F1** on the held-out set (threshold in
  `eval_thresholds.yaml`).
- **Guardrails red-team:** injection probes and cross-tenant probes must all be
  refused.
- **PII redaction:** a fake key pasted into chat must never appear unredacted in
  logs or traces.

(Grad-risk F1 gate and agent tool-selection gate come in later phases.)

---

## 6. Acceptance criteria

- A confident `plan` message returns a **verifier-valid plan** through its
  direct flow; a low-confidence/ambiguous message reaches the same result via the
  agent fallback.
- A confident course-info question and degree-audit question are answered by
  their **direct flows** (route skipped the agent's intent step), per
  `router_config.json`.
- Cross-tenant retrieval returns nothing; an injected key never appears in
  logs/traces; injection probes are refused.
- Model server refuses to boot on a SHA-256 mismatch.
- All three new CI gates green.

---

## 7. Engineering standards applied (binding)

From the standards guide — only the rules that shape this phase:

- **Async I/O everywhere:** `httpx.AsyncClient` for Cohere/embed/model-server, never
  `requests`; async DB (pgvector + FTS). No blocking call in a request path.
- **Config:** one `Settings` class (`pydantic-settings`, `extra="forbid"`) — model
  names, `EMBED_DIM`, `DENSE_K`/`SPARSE_K`/`RRF_K`/`RERANK_TOP_N`, thresholds,
  TTLs, API keys. No scattered `os.getenv`. `lru_cache` on `get_settings`.
- **Singletons in lifespan:** Cohere client, embed client, pgvector pool,
  model-server client, agent — created once in `lifespan`, on `app.state`, exposed
  via `Depends()`. Never constructed in a route.
- **Every external call:** timeout + `tenacity` retry (transient only, max
  attempts) + **structured error return** (`ToolError`), plus a degradation path
  (rerank→fused, embed→FTS, model-server→agent).
- **Validate at boundaries:** Pydantic on every endpoint, tool `args_schema`, and
  LLM structured outputs; trust types inside.
- **Logging/tracing:** `structlog` (no `print`); OTel/LangSmith span on every
  LLM/tool/retrieval call; redaction wraps logger + tracer + response.
- **Tooling/tests:** `uv`, `ruff`; test schemas, tools (mock LLM + external), and
  one end-to-end happy path; the three CI gates run on every push.

---

## 8. Instructions for Claude Code

> In the **repo-root `PLAN.md`**, under a section `## To add if time` (create the
> section if it does not exist), add: *"NeMo Guardrails sidecar — replace/augment
> the in-process rails with a topical + injection sidecar (stretch)."* MVP uses
> the in-process rails in `infra/guardrails.py`.

> Add `GEMINI_LITE_MODEL=gemini-2.0-flash-lite` to `.env.example`. This is the
> model for `chitchat` + `out_of_scope` flows only — same Gemini API key,
> separate env var. The main `GEMINI_MODEL=gemini-2.5-flash` stays for all
> reasoning-heavy flows. Log this model-routing decision in `DECISIONS.md`.

> **Keep `.env` and `.env.example` in sync.** Every var added to `.env.example`
> this phase must also be written to `.env`. New vars: `GEMINI_LITE_MODEL`,
> `COHERE_API_KEY` (embeddings + rerank), `EMBED_MODEL=embed-multilingual-v3.0`,
> `EMBED_DIM=1024`, `RERANK_MODEL=rerank-multilingual-v3.0`, `DENSE_K=20`,
> `SPARSE_K=20`, `RRF_K=60`, `RERANK_TOP_N=5`, `SESSION_TTL_SECONDS=1800`. Treat
> `COHERE_API_KEY` as a secret (Vault in compose, like the other secrets).

> Log in `DECISIONS.md`: router uses argmax = route when conf ≥
> `FALLBACK_THRESHOLD` (≈0.512, in `router_config.json`); below threshold the
> **agent decides the intent and handles the turn** (multi-step/ambiguous land
> here by low confidence — the agent is the single fallback). All 15 labels
> mapped from Day 2 (unbuilt = stub, replaced phase by phase); `chitchat`/
> `out_of_scope` use LLM-direct lite model (`gemini-2.0-flash-lite`).
---

## 9. Seed data blueprint (this phase)

> The deterministic source for the DB reseed + migration. Catalog, prereqs, and
> programs are **identical across both tenants**; tenants differ only on policies,
> sections, and students. Rich prose for every course lives in `catalog.md`.

### 9.1 Migration (additive — existing tables unchanged)
- `Program(id, tenant_id → Tenant[cascade], code, name, degree_type,
  total_credits_required, description, unique(tenant_id, code))`.
- `program_id` FK on `ProgramRequirement` and `Student` (keep `program_code` for
  readability).
- `Student.has_hold: bool = False`, `Student.hold_reason: str | null`.
- `StudentPreference(id, tenant_id → Tenant[cascade], student_id → Student[cascade],
  response_style, language, difficulty_preference, career_interest, notify_*,
  unique(tenant_id, student_id))` — closed schema, RLS-scoped.
- Postgres tables for LangGraph's `AsyncPostgresSaver` checkpointer (created by its
  `.setup()`), so agent runs are durable from the start.

### 9.2 New courses (added to the existing 24 — codes must equal `catalog.md`)
- **Data Science:** DS210(3) · DS301(3) · DS310(3) · DS320(3) · DS340(3) ·
  DS350(3) · DS401(4).
- **Chemistry:** CHEM101(4) · CHEM101L(1) · CHEM102(4) · CHEM201(4) · CHEM201L(1) ·
  CHEM202(4) · CHEM301(3) · CHEM310(3) · CHEM311(3) · CHEM320(3) · CHEM330(3) ·
  CHEM410(3) · CHEM420(4).

### 9.3 New prerequisite + corequisite edges (course ← requires)
- **DS:** DS210←CS101 · DS301←MATH210,DS210 · DS310←DS210 · DS320←CS301,DS210 ·
  DS340←CS330 · DS350←CS330,MATH201 · DS401←DS301,DS320.
- **Chem:** CHEM102←CHEM101 · CHEM201←CHEM102 · CHEM202←CHEM201 · CHEM301←CHEM102 ·
  CHEM310←CHEM102,MATH102,PHYS201 · CHEM311←CHEM310 · CHEM320←CHEM201 ·
  CHEM330←CHEM202 · CHEM410←CHEM301 · CHEM420←CHEM301,CHEM310.
- **Coreqs:** CHEM101L↔CHEM101 · CHEM201L↔CHEM201 (plus the existing
  PHYS201L↔PHYS201).
- All edges point newer→older; the graph stays acyclic (Kahn verifies at load).

### 9.4 Programs & requirement groups (per tenant, identical)
- **BSCS** — CS Core: CS101,CS102,CS201,CS202,CS301,CS302,CS320 · Capstone: CS420 ·
  CS Electives (9 cr from): CS310,CS330,CS340,CS350,CS401,CS402,CS410 · Math:
  MATH101,MATH102,MATH201,MATH210 · Science: PHYS201,PHYS201L · Gen Ed:
  ENG101,ECON101.
- **BSDS** — DS Core: DS210,DS301,DS320,DS401,CS301 · Methods: CS330,DS310 ·
  DS Electives (6 cr from): DS340,DS350,CS350 · Math & Stats:
  MATH101,MATH102,MATH201,MATH210 · Programming: CS101,CS102 · Gen Ed:
  ENG101,ECON101.
- **BSCHEM** — Chem Core: CHEM101,CHEM102,CHEM201,CHEM202,CHEM301,CHEM310 · Labs:
  CHEM101L,CHEM201L · Chem Electives (9 cr from): CHEM311,CHEM320,CHEM330,CHEM410 ·
  Capstone: CHEM420 · Supporting: MATH101,MATH102,PHYS201,PHYS201L · Gen Ed:
  ENG101,ECON101.

### 9.5 Per-tenant sections (differences only — all else open, capacity 30)
- **Northane:** CS301 full (`enrolled == capacity`) for the waitlist demo; CS330,
  DS210, CHEM101 offered **fall**.
- **Summit:** DS210 full for the waitlist demo; CS330, DS210, CHEM101 offered
  **spring**.

### 9.6 Per-tenant students (edge cases)
**Northane**
- **N1 — BSCS happy-path sophomore.** CS101(3.7) MATH101(3.3) ENG101(4.0)
  CS102(3.0). The clean, reliable end-to-end demo.
- **N2 — BSCS major-switch junior.** CS101(3.5) CS102(3.3) CS201(3.0) CS210(3.3)
  MATH101(3.7) MATH102(3.3) MATH201(3.0) MATH210(3.3) ENG101(3.7). Drives the
  contrast: CS→DS finishable in ≈3 years, CS→Chem 3+ years.
- **N3 — BSCS at-risk.** CS101(2.0) · CS102 **FAILED**(0.7) · MATH101 **FAILED**(1.0)
  · CS210(1.7) ENG101(2.3). 2 failures, GPA ≈1.6, slow progress → at-risk + C3.

**Summit**
- **S1 — BSCS probation.** CS101(1.7) CS102(1.3) MATH101(2.0) ENG101(2.3). GPA ≈1.8,
  below Summit's 2.25 good-standing line → probation (ties to the policy difference).
- **S2 — BSCS near-graduation senior.** Completed CS101,CS102,CS201,CS202,CS210,
  CS301,CS302,CS320,MATH101,MATH102,MATH201,MATH210,PHYS201,PHYS201L,ENG101,
  ECON101,CS310,CS330; CS420 in progress → graduation-application demo (F1).
- **S3 — BSCS held.** CS101(3.0) CS102(2.7) MATH101(3.0); `has_hold=True`,
  `hold_reason="unpaid balance"` → verifier hold-block.

### 9.7 Acceptance (seed-specific)
Both tenants reseeded with 3 programs; the audit makes CS→DS finishable in ≈3 years
and CS→Chem 3+ years; N3 scores at-risk on `/grad_risk`; ≥1 full section per tenant;
cross-tenant retrieval returns nothing; reseed is idempotent under `SEED_RESET=1`.