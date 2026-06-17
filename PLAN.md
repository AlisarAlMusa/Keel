# PLAN.md — Keel Build Roadmap

The ordered build plan with acceptance criteria. Check items off as they land. `SCHEDULE.md` maps these to days; this is the *what* and *done-when*. Always read the current step's acceptance criteria before implementing.

**Legend:** `[ ]` todo · `[~]` in progress · `[x]` done · 🔴 critical path · ⚙ add CI gate when done

---

## Phase 0 — Foundation (Day 1)

- [x] 🔴 `SPEC.md` written for Plan entity, agent tool schemas, tenant isolation rules (use SpecKit to scaffold)
  - *Done when:* contracts exist for every Day-2/Day-4 component the human will need before coding.
- [x] 🔴 Repo layout per `CLAUDE.md` §5; `.gitignore`, `.env.example`, `README` stub
  - *Done when:* `api/services/repositories/domain/infra/workers/agent` dirs exist with `__init__`.
- [x] 🔴 `docker-compose.yml`: api, worker, model-server, db, redis, minio, vault, mlflow
  - *Done when:* `docker compose up` brings all services to healthy (empty apps OK).
- [x] Vault wired; app refuses to boot if unreachable
- [x] OTel / LangSmith tracing initialized in lifespan
- [x] 🔴 Alembic baseline migration (see schema below)
  - *Done when:* `alembic upgrade head` creates all tables + RLS policies on a clean DB.
- [x] Seed script: 2 tenants, ≥20 courses with prereq chains, sections, 2 transcripts; catalog text → MinIO for RAG
- [x] ⚙ CI skeleton: ruff + mypy + build + compose smoke test (green)
- [x] `DECISIONS.md` started

**Baseline schema (Phase 0):** `tenants`, `users`, `students`, `courses` (code, name, credits, difficulty, offering_term), `prerequisites` (course_id, prereq_id), `corequisites`, `sections` (course_id, time, capacity, enrolled), `program_requirements`, `student_transcript`, `plans` (id, tenant_id, student_id, name, version, is_active, plan_data, created_at), `enrollments`, `waitlist`, `request_queue` (type, payload, status), `outbox` (event, payload, published_at), `audit_log`, `notifications`. RLS on every tenant-owned table.

---

## Phase 1 — Deterministic Engine (Day 2) 🔴 highest-risk phase

> Human approve edge-case tests first. Engine is not done until they pass.

- [ ] 🔴 `domain/engine/dag.py` — load prereqs → DAG, topological sort, cycle detection
- [ ] 🔴 `domain/engine/audit.py` — remaining requirements, credits, eligible set, progress rate
- [ ] 🔴 `domain/engine/validator.py` — the verifier → `Violation[]`; never throws
  - *Done when:* edge-case suite passes — circular prereq, coreq-also-prereq, wrong-term registration, hold-blocks-eligible-student, credit overflow, repeated passed course, time conflict.
- [ ] 🔴 `domain/engine/sections.py` — open, conflict-free section combinations
- [ ] `domain/engine/workload.py` — difficulty aggregation → light/medium/heavy
- [ ] `domain/engine/planner.py` — greedy fallback planner
- [ ] ⚙ **Planner correctness gate** (golden set, 20+ cases) — the headline gate
- [x] `PLANNER.md` — engine contract (inputs, Violation schema, fallback)

**Parallel (offline, Colab):**
- [ ] 🟠 Train intent classifier (classical + DL→ONNX + LLM baseline), log to MLflow
- [ ] 🟠 Train graduation-risk (RF vs LR vs HistGradientBoosting, class-imbalance handling), promote winner to MLflow staging
- [ ] 🟠 Export → ONNX/joblib, SHA-256 in model cards, push artifacts via MLflow→MinIO
- [ ] MLflow tracking server up (compose), UI reachable

---

## Phase 2 — Serving, RAG, Guardrails, Router, First Loop (Day 3)

- [x] 🔴 `model-server/` — lean ONNX/joblib service; refuses to boot on SHA mismatch
- [x] 🔴 RAG: embed catalog/policy chunks → pgvector (tenant-tagged); hybrid retrieval + rerank; DAG-grounded answers
- [x] 🔴 `infra/guardrails.py` — input rails (injection, cross-tenant), output rails (PII redaction); hardcoded
- [x] 🔴 Classifier router — intent model routes easy→workflow, hard→agent
- [x] `agent/` — bounded LangGraph scaffold (allowlist, loop cap, token budget)
- [x] First tools: `audit_degree`, `propose_plan` (generate→validate→repair), `rag_search`
- [x] Redis short-term session memory (TTL)
- [x] First end-to-end: message → router → agent → valid plan returned
- [x] ⚙ Intent classifier F1 gate
- [x] ⚙ Guardrails red-team gate (injection + cross-tenant)
- [x] ⚙ PII redaction test (fake key never appears in logs/traces)

---

## Phase 3 — Planning, Prediction, Registration, Worker (Day 4)

- [ ] 🔴 `propose_plan` complete: risk-scored candidates, ranked + explained with feasibility + risk
- [ ] `simulate_whatif`, `save_plan`/load/activate (Plan entity CRUD, one active, re-validate on load)
- [ ] `swap_course` — re-validate → idempotent Plan update
- [ ] `predict_risk` tool → model-server → risk + reasons → LLM mitigation; promote model staging→prod after gate
- [ ] GPA estimate (LLM, caveated)
- [ ] 🔴 `services/actions/` — the action pattern (validate→approve→TX(write+outbox)→audit)
- [ ] 🔴 `execute_enrollment` (idempotent, transactional + outbox)
- [ ] `waitlist_join`/`waitlist_leave`; RQ worker: capacity sync, waitlist, email-on-seat-open, outbox publisher
- [ ] Caching + invalidation on catalog change; per-tenant rate limiting
- [ ] ⚙ Agent tool-selection gate
- [ ] ⚙ Write-action safety gate (no unapproved/injected write executes)

---

## Phase 4 — Advising, Guidance, Institutional Requests (Day 5)

- [x] Course Advisor RAG (C1), Degree Audit Chat (C2)
- [x] Graduation Planning (A2): skeleton → LLM optimize → validate whole path
- [x] Failure-Recovery Chat (C3): impact → recovery plan (itself verified)
- [x] Major-Switch Advisor (C4): consequences → recommendation
- [x] Elective Recommender (E1, eligible-grounded); Career Path (E2, advisory, catalog-grounded)
- [x] `apply_graduation` (F1), `request_major_change` (F2)
- [x] 🔴 `submit_petition` (F3): block detected → LLM drafts petition → request queue (human override, not bypass)
- [x] Advisor escalation (F4): LLM handoff summary → email via outbox (no role/login)
- [x] ⚙ Write-action safety extended to institutional writes

---

## Phase 5 — Frontends, Auth, Background Intelligence (Day 6)

- [ ] 🔴 Student widget (React): chat, plan view, risk/workload badges, save/compare/activate, approval
- [ ] 🔴 Signed per-widget token exchange + server-side origin check; CORS/CSP
- [ ] `widget.js` loader (one `<script>` tag → iframe)
- [ ] Admin console: catalog upload (DB + RAG), rules config, request-queue inbox, widget config, cost view
- [ ] Automatic replanning (A6): worker → affected plans → re-audit/re-plan → notify
- [ ] Personalized alerts (G1): 4 triggers → notification table → outbox
- [ ] Eval reports (RAGAS, eval_report.json) → MinIO

---

## Phase 6 — Evaluation, CI Hardening, Red-team (Day 7)

- [ ] 🔴 RAGAS golden set (25 triples): faithfulness, relevancy, context recall/precision; hand-label 5, report agreement
- [ ] 🔴 Graduation-risk gate finalized (macro-F1 + at-risk recall); promote staging→prod; model card complete
- [ ] ⚙ All gates green & enforced: planner, intent F1, risk F1+recall, tool-selection, RAGAS, guardrails red-team, smoke test
- [ ] Thresholds in `eval_thresholds.yaml`; eval_report.json diffed against last green
- [ ] Docs: `DATA.md`, `SECURITY.md`, `EVALS.md`, `DECISIONS.md` final pass

---

## Phase 7 — Integration, Docs, Demo, Ship (Day 8)

- [ ] 🔴 `docker compose up` from a fresh clone → healthy, migrated, seeded, CI green
- [ ] Verify trace tree (no PII); verify MLflow runs/promotions/artifacts
- [ ] Docs: `DESIGN.md`, `RUNBOOK.md`, `PLANNER.md` final, `README.md` (diagram, one-command run, description, CI badge)
- [ ] 🔴 Demo video: widget → request → plan + risk badge → approve → enrollment + one filed request; show trace, CI, MLflow
- [ ] Tag `v1.0.0-capstone`, push

---

## Cut order if behind (never cut engine / prediction / approval gate / guardrails / MLflow registry / CI gates)
1. Multilingual (G2) → 2. Career Path (E2) → 3. GPA estimate (D3) → 4. Personalized Alerts (G1) → 5. Automatic Replanning (A6)

---

## To add if time (stretch — not on the critical path)

- **NeMo Guardrails sidecar** — replace the in-process `infra/guardrails.py` with a
  NeMo Guardrails server running as a separate Docker service. The in-process rails
  are the MVP; the sidecar decouples rail updates from app deploys and is the
  production-grade architecture. Pre-condition: all CI guardrail gates must remain green.

---

## Acceptance bar for "the project works" (the demo spine)
A student types a plain-language request in the widget → a **valid** plan is proposed (engine-verified) with a **risk badge** → student **approves** → enrollment **executes** (transactional + outbox + audit) → one **institutional request** is filed. Everything else layers on this. If this works end-to-end with CI green, you have a defensible capstone.