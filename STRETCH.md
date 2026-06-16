# Keel — STRETCH (deferred by design)

> Trade-off log for ideas we chose **not** to build now. Each entry states what it
> is, why it's deferred, the trade-off, and what would trigger picking it up. We
> document trade-offs so a deferral is a recorded decision, not an accident.

## Admin catalog editing

**Now:** the admin console can upload/edit **policy** docs — live ingest (chunk →
embed → upsert) through the reusable ingestion service. Course catalogs are
read-only, set by the seed.

**Deferred:** letting the admin edit the **course catalog** through the console.

**Why:** a course catalog is part of the **deterministic engine**, not just RAG
content. One catalog edit ripples through:
- Course DB rows
- the prerequisite DAG (and its cycle check)
- the degree audit
- planning & registration eligibility
- saved-plan validation + automatic replanning (every saved plan must be re-checked)
- cache invalidation (snapshot + retrieval + catalog caches)

**Trade-off:** a policy upload is pure RAG advisory with no engine impact, so it is
safe and cheap. Catalog editing would need an atomic, transactional update across
all of the above plus a re-validation sweep of every saved plan — high complexity
for low near-term value. The ingestion service is built callable, so the catalog
trigger can be added later without a rewrite.

**Trigger to build:** when institutions need self-serve catalog management and the
re-validation / replanning path is hardened.

## Guardrails sidecar (NeMo Guardrails)

**Now:** in-process rails (`infra/guardrails.py`) — injection + cross-tenant refusal
on input, `redact()` on every egress (response, logs, traces). Hardcoded; tenant
config cannot weaken them.

**Deferred:** a separate **NeMo Guardrails sidecar** for topical + injection rails.

**Trade-off:** in-process is one less service, easy to test, and the real tenant
boundary is the DB filter anyway. A sidecar adds config-driven, per-tenant rails and
defense-in-depth, but also a service, extra latency, and ops surface.

**Trigger to build:** when rails must be config-driven per tenant, or the rule set
outgrows in-process patterns.

## Carried from the capstone brief (STRETCH tier)

- **Multilingual support** (Arabic / French / English) — prompt + UI only; the
  engine and predictions are language-agnostic.
- **Career roadmap saved as a named plan** (E2 → A4).
- **Per-route thresholds** for write-capable intents — one global router threshold
  is fine for MVP.