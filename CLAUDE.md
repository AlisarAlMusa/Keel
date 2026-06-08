# CLAUDE.md — Keel

> Read this fully at the start of every session. It is the contract for how this codebase is built.
> For deeper detail: ARCH.md (architecture), PLAN.md (what to build next), SPEC.md (component contracts), ENGINEERING_RULES.md (tactical standards / review checklist), constitution.md (principles), DECISIONS.md (why we chose things).

---

## 1. What Keel is

Keel is a **multi-tenant SaaS** a university deploys for its students. One conversational AI agent, embedded in the registration portal, helps a student **plan** courses, **predict** whether a plan is wise, **advise** in plain language, and **safely execute** registration and institutional paperwork — but only after the student approves.

Two React surfaces over one FastAPI backend:
- **Student widget** — embedded chat (signed per-widget token).
- **Admin console** — registrar grounds/configures the agent and works the institutional-request queue.

---

## 2. The one rule that governs everything

**Intelligence proposes. The deterministic engine verifies. Models predict. Execution requires approval.**

This is not a slogan — it is an architectural boundary you must never cross:

- The **LLM never decides feasibility.** It proposes plans, extracts intent, ranks, explains, drafts text. It can be wrong, and that's fine — the engine catches it.
- The **deterministic engine owns all hard constraints** (prerequisites, time conflicts, capacity, credit caps, offering term, corequisites, holds, eligibility). Its verdict is final and non-negotiable.
- The **prediction models** (graduation-risk: trained; workload: deterministic) score whether a *valid* plan is *advisable*. They never gate feasibility.
- **No write happens without explicit student/registrar approval.** Every write is transactional + emits an outbox event + writes an audit row.

**If you are ever tempted to let the LLM check a prerequisite, decide eligibility, or skip the verifier — stop. That is a bug, not a shortcut.**

---

## 3. Hard rules (do not violate)

1. **Every plan shown to a student must have passed the verifier first.** No exceptions. The LLM proposes → engine validates → repair loop → only valid plans surface.
2. **Every side-effecting write goes through the action pattern:** validate → require approval → single DB transaction (write + outbox event) → audit row. Never write to a registration/request table outside this pattern.
3. **Tenant isolation is enforced at the database (RLS) AND the repository layer.** Every query is tenant-scoped. pgvector retrieval is tenant-filtered. Never trust application code alone for isolation.
4. **No `torch` in any runtime container.** Models are trained offline (Colab), exported to ONNX/joblib, served by the lean `model-server`. Containers use `onnxruntime` only.
5. **Guardrails run on every inbound message and every outbound response.** Injection refusal, cross-tenant refusal, PII redaction. Platform rails are hardcoded — never weakenable by tenant config.
6. **Secrets come from Vault at startup.** The app refuses to boot if Vault is unreachable. Never hardcode a secret, never read one from a literal in code.
7. **The engine has no LLM calls inside it.** It is pure, deterministic, unit-testable Python. Keep it that way.
8. **Edge-case tests are written before (or alongside) engine code, and require human approval (you should ask me).** Do not consider engine code done until the human's reviwed edge-case suite passes.

---

## 4. Architecture in one screen

```
React Admin                         React Student Widget (signed token)
   |  catalog/rules/request-queue       |  chat · plan view · risk badges · approval
   \________________ FastAPI backend (RLS · layered · async) ________________/
                              |
   inbound -> GUARDRAILS -> CLASSIFIER ROUTER --easy--> deterministic workflow
              (in-process)   (trained intent model)  \--hard--> bounded LangGraph AGENT
                              |
   AGENT tools: audit_degree · propose_plan · simulate_whatif · predict_risk
                search_sections · save_plan · swap_course · execute_enrollment
                apply_graduation · request_major_change · submit_petition · escalate
                              |
   +------- DETERMINISTIC CORE (no LLM): prereq DAG · conflict checker --------+
   |  degree audit · plan validator · workload index · greedy fallback planner |
   +--------------------------------------------------------------------------+
                              |
   model-server (ONNX/joblib): intent classifier · graduation-risk model
   pgvector RAG (hybrid+rerank, DAG-grounded) · Postgres+RLS · Redis · RQ worker
   Vault · MinIO · MLflow · outbox publisher · OTel/LangSmith tracing
```

Full version: `ARCH.md`.

---

## 5. Layered backend — where code goes

Strict dependency direction: `api → services → repositories → domain`. `infra` is wired in via dependency injection. **Never import upward.**

```
src/keel/
  api/            # FastAPI routers, request/response schemas, auth deps. Thin. No business logic.
  services/       # Orchestration / use-cases. Calls repositories + domain + infra. Where the agent lives.
  repositories/   # All DB access. Every method tenant-scoped. Returns domain objects, not ORM rows.
  domain/         # Pure business types + the deterministic ENGINE. No I/O, no framework imports.
  infra/          # DB, Redis, Vault, MinIO, model-server client, guardrails, tracing, outbox, email.
  workers/        # RQ tasks: capacity sync, waitlist, outbox publisher, alerts, auto-replan.
  agent/          # LangGraph graph, tool definitions, prompts (versioned).
tests/
  unit/           # Engine edge cases (the important ones), pure-function tests.
  eval/           # Golden sets + thresholds (planner, classifier, risk, RAG, red-team).
  integration/    # End-to-end through the API.
migrations/       # Alembic.
frontend/
  admin/          # React admin console.
  widget/         # React student widget + loader.
model-server/     # Separate lean service (onnxruntime only).
```

**The engine lives in `domain/engine/`. It is the crown jewel. Pure, deterministic, no imports from `infra` or `services`.**

---

## 6. Tech stack (and why — do not substitute without a DECISIONS.md entry)

- **uv** — package management (replaces pip/pip-tools/virtualenv). `pyproject.toml` + `uv.lock`. Never use `pip install`. In Dockerfiles, use `uv sync --frozen --no-dev`.
- **FastAPI + Pydantic** — async API, typed boundaries, structured LLM/tool I/O.
- **PostgreSQL + Row-Level Security + Alembic** — one DB; RLS enforces isolation. Hosts Plan entity, request queue, outbox, audit log.
- **pgvector** — RAG in the same DB, tenant-filtered by construction.
- **LangGraph** — the bounded agent (hard turns only). Loop cap + token budget enforced.
- **Redis + RQ** — session memory/cache + background worker.
- **model-server (onnxruntime/joblib)** — lean model serving, no torch.
- **MLflow** — experiment tracking + model registry (staging→production, rollback). Backed by MinIO + Postgres.
- **Vault** — secrets at startup. **MinIO** — model artifacts, eval reports.
- **OpenTelemetry / LangSmith** — tracing every LLM/tool/retrieval call.
- **NeMo-style in-process guardrails** — sidecar is a documented stretch, not MVP.
- **Docker Compose** — whole stack, one command. **GitHub Actions** — CI with eval gates.

---

## 7. Coding conventions

- **`uv` for all dependency management.** `uv add <pkg>` to add, `uv sync` to install, `uv run <cmd>` to execute. Never `pip install`. The lockfile (`uv.lock`) is committed. Dockerfiles use `uv sync --frozen --no-dev`.
- **Python 3.12, async throughout.** No sync DB calls in request paths.
- **Type everything.** `mypy` is in CI. Pydantic models at every boundary.
- **Dependency injection via FastAPI `Depends`.** Singletons (DB pool, Redis, model-server client, Vault) created in lifespan, not per-request.
- **Repositories return domain objects**, never raw ORM/SQLAlchemy rows, never dicts.
- **No business logic in routers.** Routers parse, authorize, delegate to a service, serialize.
- **Tool inputs are Pydantic models.** The agent never passes free-form dicts to tools.
- **Prompts are versioned** (in `agent/prompts/`, with a version string). Never inline a prompt in business logic.
- **Structured logging** (JSON), one trace/span per request, tenant_id on every log line.
- **Idempotency keys** on every write action. **Every write also enqueues an outbox event in the same transaction.**
- **Errors:** domain raises typed exceptions; services translate to API errors; never leak stack traces or secrets to the client.

---

## 8. The deterministic engine (treat with extra care)

Location: `domain/engine/`. Pure functions, no I/O.

- `dag.py` — load prerequisites into a DAG; topological sort; cycle detection.
- `audit.py` — transcript + program → remaining requirements, remaining credits, eligible course set.
- `validator.py` — **the verifier.** Takes a candidate plan → returns structured `Violation[]` (empty = valid). Checks: prereq order, time conflicts, capacity, credit cap, corequisites, holds, repeats, offering term.
- `sections.py` — eligible courses + preferences → open, conflict-free section combinations.
- `workload.py` — deterministic difficulty aggregation → light/medium/heavy.
- `planner.py` — greedy fallback planner (valid plan without the LLM).

**Rules:** no LLM, no network, no DB inside `engine/`. Everything injected. The validator must **never throw on bad input** — it returns violations. Edge cases (circular prereq, coreq-that-is-also-prereq, wrong-term registration, hold-blocks-eligible-student) must each have a unit test that proves the verifier catches them.

---

## 9. The agent (bounded, not open-ended)

- One LangGraph agent, reached only for **hard** turns (the classifier router handles easy ones directly — this keeps cost and risk down; do not replace the router with an LLM).
- **Bounded:** max iterations, token budget, tool allowlist. If it can't resolve, it escalates or falls back to the greedy planner.
- **propose_plan flow:** engine builds eligible pool → LLM proposes 2–3 candidates → engine validates each → LLM repairs from structured violations → predictors score valid candidates → LLM ranks + explains with feasibility + risk.
- The agent **proposes and explains; it never writes directly.** Writes go through the approval-gated action pattern in `services/actions/`.

---

## 10. The action pattern (every write)

Every side-effecting action (enrollment, waitlist, petition, major-change, graduation application) is the SAME shape — implement once in `services/actions/`, instantiate per action:

```
1. Build the request (validated Pydantic model).
2. Engine validates preconditions (eligibility, etc.). If invalid → explain, do not write.
3. Require explicit approval (student or registrar).
4. Single DB transaction: write the row + insert the outbox event.
5. Write an audit_log row.
6. Worker publishes the outbox event (email / notification) with retry + backoff.
```

**Test the pattern once, reuse everywhere.** Security test: no injected or unapproved request ever reaches step 4.

---

## 11. Multi-tenancy (isolation is the grade)

- RLS policies on every tenant-owned table; the session sets `tenant_id` and the DB enforces it.
- Repositories also filter by tenant (defense in depth) — never rely on RLS alone in code review.
- pgvector queries carry a tenant filter.
- The platform operator can provision/suspend/erase tenants but **never reads tenant data**.
- Red-team CI: cross-tenant probes must all be refused.

---

## 12. Testing & CI (gates are added as features land — not at the end)

CI runs on every PR: `ruff`, `mypy`, build images, stack smoke test, **plus every eval gate that exists so far**. Add the gate the same day you build the thing:

- **Planner correctness** — no generated plan violates any hard constraint (golden set). The headline gate.
- **Intent classifier** — macro-F1 threshold.
- **Graduation-risk** — macro-F1 + at-risk recall (minority class).
- **Agent tool-selection** — right tool (or correctly none) on a golden set.
- **RAG (RAGAS)** — faithfulness, answer relevancy, context recall/precision.
- **Guardrails red-team** — injection + cross-tenant + write-action safety + PII redaction.

Thresholds live in `tests/eval/eval_thresholds.yaml`. Eval report JSON → MinIO each run.

---

## 13. Models (trained offline, served lean)

- Two trained models only: **intent classifier** and **graduation-risk**. Workload is deterministic. GPA is a light LLM baseline (explicitly the weak option — never present it as a guarantee).
- Each compared three ways (classical ML / small DL→ONNX / LLM baseline). Runs logged to MLflow.
- Winners exported to ONNX/joblib, SHA-256 pinned in a model card. **model-server refuses to boot on hash mismatch.**
- MLflow registry is the source of truth for artifacts and promotion (staging→production). Rollback = re-point to previous production version.
- Training data: graduation-risk data may be synthetic — see `DATA.md`. Never claim it's real; document the generative assumptions.

---

## 14. What to do at the start of a task

1. Check `PLAN.md` for the current step and its acceptance criteria.
2. If it touches the engine or a write action, **read the relevant `SPEC.md` section first** (or ask the human to write it).
3. For engine work, confirm the human's edge-case tests exist before implementing.
4. Make the change in the correct layer (see §5). Keep the dependency direction.
5. Add/extend the matching CI gate (§12).
6. Append any non-obvious decision to `DECISIONS.md`.
7. Update the checkbox in `PLAN.md`.

## 15. What NOT to do

- Don't let the LLM decide feasibility or invent prerequisites.
- Don't write to a table outside the action pattern.
- Don't add `torch`, a second database, a multi-agent setup, MCP, or GraphRAG (see `DECISIONS.md` for why each was rejected).
- Don't put business logic in routers or I/O in the engine.
- Don't weaken guardrails or tenant filtering "to make a test pass."
- Don't mark engine code done without the edge-case suite green.
- Don't inline secrets or prompts.
- do not make complex overengineering decisions

<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan:
`specs/001-phase-0-foundation/plan.md` (and its `research.md`, `data-model.md`,
`contracts/`, `quickstart.md`).
<!-- SPECKIT END -->
