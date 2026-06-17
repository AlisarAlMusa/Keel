# Keel — Day 5 Plan (Design Decisions & Integration) — Final

How Day 5 is built and why. The "why" companion to `spec.md` (the "what") and `tasks.md` (the "do").

---

## 1. Guiding decisions

| # | Decision | Why | Tradeoff accepted |
|---|---|---|---|
| D1 | Advising (C1–C4) and A2 are **read-only** | Correctness comes from the engine; the LLM narrates. Keeps the write surface tiny. | Students take an explicit second step (save/register/file) to act — by design. |
| D2 | Guidance (E1, E2) outputs are catalog-grounded; **E2 may be saved only through the verifier loop** | E2 has no ground truth; DAG+RAG grounding is the only honesty guarantee. Saving routes through propose-verify-repair so A4's "valid at save time" invariant holds. | E2 advice stays soft/uncaveated-as-prediction; legality is enforced only when it becomes a persisted plan. |
| D3 | All institutional requests (F1–F4) reuse **one action pattern** + `request_queue` + outbox | One subsystem, four intents — not four pipelines. Tested once. | None meaningful; this is the brief's core architectural bet. |
| D4 | **One** approval gate = the student. Registrar decision is downstream/manual. | The agent automates the *request*, not the *decision*. Matches real registration offices. | Demo shows "filed", not "approved" graduation — filing is the agent's value. |
| D5 | New `advisors` **lookup table**, no advisor role | F4 needs a routing target, not a login. Preserves the three-role model. | Slightly more seed data; negligible. |
| D6 | Idempotency on F1/F2 via partial unique index on PENDING rows | Prevents duplicate filings; reuses the enrollment idempotency pattern exactly. | Re-apply only after the prior request resolves — correct behavior. |
| D7 | F3 keeps the engine block **hard** | The petition is an override *request*, never an auto-enroll. This is the safety story. | A valid case still waits for a human — correct and intended. |
| D8 | F4 = **email handoff only**; appointment row cut | Keeps scope honest; the row added no demo value today. | No appointment booking (no calendar anyway). |

---

## 2. How each feature reuses existing engines (no new pipelines)

```
                     +---------------------------------------------+
                     |      DETERMINISTIC CORE (Days 2-4)          |
                     |  DAG · conflict checker · degree-audit ·    |
                     |  plan validator · workload index · planner  |
                     +---------------------------------------------+
   C2 audit --numbers--+   |            |            |
   C3 recovery --impact+ propose-verify-repair LOOP --+
   C4 / F2 --consequences---+            |            |
   E1 --eligible set--------------------+            |
   E2-save --propose-verify-repair------+ (then save_plan)
                     +---------------------------------------------+
                     |      ADVISING RAG (Day 3) — tenant-filtered |
                     +---------------------------------------------+
   C1 --grounded answer--+     E2 advice --catalog grounding--+
                     +---------------------------------------------+
                     |  ACTION PATTERN + request_queue + outbox    |
                     |             (Day 4 subsystems)              |
                     +---------------------------------------------+
   F1 · F2 · F3 · F4 --all four are intents over this one path--+
```

**Net new code today:** one table (`advisors`), four F-tools, six advising/guidance tools (+ E2-save wiring), one parametrized CI test, seven prompt files, one schema file. Everything else is wiring into engines that exist. C3 and E2-save are the **same `propose_plan` loop** with different seeds — no new planner.

---

## 3. AI vs Deterministic split (per feature)

| Feature | LLM decides | Engine guarantees | Predicts | Writes? |
|---|---|---|---|---|
| C1 Course Advisor | answer phrasing from retrieved context | tenant filter; prereqs from DAG | — | No |
| C2 Audit Chat | conversational summary | missing reqs, credits, eligible set | — | No |
| C3 Failure Recovery | **proposes** plan + repairs from violations; narrative | impact + eligible pool; **verifier validates each candidate** (loop, same as A1) | — | No |
| C4 Major-Switch | recommendation framing | consequences vs each program | — | No |
| E1 Elective Rec | rank + justify | eligible elective set (DAG+audit) | — | No |
| E2 Career Path | interest→skills→courses map; proposes roadmap | catalog grounding; **verifier validates before save** | — | Only via `save_plan` (verified) |
| F1 Graduation App | readiness explanation | **eligibility check**; idempotent write | — | **Yes** (queue) |
| F2 Major-Change | impact summary text | consequences; routed write | — | **Yes** (queue) |
| F3 Petition | draft justification | **block stays hard**; routed write | — | **Yes** (queue) |
| F4 Escalation | decide-to-escalate; handoff summary | routing; outbox email; audit | — | **Yes** (email) |

No model predictions introduced today. Risk/GPA already exist (Day 4) and are consumed, not built.

---

## 4. Integration points to verify before coding

1. **A4 save/load works** — C3's validated plan, A2's path, and E2-save all lean on the Plan entity + `save_plan`.
2. **request_queue columns** match spec §4; if `idempotency_key` or the partial unique index is missing, add a small Alembic migration first.
3. **Outbox publisher (Day 4)** handles new event types cleanly (`GRADUATION_APPLICATION`, `MAJOR_CHANGE`, `PETITION`, `ESCALATION_EMAIL`) — they're just rows; the publisher is generic.
4. **Guardrails (Day 3)** sit in front of every F-tool — injection/cross-tenant refusal happens *before* the agent reaches a write.
5. **Agent tool allowlist** — the four F-tools + E2-save are registered at an identifiable line (defense requirement).
6. **The loop is callable as a unit** — C3 and E2-save both invoke the existing `propose_plan` loop; confirm it accepts a pre-built eligible pool + seed.

---

## 5. Risks & mitigations (today)

| Risk | Mitigation |
|---|---|
| F3 accidentally enrolls | CI asserts `submit_petition` never creates an enrollment row, under any input. |
| LLM restates engine numbers wrong (C2/C3/F1) | Numbers passed verbatim into the prompt; the LLM formats, never recomputes (see Appendix C prompts). |
| Duplicate filings (double-click, retry) | Partial unique index on PENDING + idempotency key. |
| E2 over-claims | Hard-coded caveat in the contract; courses constrained to catalog; save is verified. |
| Notification storms from F-actions | Outbox dedup (Day-4 publisher); one event per write. |
| Cross-tenant leak via F4 advisor lookup | `advisors` is RLS-scoped by `tenant_id` like every other table. |
| E2-save persists an invalid plan | Routed through the verifier loop; only `ValidatedPlan` reaches `save_plan`. |

---

## 6. Definition of done for Day 5

- Six advising/guidance tools return correct engine numbers + grounded narration; **zero writes** confirmed (E2 advice included).
- C3 and E2-save produce verifier-valid plans via the reused loop.
- E2-save persists a `Career-aligned` plan only after verification.
- All four F-tools write a PENDING row (or send F4 email) **only after student approval**, transactionally, with audit + outbox event.
- `advisors` table migrated + seeded for both tenants; F4 resolves a real target.
- Idempotency proven (double-file → one row); F3 proven to never enroll.
- The parametrized write-action-safety CI gate is **green and enforced**.
- Pydantic schemas (Appendix A), CI test (Appendix B), and prompt files (Appendix C) committed.