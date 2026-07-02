# Keel Phase 2 — PLAN

How we build what spec.md defines. Five pieces, the decisions behind them,
testing, and risks.

---

## 1. Architecture & placement

```
model-server/            # SEPARATE service: onnxruntime + joblib, no torch
services/
  router.py              # intent score -> direct handler | agent
  agent/                 # bounded LangGraph agent + tool wiring
  handlers/              # direct handlers: rag_advise, degree_audit
  rag/                   # ingestion + hybrid retrieval + rerank
infra/
  guardrails.py          # input rails + output redaction (one shared fn)
  model_client.py        # HTTP client to the model server
domain/engine/           # Phase 1 — reused as-is (verifier, audit, planner)
```

The model server is its own container so the backend stays torch-free and the
model artifacts can be promoted/verified independently.

## 2. Model server

- Load artifacts at boot; **verify SHA-256 vs the model card**; mismatch →
  refuse to boot. MLflow registry gives the artifact path.
- `/intent`: load the **TF-IDF + LogReg** joblib bundle (vectorizer +
  classifier together); raw text in → label + probability out.
- `/grad_risk`: load the winning grad-risk artifact (**HistGradientBoosting,
  sklearn joblib**); **9-vector in** → label + `prob_at_risk` out. No feature
  logic here. Both winners are joblib, so the container needs no torch/ONNX.
- **Two defense layers at each endpoint:** (1) Pydantic validates shape/bounds
  before inference (`text` non-empty ≤2000 chars; `features` exactly 9 floats);
  validation failure → structured `INVALID_INPUT` error, never a 500. (2) The
  inference itself wrapped in try/except → structured `INFERENCE_ERROR`. The
  caller always gets a typed response, never a raw exception.
- Backend `model_client.py` has a short timeout; on failure the router falls
  back to the agent and logs a trace warning (fail safe).

## 3. RAG

- **Reseed the DB and the MinIO corpus this phase.** Both tenants (`northane`,
  `summit`) carry the same **44-course catalog across 3 programs** (BSCS, BSDS,
  BSCHEM), but the corpus is **duplicated per tenant** (`corpus/<slug>/catalog.md`,
  identical content at seed, independently editable); **policies are per-tenant**
  (`corpus/<slug>/policies.md`) and deliberately differ on advisory numbers (now
  including a Program/Major-Change section) so cross-tenant leaks are visible. DB
  courses, prereqs, and programs are identical; tenants differ only on policies,
  sections, and students. The 18-credit cap (DB-backed) is
  identical in both. Seed uploads `{slug}/catalog.md` + `{slug}/policies.md` and
  deletes the stale `{slug}/catalog.txt`. The seed-data blueprint is spec §9.
- **Ingestion (one reusable tenant-scoped service, idempotent):** `load → clean →
  chunk → embed → upsert(pgvector + FTS)`. Stable `chunk_id` so re-ingest upserts
  changed chunks and **deletes removed ones** (no orphan vectors). Async batched
  embed with timeout + retry. **Two triggers:** the seed (this phase) and **admin
  doc upload (Day 6)** — upload → MinIO → enqueue an RQ job → worker ingests under
  the tenant_id → bust that tenant's caches → notify via outbox. Build the service
  callable now so Day 6 only wires the trigger.
- **Chunking:** course = 1 chunk (overlap 0); policy = 1 chunk per heading (whole
  if <~400 tokens). No parent-child — rerank + tiny corpus make it unjustified.
- **Embedding:** `cohere embed-multilingual-v3.0`, 1024-dim (no-torch,
  multilingual for G2). pgvector column `vector(1024)`.
- **Retrieval:** redact query → dense (top 20) + sparse FTS (top 20) →
  RRF fuse (k=60) → Cohere rerank top ~12 → top 5 to the LLM. Degradation:
  rerank down → fused; embed down → FTS-only.
- **Tenant filter** in the repository query itself — no retrieval path without it.
- **Grounding:** prose answers what/why; prereq/credit facts come from the engine,
  never quoted from chunk prose. "Recommended prep" stays advisory in wording.
- **Standards:** all knobs in `Settings` (`extra="forbid"`); Cohere/embed/pgvector
  clients are lifespan singletons via `Depends()`; `rag_search` returns a
  structured `ToolError`; `structlog` + spans.
- **Decisions to record in docs/DECISIONS.md:** chunk rule + the defended numbers,
  embed model + dim, RRF k=60, dense/sparse/rerank k's, why no parent-child.
  **Test on 5 hand-written queries** before wiring into the agent.

## 4. Guardrails

- Input rails run first: injection patterns + cross-tenant probes → refuse with a
  safe message.
- Output redaction wraps the **logger and tracer**, not just the response, so a
  leak cannot slip into a span. One `redact()` function, applied at every egress.
- Patterns: API-key shapes, email, national-ID formats. Platform rails are
  constants in code; tenant config cannot disable them.

## 5. Router

- **Argmax routes, max_prob gates trust.** Confident (≥ `FALLBACK_THRESHOLD`) →
  trust the classifier's label → its flow. Not confident → the **bounded agent**
  decides the intent and handles the turn. There is no third path.
- **Multi-step / ambiguous land on the agent by design** — they score low
  classifier confidence, so they fall through to the agent naturally. That is the
  agent's job: the low-confidence fallback that decides intent and orchestrates.
- **All 15 labels mapped from Day 2.** 5 real Phase-2 flows; 10 stubs (one fixed
  string per label). A confident-but-unbuilt label returns its stub. Stubs are
  replaced phase by phase — no router surgery ever needed.
- **Model routing:** `GEMINI_MODEL` (`gemini-2.5-flash`) for reasoning-heavy flows
  and the agent; `GEMINI_LITE_MODEL` (`gemini-2.0-flash-lite`) for `chitchat` +
  `out_of_scope` only — LLM-direct, 50-token cap. Canned string if the lite call
  fails.
- **Threshold:** `FALLBACK_THRESHOLD ≈ 0.512` in `router_config.json` (computed;
  ≥90% accuracy on covered subset). Separate from F1 gate in `eval_thresholds.yaml`.
- **Keep the 15 labels** — per-intent analytics + agent priming + macro-F1
  deliverable.

## 6. Agent

- **Single bounded agent — not multi-agent.** One LangGraph agent behind the
  trained router (the router is the deterministic supervisor). Allowlist, max **6**
  iterations + token budget, every tool input Pydantic-validated. Tools namespaced
  (planning / advising / action) inside the one agent.
- **Memory — three stores, built in `lifespan` / `app.state` / `Depends`, never at
  import:**
  - Session/chat memory → **Redis**, last **N** turns, 30-min sliding TTL.
  - Graph checkpointer → **Postgres `AsyncPostgresSaver`** from the start (durable
    across restart + long HIL pause; Redis TTL would lose an interrupted run).
  - Approval / pending-write → **Postgres** always (`request_queue` + audit + outbox).
- **Per-turn context envelope:** current message + last N turns (bounded) +
  structured preferences (Postgres) + engine student snapshot (program, standing,
  term, holds, GPA, completed-credit summary) + active plan reference. Policy stays
  in RAG (retrieved on demand). Redacted at every egress; preferences never relax a
  constraint.
- **LLM client:** thin `get_llm(role)` factory (lifespan singleton via `Depends`);
  models / temps / **pinned versions** in `Settings`; per-call structured logging +
  fallback hook. Not a multi-provider SDK.
- **Answer-or-tool:** the LLM node answers directly only for meta/chitchat; any
  academic question must use a grounding tool.
- `propose_plan` wires the Phase-1 verifier into the repair loop: LLM proposes →
  verifier → repair (≤ **3** attempts) → greedy fallback → clean fail. **No risk
  scoring/ranking yet.**
- **Cost circuit breaker** (per-tenant hard cap) + **error taxonomy** (categorized
  `ToolError`, never silent, no 4xx retry) + **idempotent on resume** (idempotency
  keys, so a resumed run never double-writes).

## 7. Decisions & tradeoffs

| Decision | Choice | Tradeoff |
|----------|--------|----------|
| Router signal | argmax = route if conf ≥ threshold; else agent decides intent | one clean fallback (the agent); no third path |
| All 15 labels mapped Day 2 | real flows (5) + stubs (10) | stubs replaced per phase; zero router changes later |
| chitchat/out_of_scope model | gemini-2.0-flash-lite, 50-token cap, LLM-direct | varied UX at low cost; canned fallback if lite call fails |
| Threshold | computed ≈0.512 in `router_config.json` (≥90% acc on covered) | tunable without retrain; separate from F1 gate |
| Keep 15 labels | yes (analytics + priming + eval) | overkill if only used for routing — so we use them more |
| Intent preproc | bundled in joblib artifact (text-in) | server owns NLP preprocessing |
| grad-risk input | 9-vector from backend (no features in server) | one extra backend step; keeps no-second-copy |
| Model-server down | route to agent | costlier turn, but safe |
| Guardrails | in-process for MVP | NeMo sidecar deferred → `docs/STRETCH.md` |
| Admin doc upload scope | policies only now; catalog editing → `docs/STRETCH.md` | policy is pure RAG (safe); catalog edits ripple through the engine (DB rows, DAG, audit, eligibility, saved-plan validation, replanning, caches) |
| Agent caps | 6 iters / 3 repairs / 30-min TTL | bounds cost + latency |
| propose_plan | feasibility-only this phase | risk + ranking land in Phase 3 |
| Graph checkpointer | Postgres `AsyncPostgresSaver` from the start | durable across restart + long HIL pause; not Redis-then-switch |
| Memory split | session = Redis · checkpointer = Postgres · approval = Postgres | each store matched to its durability need |
| LLM client | thin `get_llm(role)` factory, pinned in `Settings` | pinning + logging + fallback; not a multi-provider SDK |
| Agent topology | single bounded agent (no multi-agent) | router is the deterministic supervisor; sub-agents add cost not correctness |
| Preferences | structured Postgres table, injected as context | deterministic; not open-ended memory (PII surface) |
| Classifier input | current message only | context-dependent follow-ups fall to the agent by low confidence |
| Student snapshot cache | Redis per student, invalidate on write | fewer audit recomputes per turn; correctness via explicit invalidation |
| Ingestion | one reusable tenant-scoped service, two triggers (seed + admin upload via RQ worker) | no second pipeline; live grounding when an admin edits a doc |
| Majors | 3 first-class programs (CS/DS/Chem) via new `Program` table | small additive migration; enables real what-if / major-switch contrast |
| Program difference source | degree audit filters `ProgramRequirement`; DAG stays catalog-wide | one DAG per tenant; no per-program DAG to maintain |
| Cross-tenant differentiation | sections + students + policies differ; DB courses + prereqs + DAG identical (corpus duplicated per tenant) | clean isolation test; half the authoring/validation surface |
| Chem program | 13 new courses in a deep chain | authoring cost; chain must stay acyclic (Kahn verifies) |
| DB scope this phase | reseed DB (was MinIO-only) | needed for switch/prediction/waitlist demos; recorded reversal |

## 8. Testing strategy

- **Intent macro-F1 gate** on the held-out set (threshold in yaml).
- **Red-team gate:** injection + cross-tenant probes all refused.
- **PII test:** fake key in chat → never in logs/traces.
- **RAG smoke:** 5 hand-written queries return the right chunks; a cross-tenant
  query returns nothing.
- **End-to-end smoke:** a NL message → router → agent → a verifier-valid plan.
- **Agent node tests:** mock the LLM, assert on the returned `Command` (state
  update + goto) — the cheap, high-value layer.
- **Trajectory snapshots:** did the agent visit the expected nodes in the expected
  order (e.g. LLM → tool → verifier → repair)?
- **Tool unit tests + agent tool-selection golden set** (right tool, or correctly
  none). Deterministic LLM mocks (record/replay) so tests never hit the network.
- **Versioning:** pin the Gemini model version (never "latest"); every prompt
  carries a `prompt_version`.
- **Tracing fields:** a `request_id`/`trace_id` minted at the FastAPI entry and
  propagated; per-call spans log model+version, prompt_version, tokens, latency,
  outcome; log `prompt_hash` + `content_hash`, never full prompt/message text.
  Traces feed the eval flywheel (golden sets grown from real traces).
- **Boot test:** corrupt a SHA → server refuses to boot.

## 9. Risks & mitigations

| Risk | Mitigation |
|------|-----------|
| RAG grounding leak (invented prereq) | facts from engine only; test grounding on the 5 queries |
| pgvector tenant leak | filter enforced in the repo query; cross-tenant test returns nothing |
| Agent runaway (cost/latency) | hard loop + token cap, allowlist |
| Injection bypass | red-team gate + DB tenant filter as the real boundary |
| Misroute (wrong capability) | threshold computed (≥90% covered-acc); writes still need approval, so misroute can't execute |
| Model-server coupling | fail-safe to agent; SHA refuse-to-boot only on integrity error |
| Cyclic / unsatisfiable chem chain | author the chain newer→older; Kahn cycle check refuses to load; cover in the planner golden set |
| At-risk student doesn't score at-risk | verify N3's 9-vector against `/grad_risk` post-seed; tune the transcript until it lands at-risk |
| Golden tests break on catalog growth | keep the planner golden set on northane CS; DS/Chem are additive, CS unchanged |
| Long HIL pause loses agent state | Postgres `AsyncPostgresSaver` survives restart + TTL; approval row lives in Postgres |
| Cost runaway across a session/tenant | per-tenant hard cost cap (circuit breaker) on top of the per-turn token/iter caps |
| Ungrounded direct answer (invented facts) | system prompt limits direct answers to meta/chitchat; academic questions require a grounding tool; tool-selection golden set catches misses |
| Stale snapshot / orphan vectors | snapshot cache invalidated on every snapshot-affecting write (short TTL backstop); ingestion deletes removed `chunk_id`s and busts tenant caches on doc change |