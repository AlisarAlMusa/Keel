# Keel — Day 5 Tasks (Final)

Actionable checklist, dependency-ordered: schema → contracts → engine-backed advising → guidance → institutional writes → prompts → CI gate → docs. `[CRIT]` = blocks later work. `[CI]` = add the gate the moment the thing it tests exists.

---

## Block 0 — Schema & seed (do first)

- [ ] **`advisors` table** — Alembic migration: `id, tenant_id (RLS), name, email, program, created_at`. No role, no auth. `[CRIT]`
- [ ] **request_queue check** — confirm columns: `id, tenant_id, student_id, request_type, target, status, payload, idempotency_key, created_at`. Add `idempotency_key` if missing.
- [ ] **Partial unique index** — `(tenant_id, student_id, request_type, target) WHERE status='PENDING'` for F1/F2 idempotency. `[CRIT]`
- [ ] **Seed** — 1–2 advisors per tenant (with `program`) so F4 can resolve a target.

## Block 0.5 — Contracts (before any tool logic)

- [ ] **`app/models/schemas_day5.py`** — all Pydantic In/Out models from spec Appendix A. `[CRIT]`
- [ ] Confirm `ValidatedPlan` is constructed **only** by the engine (type-level `is_valid: True` guarantee).

---

## Block 1 — Advising (read-only, no writes)

- [ ] **C1 Course Advisor** — RAG answer; **prereqs injected from engine DAG**, not prose. Return `CourseAdvisorOut`.
- [ ] **C2 Degree Audit Chat** — engine computes numbers; LLM summarizes. Pass numbers **verbatim** into the prompt; assert not recomputed.
- [ ] **A2 Graduation Planning** — engine builds path skeleton; LLM optimizes toward goal (fastest/balanced/easier); **engine validates whole path**. Savable via A4 (no new write path).
- [ ] **C3 Failure-Recovery Chat** — engine computes downstream-delay + grad-date impact and rebuilds the eligible pool; LLM **proposes** a recovery plan and **repairs from verifier violations until valid** (reuse the `propose_plan` generate→verify→repair loop, not a new path; greedy fallback if it can't converge); then narrates. No write.
- [ ] **C4 Major-Switch Advisor** — engine computes consequences vs candidate program; LLM recommends with explicit "advisory, not a guarantee" framing. No write.

## Block 2 — Guidance

- [ ] **E1 Elective Recommender** — eligible set from engine (DAG+audit); LLM ranks by strengths/GPA/difficulty/career. **Drop any course not in the eligible set.** No write.
- [ ] **E2 Career Path (advice)** — LLM maps interest→skills→catalog electives→projects; grounded via RAG+DAG. **Hard caveat** "suggestion, not a prediction"; courses must exist in catalog. Chat is advisory/unverified.
- [ ] **E2 save roadmap (E2 → loop → A4)** — when the student saves, route suggested courses through the **propose-verify-repair loop** (reuse A1/C3 loop), then call existing `save_plan(name="Career-aligned")`. Saved plan is verifier-valid; no new write path (reuses Plan entity + `save_plan`).

---

## Block 3 — Institutional Requests (the F-writes — one action pattern)

> Every F-tool: `propose → engine-validate → STUDENT approves → TXN{queue row + outbox} → audit`.

- [ ] **F1 `apply_graduation`** — engine confirms **all** requirements met before offering; on approval → idempotent PENDING write + outbox + audit. Key `(tenant, student, GRADUATION_APPLICATION, program)`. `[CRIT]`
- [ ] **F2 `request_major_change`** — reuse C4 consequences as the impact summary; on approval → PENDING write + outbox + audit. Key `(tenant, student, MAJOR_CHANGE, target_program)`.
- [ ] **F3 `submit_petition`** — engine detects eligibility block and **refuses to auto-enroll**; LLM drafts justification from student reason + transcript; on approval → `PETITION` PENDING write + outbox + audit. **Never writes an enrollment row.** `[CRIT]`
- [ ] **F4 `escalate`** — LLM decides escalation + writes handoff summary; resolve target from `advisors` by program; send via outbox email + audit. Email only.
- [ ] **Register all four F-tools + E2-save** on the bounded agent's allowlist — at an identifiable line. `[CRIT]`
- [ ] **Guardrails in front** — confirm injection/cross-tenant rails run before any F-tool executes.

## Block 3.5 — Prompt templates (alongside the tools)

- [ ] Create `app/services/prompts/`: `c2_audit_summary_prompt.py`, `c3_recovery_propose_prompt.py`, `c4_major_switch_prompt.py`, `e1_elective_rank_prompt.py`, `e2_career_path_prompt.py`, `f3_petition_draft_prompt.py`, `f4_handoff_summary_prompt.py` (text in spec Appendix C). Versioned in source control.

---

## Block 4 — CI gate (add immediately after Block 3) `[CI]`

- [ ] **`tests/test_institutional_write_safety.py`** — parametrized over all four F-tools (full test in spec Appendix B):
  - [ ] no write without explicit student approval → 0 rows in queue/outbox
  - [ ] injection probe → never triggers an F-write
  - [ ] cross-tenant request → never writes for another tenant
  - [ ] `submit_petition` → produces PETITION row, **never** an enrollment row
  - [ ] double-call F1/F2 with same key → exactly **one** PENDING row
- [ ] Gate **blocks merge** on regression; asserts committed; wired into `.github/workflows/test.yml`.

---

## Block 5 — Docs (parallel, end of day)

- [ ] **docs/DECISIONS.md** — log D1–D8 from `plan.md` (read-only advising, single student gate, advisors-table-no-role, F3 hard block, idempotency reuse, F4 email-only, E2 saved only through the verifier loop).
- [ ] **docs/SECURITY.md** — extend write-action-safety section to the four institutional writes; note F3-never-enrolls.
- [ ] **docs/ENGINE.md** — note that C3's recovery plan reuses the `propose_plan` generate→verify→repair loop (failure baked into the audit) and A2's path re-enters the verifier — no special-case planner code for either.
- [ ] **docs/SPEC.md** — fold today's contracts in (or link this `spec.md`).

---

## Cut order (only if behind — advisory items first)

E2 Career Path (advice is the softest feature; its save-wiring is trivial reuse, so cutting saves little) → C4 narration depth → E1 → C3 narration depth.
**Never cut:** the four F-writes, the action pattern, the approval gate, guardrails, the CI gate.

---

## End-of-day acceptance

- [ ] Six advising/guidance tools: correct engine numbers, grounded narration, **zero writes** (E2 advice included).
- [ ] C3 + E2-save produce verifier-valid plans via the reused loop; E2-save persists only after verification.
- [ ] Four F-tools: write **only** after student approval, transactional, audited, outbox event emitted.
- [ ] `advisors` migrated + seeded; F4 resolves a real target email.
- [ ] Idempotency proven; F3-never-enrolls proven.
- [ ] Institutional write-action-safety CI gate **green and enforced**.
- [ ] Schemas (App. A), CI test (App. B), prompt files (App. C) committed.