# Keel — Day 6 FRONTEND Design & Build Guide

> **Read order:** `production.md` (architecture) → `spec.md` (contracts) → `plan.md` (patterns) → `tasks.md` (work items) → **this file** (the visual + UX layer).
> This guide does **not** repeat endpoint contracts. It tells you *what the three surfaces look like, how they behave, and what the user sees*. Where this file and `spec.md` disagree on a route or payload, **`spec.md` wins**. Where they disagree on look/UX, **this file wins**.

---

## 0. The one rule that shapes the whole frontend

Keel is **an AI layer on top of the university's SIS — not the SIS itself.** The UI must *show* that boundary, not hide it.

So the three surfaces deliberately do **not** look the same:

| Surface | Whose product | Skin | Feeling |
|---|---|---|---|
| **Mock SIS portal** (host page + My Schedule + registrar) | the university's SIS | **Institutional skin** — light, plain, a bit boring, "official records system" | "This is my school's portal." |
| **Student widget** (chat, plans, approval) | **Keel** | **Keel skin** — dark navy, calm, premium, focused | "This is the smart assistant living *inside* the portal." |
| **Keel admin console** (registrar configures the agent) | **Keel** | **Keel skin**, dashboard variant — light navy work surface | "This is where I run Keel." |

The widget popping (dark, premium) against the plain portal **is the demo's strongest visual idea.** It proves the boundary at a glance. Spend your design effort here.

---

## 1. Design intent

- **Trustworthy and calm, not flashy.** This product registers real students into real classes and files petitions. It should feel like a serious records tool, closer to a bank or a clinical system than a consumer chatbot.
- **One bold thing, everything else quiet.** The bold thing is the **widget against the portal** (point 0) and the **approval moment** (point 8). Everything else is disciplined and plain.
- **Do NOT use** glowing low-poly graduation caps, neon wireframe lightbulbs, circuit-board textures, or sparkly "AI" stock art. They read as generic and cheapen a serious product. (The inspiration images of that type were mood-only — ignore them as literal UI.)
- **Honesty in the UI.** A risk badge is a *prediction*, not a verdict. A "via Keel" badge is *provenance*, not a feature ad. Labels say what is true, plainly.

---

## 2. Design tokens

### 2.1 Color — base palette (from the provided palette image; use these exact hex)

| Token | Hex | Role |
|---|---|---|
| `--moonlight` | `#F0ECDD` | Light text on dark; light page background; the primary action fill **on the dark widget** |
| `--frost` | `#8BA3C5` | Muted accent, borders, secondary text on dark, inactive states |
| `--steel` | `#495B7D` | Mid tone; secondary buttons; secondary text on light; chart mid |
| `--storm` | `#23354D` | Card / panel surface on the dark widget; headers on light |
| `--oxford` | `#02122F` | Darkest — the widget background; primary text on light |

### 2.2 Color — extensions (NOT in the palette image — **please confirm or swap**)

The base palette is 5 blues + cream. A working UI still needs **one action accent** and **a few status colors** (risk and workload badges can't all be blue). These are deliberate, minimal additions. Confirm them or give me replacements:

| Token | Hex | Role | Note |
|---|---|---|---|
| `--accent` | `#5BC2E7` | Focus rings, active tab, links, the "streaming" indicator | A brighter frost-cyan. This is the "one bright color." Use sparingly. |
| `--risk-ontrack` | `#3E8E7E` | "On-track" badge | Muted teal-green. |
| `--risk-atrisk` | `#D9A441` | "At-risk" badge | Calm amber — noticeable, **not** alarmist red. At-risk is the case that matters. |
| `--load-light` | `#8BA3C5` | Workload: light | = `--frost` (reuse) |
| `--load-medium` | `#495B7D` | Workload: medium | = `--steel` (reuse) |
| `--load-heavy` | `#D9A441` | Workload: heavy | = `--risk-atrisk` (reuse) |

**Primary action button:**
- On the **dark widget** → fill `--moonlight`, text `--oxford` (cream on navy pops, stays in-palette, looks premium).
- On **light surfaces** (portal, admin) → fill `--storm`, text `--moonlight`.

Keep one primary action visible per screen. Secondary actions are outlined in `--steel`.

### 2.3 Typography

Two faces, loaded from Google Fonts (no files needed):

- **Display / headings:** a calm modern serif — **Fraunces** or **Source Serif 4**. Used with restraint (page titles, the Keel wordmark, plan names). The serif signals "institutional / trustworthy."
- **Body / UI / data:** **Inter** (or **IBM Plex Sans**). All controls, chat text, tables, numbers.
- Optional **mono** for IDs/audit/cost figures: **IBM Plex Mono**.

Type scale (rem): 2.0 / 1.5 / 1.25 / 1.0 / 0.875 / 0.75. Body 1.0, line-height 1.5. Don't go below 0.75 for any readable text.

### 2.4 Spacing, shape, depth, motion

- **Spacing:** 4 px base; use 4/8/12/16/24/32/48.
- **Radius:** 10–12 px on cards and the widget; 8 px on buttons/inputs. The portal can be squarer (6 px) to feel more "records system."
- **Elevation:** one soft shadow for the widget panel and floating cards. The portal stays flat (institutional).
- **Motion:** minimal. Chat tokens stream in; the launcher→panel open is a short ease (~150 ms); buttons have a quiet hover. Respect `prefers-reduced-motion`. No ambient particles, no glowing pulses.

---

## 3. Shared component primitives (build once, reuse on all three surfaces)

Put these in a shared `ui/` folder. Same tokens, two skins (Keel-dark, SIS-light) via a wrapper class. This keeps the frontend small instead of three separate UIs.

- `Button` (primary / secondary / ghost / danger), with `loading` and `disabled`.
- `Badge` (variants: `via-keel`, `risk-ontrack`, `risk-atrisk`, `load-light|medium|heavy`, `status-pending|sent|approved|rejected`).
- `Card` / `Panel`.
- `Field` (label + input + inline error; active voice; errors say what to fix).
- `Table` (read-only data; used by cost, audit, schedule, registrar views).
- `Tabs`, `Toast`, `EmptyState`, `Spinner`, `Modal` (used for the approval confirm).
- `StreamingText` (renders SSE tokens as they arrive).

Copy rules (from the design skill, applied): label things by what the **student/registrar** controls, never by system internals. Button verb = result: "Approve & enroll" → toast "Enrolled". Empty states invite an action. Errors don't apologize and are never vague.

---

## 4. Surface A — Student widget (React/Vite) — the centerpiece

**Skin:** Keel-dark. Background `--oxford`, panels `--storm`, text `--moonlight`, muted `--frost`.

**Mount model (see plan §2e):**
1. Page load → `widget.js` injects only a **launcher icon** (bottom-right). No token yet.
2. Click launcher → panel opens in an **iframe** → **only now** fetch the Keel token (`GET /portal/keel-token`), keep it **in memory** (never localStorage). Silent re-fetch + retry once on `401`.
3. Panel is a focused chat column, ~380 px wide on desktop, full-screen sheet on mobile.

**Layout inside the panel (top → bottom):**
- **Header:** Keel wordmark (serif) + persona name (from widget-config) + close. Tiny "secure session" cue is fine; don't oversell.
- **Message stream:** student bubbles (right, `--steel`), Keel bubbles (left, `--storm`). Keel text **streams** token-by-token via SSE. A subtle `--accent` dot = "thinking/streaming".
- **Plan card** (when the agent returns a plan — this is the hero component):
  - Plan name (serif) + term + total credits.
  - Course rows: code, title, credits, section/time.
  - **Two badges, always:** a **risk badge** (`On-track` teal / `At-risk` amber) and a **workload badge** (`Light` / `Medium` / `Heavy`). These come straight from the backend — the UI never computes them.
  - Short LLM explanation under the plan ("why this plan").
  - If 2–3 candidate plans are returned: a **compare** strip (tabs or side-by-side mini-cards) → student picks one.
  - Actions: **Save plan**, **Activate**, and the gated **Approve & enroll**.
- **Composer:** text input + send. Disabled while a write is awaiting approval.

**Plan tools in the UI:** Save / Load / Compare / Activate map to the existing Plan tools. Exactly one plan shows an "Active" badge.

**AI vs deterministic — what the UI must signal:**
- The chat reply and the plan *explanation* are **AI** (may stream, may vary). Fine to show as conversational.
- The plan's **validity, risk badge, workload badge, credit totals** are **engine/model outputs** — render them as **facts**, in stable chips, never as chat prose the model could restate wrong.

---

## 5. Surface B — Keel admin console (React) — registrar configures the agent

**Skin:** Keel-light dashboard (light `--moonlight` bg, `--storm` headers, `--steel` text). Plain left nav + content.

Screens (config only — **no** catalog/student CRUD, **no** rule editing, **no** request queue here; the queue lives in the SIS portal):
1. **RAG upload** — drag-drop `catalog.md` / `policy.md` / handbooks → on success show `{docs, chunks, last_upload}`. Make clear this is **prose for advising**, not structured records.
2. **Widget config** — form for `persona`, `allowed_origins[]`, `enabled_tools[]`. **Safety rails are locked and not shown as editable** (state that they're enforced in code).
3. **Widget snippet** — read-only `<script …>` block with a copy button.
4. **Cost** *(thin)* — table grouped by `kind` (llm/embedding) with token + cost totals for the period. Plain table, optional one small bar.
5. **Audit** — read-only, reverse-chronological table (who/what/when). Mono for IDs.

---

## 6. Surface C — Mock SIS portal (the host) — make it look like a real school portal

**Skin:** SIS-institutional. Light, flat, squarer corners, a plain top bar with a **fictional university name + simple text/crest logo** (invent one, e.g. "Northgate State University" — do **not** use any real university's name or logo). This surface should look intentionally *plainer* than the widget.

**Student role:**
- **Student switcher** (the SSO stand-in) → calls `/portal/login`.
- **My Schedule** *(REAL)* — current enrollments table. Rows that Keel created show a **"via Keel" badge** (from `enrollments.source`). This table updating after an approval is the **write-proof** of the whole demo — make the new row + badge obvious.
- **Requests** *(REAL)* — this student's requests with status. **Activity** — recent changes (read view).
- **Section search** + **submit-petition** form *(STAGE SET)* — they render and look real but have **no endpoints**. Buttons are visibly present but inert. Don't fake success states that imply a write.
- The **Keel widget is embedded on these pages** via the snippet.

**Registrar role:**
- **Request queue** *(REAL — the one functional registrar action)* — list pending requests; each has **Approve / Reject + note**; submitting updates the request and notifies the student. This is the only place the registrar writes.
- **Catalog / Sections / Students / Rules** *(read-only)* — render real seed data with **dead Add/Edit buttons** (present but disabled or no-op, to show the shape without building CRUD).

---

## 7. Critical UX rules (don't violate these)

1. **The approval gate is sacred and unmistakable.** No enrollment, waitlist, petition, major-change, or graduation-application ever happens without an explicit student tap on a clearly-labeled button ("Approve & enroll"). Use a confirm modal that **restates exactly what will happen** before the write. Until approval, show the plan as a *proposal*.
2. **Never show a write as done before it is.** The "via Keel" row appears only after the real write returns.
3. **Risk/workload are predictions** — label them so. The at-risk badge offers the mitigation text the backend returns; it never scolds.
4. **Streaming + loading states** for every async call (chat, plan, upload). Never a frozen screen.
5. **Empty/error states** everywhere (no plans yet, no requests, upload failed, token expired). Errors say what happened and the next step.
6. **Token never leaves memory.** No token in localStorage, no `student_id` ever sent from the client.

---

## 8. Quality floor

- Responsive down to mobile (widget = full-screen sheet on phones).
- Visible keyboard focus (`--accent` ring); tab order sane.
- Contrast: cream text on `--oxford`/`--storm` passes AA; don't put `--frost` text on `--oxford` for body copy (too low) — use it only for large/muted labels.
- `prefers-reduced-motion` respected.
- Small bundle; served from API/MinIO with cache headers (per tasks B).

---

## 9. Build / fake / skip (mirrors `tasks.md`)

- **Build real:** widget (chat + plan + badges + approval + enroll), lazy token + RLS-wrapped `/chat`, My Schedule + via-Keel badge, requests/activity, registrar request queue, admin (RAG / widget-config / snippet / cost / audit).
- **Stage set (looks real, no endpoint):** student section-search, submit-petition form.
- **Read-only-real (reads live, writes dead):** registrar Catalog / Sections / Students / Rules.
- **Skip:** manual Add/Drop, SIS CRUD writes, rule editor, editable safety rails.
- **Cut order if behind:** registrar Students/Rules → cost dashboard UI → student stage-set pages. **Never cut:** approval gate, My Schedule write-proof, registrar queue, the auth boundary.

---

## 10. Acceptance (visual / UX checklist)

- [ ] Portal looks institutional and plain; widget looks dark/premium; the contrast is obvious.
- [ ] Launcher loads with no token; token is fetched only on chat open; lives in memory.
- [ ] Plan card always shows a risk badge **and** a workload badge, rendered as facts.
- [ ] Approve & enroll is gated by a confirm modal that restates the action.
- [ ] After approval, My Schedule shows the new row with a "via Keel" badge.
- [ ] Registrar approve/reject updates the request and the student sees the new status.
- [ ] Stage-set pages render but perform no writes; read-only registrar pages have dead buttons.
- [ ] All three surfaces use the same tokens and shared primitives.
- [ ] Mobile, keyboard focus, reduced motion, empty/error states all handled.

---

## 11. Assets Claude needs from you

Short answer: **almost nothing — this file carries the tokens.** Specifically:

- **Color palette** — already encoded as hex above. You do **not** need to send the palette image to Claude.
- **Fonts** — named above (Google Fonts). No files.
- **Keel logo** — *optional.* If you have an SVG, drop it in `ui/assets/`. If not, Claude renders a **typographic serif wordmark** "Keel" — fine for the demo.
- **University identity for the portal** — Claude invents a fictional name + simple text/crest. **Do not** supply or ask for a real university's logo (IP).
- **Inspiration images** — **do not** give Claude the glowing-cap / circuit / lightbulb stock images; they will push it toward generic neon UI. The only image worth referencing for *mood* is the dark-navy + cyan depth feel — already captured in the tokens.
- **Existing Day 1–5 code** — Day 6 builds both React surfaces fresh, so no prior UI screenshots are needed. Do point Claude at the existing `Settings`, `/chat`, RLS dependency, and Plan tools so it wires to them instead of re-inventing.

Hand Claude: `production.md`, `spec.md`, `plan.md`, `tasks.md`, **this file**, and (optionally) a `Keel` logo SVG. That's the full set.