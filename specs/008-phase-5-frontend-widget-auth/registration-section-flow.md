# Spec — Section-Selection Registration Flow (Planning → Registration)

> Status: **DRAFT for review.** Scopes the planning→registration redesign discussed
> 2026-06-25. Piece 1 (graph routing bug) is **already fixed**; pieces 2–4 below are
> proposed and gated on human approval before agent/tool code lands (changes to agent
> behaviour require sign-off per the project debugging protocol).
>
> **Saved/named plans (A4) are deferred to STRETCH** by decision (2026-06-25) — see
> `STRETCH.md` → "First-class saved/named Plan entity". The registration flow below
> operates on the plan the student selected **in the current conversation**, not a
> persisted plan.
>
> Read with: `KEEL_BRIEF.md` §3 (Propose·Verify·Predict·Approve), §B1/§B2,
> `SPEC.md` §3.4/§7/§8, `PLANNER.md`, `EXPLAIN.md` (routing), `system_v3.md`.

---

## 1. Problem

Planning and registration are wired, but the seam is thin and was partly broken:

1. **`propose_plan` validates academic feasibility but NOT live sections.** It builds
   plans from `audit().eligible_now` and calls `verify(...)` **without** the `sections`
   argument, so it checks prereqs / credit cap / offering term but never whether an
   **open section actually exists** in the chosen term. A plan can contain a course
   (e.g. `CS420` in Spring 2026, whose only seeded section is another term) that has no
   registrable section — the student only discovers this at enroll time.
2. **Section choice is not agentic.** `stage_enrollment` deterministically auto-picks
   the first open, conflict-free section per course. There is no step where the student
   states preferences ("no 8am, no Fridays") and the **agent reasons** over the open
   sections to choose a fitting schedule — which is the core idea of the project applied
   to sections.
3. **The demo seed has one section per course per tenant, year 2026, single term**
   (`scripts/seed.py` §"Sections"; no `instructor` column). There is nothing to reason
   *over*, and courses planned in a non-offered term have **zero** sections.
4. **(FIXED — Piece 1)** A failed `stage_enrollment` (no open section) returned a
   `ToolError` that never reached the LLM: the graph routed `stage → interrupt`
   unconditionally and suspended with no action, so the student saw an empty/looping
   reply instead of "that section is full — want the waitlist or another term?" Fixed
   via a result-conditional edge (`_after_stage`) in `agent/graph.py`: a stage result
   **with** an `action_id` pauses for approval; a `ToolError` (no `action_id`) routes
   back to the LLM so it can explain and offer an alternative.

---

## 2. Core principle (the project's spine, applied to sections)

> **Intelligence proposes. The deterministic engine verifies. The student approves.**

The section step mirrors the planning loop exactly:

| Layer | Planning (`propose_plan`) | Sections (`propose_sections` — NEW) |
|---|---|---|
| **Engine builds the pool** | `audit().eligible_now` | **all OPEN sections** for the chosen plan's courses (no preference filtering) |
| **LLM proposes (fuzzy)** | 2–3 candidate plans | 2–3 **section combinations** that best fit the student's stated preferences (no 8am / no Fridays / compact …) — maybe only one fully aligns |
| **Engine verifies** | `verify(plan)` → `Violation[]` | each combo re-checked: conflict-free + every section open |
| **Predict / rank** | risk + workload, LLM ranks | LLM ranks by preference-fit, notes which prefs each meets |
| **Approve** | student picks a plan | student approves one combo (approve/reject) |

The LLM never *filters* sections like a portal; it **reasons** over the full open set
against soft preferences and proposes — the engine guarantees legality.

---

## 3. Target conversational flow

```
"plan my term [+ prefs]"  → propose_plan ──► plan cards ──► student picks a plan
                                                                  │
"register me for <plan/courses>" (plan already in conversation) ──┤
                                                                  ▼
        propose_sections(plan courses, term, year, preferences)
          1. engine: fetch ALL open sections for those courses        (deterministic)
          2. LLM: propose 2–3 section combos matching preferences      (fuzzy)
          3. engine: validate each combo (conflict-free + open)        (deterministic)
          4. flag any course with NO open section → offer waitlist / alt term
                                                                  │
                              section combo cards (per course: day/time, instructor,
                              seats; which prefs met) ──► student picks one combo
                                                                  ▼
        stage_enrollment(section_ids)  ──► engine RE-verifies (open + conflict-free
          + eligible) ──► pending action ──► approval card (Approve / Reject)
                                                                  │
                approve ──► execute_node → single TX write + outbox + audit
                reject  ──► action cancelled; student says why in chat → agent re-proposes
```

Combined "plan my fall 2026, no 8am, no Fridays, balanced" chains
`propose_plan → propose_sections → stage_enrollment` in one conversation — the agent
already chains tools (system prompt rule 11). One pipeline, three entry points, **no
duplication**.

---

## 4. Design — pieces 2, 3, 4

### Piece 2 — Richer seed + `instructor` column (LOW risk)

**Migration** `migrations/versions/00xx_section_instructor.py`:
- `ALTER TABLE sections ADD COLUMN instructor text NULL;` (nullable → no backfill; RLS
  unaffected — same table).
- Add `instructor: Mapped[str | None]` to `Section` in `infra/database/models.py`.

**Seed** (`scripts/seed.py`):
- Emit **2–3 sections per course per term it is offered** (today: 1), with varied `slots`
  (e.g. a MW 9am section and a TR 2pm section — so "no 8am / no Fridays" actually
  discriminates) and synthetic `instructor` names.
- **Deliberately keep some sections full / a course unoffered in a term** so the agent's
  "no open section → offer waitlist / suggest another term" branch is exercised. Keep the
  existing full sections `CS301` (Northane) and `DS210` (Summit) as the waitlist demo;
  make each *one* full section among open ones where a course has several, so "full" is a
  real branch, not always a dead end.
- Instructor names are **synthetic** — labelled as seeded in `DATA.md` (FERPA/honesty).

**No engine change** — section search / verify already accept many sections per course.

### Piece 3 — `propose_sections` tool + section combo cards (MEDIUM risk)

A **read-only** tool (no writes, no approval) implementing the loop in §2.

```python
class ProposeSectionsInput(BaseModel):
    student_id: str
    tenant_id: str
    course_codes: list[str]                  # the chosen plan's courses
    term: str
    year: int
    preferences: list[str] = []              # natural-language prefs, e.g.
                                             # ["no classes before 9am", "no Fridays",
                                             #  "prefer compact days"]

# Steps inside the tool:
# 1. Engine: SELECT open sections (enrolled < capacity) for course_codes/term/year.
#    NO preference filtering here — return the full open set per course.
# 2. LLM: given the open sections + preferences, propose up to 3 combinations
#    (one section per course). Reason about which prefs each combo satisfies.
# 3. Engine: validate each combo — conflict-free (TimeSlot overlap) + all open.
#    Drop invalid; on invalid, one repair pass (mirror PLANNER.md MAX_REPAIR_ROUNDS).
# 4. Courses with NO open section → collected as `unavailable` with a reason
#    (full vs not-offered-this-term) so the agent can offer waitlist / alt term.
# Returns structured combos → emitted as SECTION CARDS (see below) + a short summary.
```

- Reuse `domain/engine/sections.py::find_sections` for conflict-free combination logic;
  extract the section-time formatting already in `propose_plan` (planning.py ~380–445)
  into a shared helper so there is **one** formatting path.
- **Distinguish "full" from "not offered this term"** in the unavailable reason so the
  agent gives the right remedy (waitlist vs. plan in another term). This is a small but
  important UX fix over today's generic message.
- Emit structured **section combo cards** via the existing plan-channel pattern
  (`agent/plan_channel.py`); add `emit_sections` / a typed payload. Widget renders a
  `SectionCard` (mirrors `PlanCard` in `frontend/widget/src/ChatWidget.tsx`): each card =
  one full schedule option listing, per course, the section's days/times, instructor, and
  seats-open, plus a line on which preferences it meets.
- `propose_sections` is named `search_sections` in `SPEC.md §7`; this realizes that
  contract (rename or alias — note in DECISIONS).

### Piece 4 — student-chosen sections in `stage_enrollment` + plan registrability (MEDIUM risk)

**4a. `stage_enrollment` accepts explicit `section_ids`; engine re-verifies.**
- Add `section_ids: list[str] | None = None` to `StageEnrollmentInput`.
- If provided (the combo the student approved): the engine **re-verifies** each section
  is (a) the right course, (b) open, (c) conflict-free with the others, (d) eligible —
  via `verify(... sections=...)`. Any violation → `ToolError` (now correctly surfaced
  after Piece 1) → conversational repair. **The LLM never invents a section** — it passes
  IDs the student selected from `propose_sections`, and the engine re-checks them.
- If omitted: keep today's deterministic auto-pick as the "you choose for me" fallback.
- Frozen payload still stores resolved `section_ids`; `execute_node` unchanged.

**4b. Make proposed plans registrable by construction.**
- `propose_plan` passes `sections` to `verify()` (or runs a post-pass) so a course with
  **no open section in the target term is flagged** — recommended behaviour: **keep + flag**
  ("CS420 has no section in Spring 2026 — take it in Fall or join the waitlist"), so the
  advising stays useful rather than silently dropping the course.

---

## 5. Safety invariants (must hold)

| Invariant | How preserved |
|---|---|
| LLM never decides feasibility | Engine returns the open-section pool and re-validates every LLM/student-chosen combo. |
| No write without approval | Unchanged: `propose_sections` is read-only; `stage_enrollment` only **stages**; `execute_node` writes only on approved resume. |
| Tenant isolation | All section/plan reads run in `tenant_session` (RLS) + repo scoping. |
| No self-approval | No tool has an `approved` field; `propose_sections` is read-only. |
| No fabricated results | New system-prompt rule (below) forbids inventing a result when a tool errors. |

**New system-prompt rule (add to `system_v3.md`):**
> *If a tool returns an error object, tell the student plainly what failed and whether
> it's worth retrying (use the error's `retryable` flag). Never fabricate a result to
> fill the gap. For a full or unavailable section, offer the waitlist or another term.*

---

## 6. Risk assessment

| Piece | Files touched | Risk | Why |
|---|---|---|---|
| 1 (done) | `agent/graph.py` | Low | Conditional edge on the already-broken error path; happy path + approval gate untouched; agent tests green. |
| 2 | migration, `models.py`, `seed.py`, `DATA.md` | **Low** | Nullable column + seed data; no engine/API/RLS change. |
| 3 | `agent/tools/*`, `plan_channel.py`, `system_v3.md`, widget | **Medium** | New read tool + new card type + prompt rule. Read-only ⇒ no write-safety exposure. |
| 4 | `agent/tools/enrollment.py`, `planning.py`, `system_v3.md` | **Medium** | Touches the write tool — risk is *not weakening* verify-then-stage-then-approve; mitigated by engine re-verification with `sections` + existing write-safety tests. |

No rule in `CLAUDE.md §2/§3` is crossed; the write tool keeps every guarantee because the
engine re-verifies student-selected sections.

---

## 7. Task breakdown (ordered)

- [x] **T1** Fix graph routing so a failed stage returns to the LLM (`_after_stage`).
- [x] **T2.1** Migration `0013_section_instructor`: `sections.instructor` (nullable) + model.
- [x] **T2.2** Seed: 2 sections/course/offered-term with instructors + varied times;
  kept some full / unoffered-in-term to exercise the waitlist/alt-term branch.
- [x] **T2.3** `DATA.md` §1b: instructor names synthetic/seeded.
- [x] **T3.2** `propose_sections` read tool: engine open-section pool per course +
  instructor/seats + which meet prefs; distinguishes full vs not-offered.
- [x] **T3.4** System-prompt: `propose_sections` step + preference pass-through + the
  tool-error rule (rule 12).
- [x] **T4.1** `stage_enrollment` accepts `excluded_days`/`min_start_hour`; engine resolves
  preference-aware, conflict-free, open sections and surfaces the chosen schedule.
- [x] **T4.2** `propose_plan` flags courses with no open section in the target term.
- [x] **T5** Tests: `tests/unit/test_section_selection.py` (pref ranking, conflict
  avoidance, unresolved, `_after_stage` routing); write-safety + agent-node suites green.
- [x] **T6** `DECISIONS.md` D-P6-003; this spec updated. `SPEC.md §7` notes `propose_sections`.
- [x] **Section cards (DONE)** — `propose_sections` emits a structured `sections` card
  (per course: open options with time/instructor/seats + ✅/⚠ pref fit) via the plan
  channel; the widget renders `SectionOptionsView`. `plan_graduation` likewise emits 2–3
  `gradplan` variant cards (tabbed `GradPlanView`).
- [ ] **Deferred** `T3.1` formatting-helper extraction from `propose_plan` (left as-is to
  avoid refactoring working code).

## 7b. As-built refinement (vs §2–§4 draft)

The implemented design is **more automated** than the draft's "student picks one section
per course": the student expresses preferences in natural language and the **agent +
engine choose** the matching sections; the student simply **approves the resulting
schedule**. So `stage_enrollment` takes `excluded_days`/`min_start_hour` (not student-
supplied `section_ids`), and the LLM does the fuzzy preference reasoning while the engine
owns legality. `propose_sections` is the read-only "show me my options" preview. This is
closer to the project's "say your prefs, the agent reasons" goal and avoids passing
section UUIDs through chat. A structured `SectionCard` remains a nice-to-have (deferred);
the chosen schedule is surfaced in the approval message today.

---

## 8. Decisions (resolved 2026-06-25)

1. **Saved/named plans → STRETCH** (see `STRETCH.md`). Registration uses the
   conversation-selected plan, not a persisted one.
2. **Section selection is agentic, not filter-based** — engine returns the open pool;
   the LLM reasons over preferences and proposes 2–3 combos; the engine validates.
3. **Reject = cancel + comment via chat** (no comment box on the reject button).
4. **Keep some full/unoffered sections in the seed** to exercise waitlist / alt-term.
5. **Add the tool-error system-prompt rule.**
6. One `propose_sections` call covers all plan courses; one `stage_enrollment` stages the
   chosen combo together (one approval). Keep the "you choose for me" auto-pick fallback.
   Synthetic instructor names. `propose_plan` keeps + flags no-section courses.
