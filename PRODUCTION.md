# Keel — Production Architecture & Boundary

The production-oriented truth: what Keel is, what the SIS is, how they integrate, and what the demo mocks vs. ships for real. This is the defense artifact. Cross-reference it from `DESIGN.md`.

---

## 1. What Keel is

Keel is a multi-tenant AI advising-and-registration **layer that sits on top of a university's SIS** (Banner, Workday, PeopleSoft, etc.) through a canonical domain model and a pluggable adapter. The SIS owns the academic backbone; Keel adds the intelligence and safe-action layer.

> One-line defense: *"Keel is a multi-tenant AI advising-and-registration layer that sits on top of a university's SIS via a canonical domain model and pluggable adapters; the capstone ships a mock SIS adapter over a tenant-isolated Postgres to run the full planning, registration, and advising flow end-to-end."*

It is **Concierge + an operational spine**: Concierge's pattern (multi-tenant, widget-on-host-site, RAG over tenant content, isolation) plus a deterministic engine and structured operational data that let the agent *act*, not just answer.

---

## 2. The two systems

```
University SIS (system of record)            Keel (agent layer)
 ├─ SIS DB: catalog, sections, students,      ├─ Keel DB: plans, conversations, risk,
 │  transcripts, enrollments, requests        │  RAG/pgvector, config, audit, outbox, cost
 └─ SIS Student/Registrar Portal  ◄─ widget ─►└─ Keel admin console
        (university owns this)                    (registrar configures the agent)
                     \                          /
                      \__ per-tenant adapter (API contract) __/
```

- Keel's **admin + widget** share Keel's DB. The **portal is the SIS's**, not Keel's.
- They link through the **adapter**, not a shared DB.

### Canonical model + adapter
The engine speaks one internal language — `Student`, `Course`, `Section`, `Enrollment`, `Transcript`, `Request` — and reads/writes through a `SISGateway` interface. The engine never touches SIS table structure. Each university gets an adapter that maps its SIS to the canonical model; Keel's core never changes.

```
engine ─► SISGateway (interface)
            ├─ LocalPostgresSIS   (the demo: reads/writes local SIS-domain tables)
            └─ BannerAdapter / WorkdayAdapter / RestAdapter (production)
```

---

## 3. Authentication

The **university authenticates the student** (SSO: SAML/Shibboleth, CAS, Okta, Azure AD). Keel never logs the student in.

**Token mint — once, server-side (the trust bridge):**
```
student (SSO'd on portal) → portal backend ──signed assertion──► Keel /widget/token
                                                              → token { tenant_id, student_id, exp }
```
The browser can't be trusted to assert identity, so a trusted server (the portal backend) vouches. `student_id` comes from the server session, never a client field.

**Chat — every message, direct (portal not in path):**
```
widget ──token in header──► Keel chat router → RLS scope from token → response
```

- **Lazy mint:** the launcher icon loads with the page (no token); the token is minted when the student **opens the chat** (`/portal/keel-token`). Refresh silently if it expires.
- The **Keel token is widget-only.** Portal pages use the portal's own session.

---

## 4. Data ownership & integration

| Data | Owner | Read path | Write path |
|---|---|---|---|
| Catalog, prerequisites, programs | SIS | adapter **syncs** a tenant-scoped copy into Keel (cached, RLS) | — |
| Transcripts, GPA, holds | SIS | adapter sync (FERPA-aware) | — |
| Live seat counts | SIS | adapter **live-read** at decision time | — |
| Enrollment / drop / waitlist | SIS | — | adapter **write-through** to SIS; Keel records audit/outbox |
| Requests (grad app, petition, major-change) | SIS | adapter | agent **submits** via write-through; registrar approves in SIS |
| Plans, conversations, risk, RAG, config, audit, cost | Keel | Keel DB | Keel DB |

Integration is **bidirectional**: SIS→Keel sync for reads, Keel→SIS write-through for actions. When Keel registers a student, the **SIS updates** — it's the system of record.

---

## 5. Multi-tenancy (preserved after integration)

Isolation is enforced at **three** layers:

1. **RLS + `tenant_id`** on every Keel-owned row (and on synced SIS copies).
2. **Per-tenant adapter config** — one row per tenant in `sis_integration(tenant_id, adapter_type, base_url, vault_secret_ref)`; Keel resolves `tenant_id` → config → adapter instance at request time.
3. **Per-tenant credentials** in Vault — Tenant A's adapter can only reach Tenant A's SIS.

Not a separate adapter table per tenant — **one config table, one row per tenant.** Integration *adds* a tenant boundary; it never removes one. **Keel stays multi-tenant, guaranteed.**

**Demo:** one adapter (`LocalPostgresSIS`), all tenants, RLS on the SIS-domain tables. The `sis_integration` table is described here, not built.

---

## 6. Provenance ("via Keel")

A SaaS vendor never alters the customer's SIS schema. "via Keel" comes from:
1. **Keel's audit log** (canonical — Keel knows which writes it made), and
2. the SIS's **existing transaction-source field** (Keel writes through an authenticated service account, which the SIS stamps).

**Demo:** a `source` column (`keel`/`manual`/`sis`) on the SIS-domain enrollment row, set by the adapter on Keel writes. This models a field real SIS systems already have.

---

## 7. Mocked vs. real (the boundary, explicit)

| Concern | In production | In the capstone |
|---|---|---|
| Course catalog, prereqs, programs (structured) | SIS, registrar-managed | **seeded** SIS-domain tables |
| Sections, seats, capacity | SIS | seeded SIS-domain tables |
| Students, transcripts, holds | SIS | seeded (FERPA rationale) |
| Registration rules (caps, windows, holds) | SIS config | seeded SIS-domain |
| Enrollment / drop / requests (the write) | SIS via API | adapter writes local SIS-domain tables |
| University student/registrar portal | the university's own SIS portal | **mock SIS portal** (2 roles), read-only views of seed data + dead write buttons; functional request queue |
| Student authentication | university SSO | **student switcher** = SSO stand-in; server-side token mint |
| SIS integration | per-tenant adapters (sync + live + write-through) | one `LocalPostgresSIS` adapter; per-tenant config described, not built |
| Advising RAG corpus (prose) | Keel-curated | **Keel admin upload** (`catalog.md`, `policy.md`) |
| Plans, risk, agent, audit, cost, widget | Keel | Keel (real) |

**Real Keel features:** widget, Keel admin (agent config + RAG), engine/agent/predictions, RAG, audit/outbox/cost, the adapter interface.
**Mocked:** the SIS portal's own pages and SIS data (the system Keel integrates with, not builds).