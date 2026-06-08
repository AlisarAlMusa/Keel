# Feature Specification: Phase 0 — Foundation

**Feature Branch**: `001-phase-0-foundation`

**Created**: 2026-06-06

**Status**: Draft

**Input**: User description: "Phase 0 — Foundation (Day 1) for Keel. Scaffold the multi-tenant SaaS foundation: repo layout, containerized service stack, secrets-gated startup, tracing, baseline database with row-level isolation, seed data, and a green CI skeleton."

## Overview

Keel is a multi-tenant SaaS that a university deploys for its students: one conversational agent that plans courses, predicts whether a plan is wise, advises in plain language, and safely executes registration after approval. Before any of that intelligence can be built, the project needs a **foundation**: a clean, layered codebase; a one-command local stack of all supporting services; a database whose tenant isolation is enforced by the database itself; representative seed data; and continuous integration that proves the whole thing stands up.

This feature delivers that foundation only. It deliberately contains **no business intelligence** — no deterministic planning engine, no conversational agent, no trained models. Those arrive in later phases and depend on this scaffold existing and being trustworthy.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - One-command local stack stands up healthy (Priority: P1)

A developer clones the repository and brings the entire supporting stack up with a single command. Every service — the application API, the background worker, the model-serving service, the database, the cache, the object store, the secrets manager, and the experiment registry — reaches a healthy state without manual intervention.

**Why this priority**: Nothing else in the project can be built, tested, or demonstrated until the stack runs locally. This is the literal floor the entire capstone stands on, and the final demo requires "fresh clone → healthy" to work.

**Independent Test**: From a clean checkout, run the single bring-up command and confirm every service reports healthy. Delivers value on its own: any contributor can now run the system.

**Acceptance Scenarios**:

1. **Given** a fresh clone of the repository, **When** the developer runs the single stack bring-up command, **Then** all eight services (api, worker, model-server, database, cache, object store, secrets manager, experiment registry) reach a healthy state.
2. **Given** the stack is running, **When** the developer queries the API's health endpoint, **Then** it responds successfully and reports the application is ready.
3. **Given** the stack is running, **When** the developer stops and restarts it, **Then** previously stored data (database contents, object store contents) survives the restart.

---

### User Story 2 - The application refuses to run without its secrets (Priority: P1)

When the application starts, it obtains its secrets from the secrets manager. If the secrets manager is unreachable, the application refuses to boot rather than starting in an insecure or half-configured state.

**Why this priority**: A security-by-default posture is a graded, non-negotiable property of the project. "Fail closed on missing secrets" must be true from the very first day so that no later code can accidentally introduce a hardcoded-secret shortcut.

**Independent Test**: Start the application with the secrets manager available (boots) and again with it unreachable (refuses to boot with a clear error). Both outcomes are observable without any other feature.

**Acceptance Scenarios**:

1. **Given** the secrets manager is reachable and seeded, **When** the application starts, **Then** it boots successfully and loads its configuration from the secrets manager.
2. **Given** the secrets manager is unreachable, **When** the application attempts to start, **Then** it refuses to boot and emits a clear, non-sensitive error explaining why.
3. **Given** the application is running, **When** any log line or trace is emitted, **Then** it carries correlation identifiers (request/trace) and never contains a secret value.

---

### User Story 3 - Database is created with tenant isolation enforced by the database (Priority: P1)

Applying the baseline database migration to a clean database creates every foundational table and turns on row-level isolation for every table that holds tenant-owned data. Isolation is enforced by the database, not merely by application code.

**Why this priority**: Tenant isolation is described in the project as "the grade." It must be a property of the data layer from the first migration, so that every later table and query inherits a safe default rather than retrofitting isolation later.

**Independent Test**: Apply the migration to an empty database and confirm all baseline tables exist and that row-level isolation policies are active on every tenant-owned table. Verifiable directly against the database with no application running.

**Acceptance Scenarios**:

1. **Given** an empty database, **When** the baseline migration is applied, **Then** all sixteen baseline tables are created.
2. **Given** the baseline migration has been applied, **When** the database is inspected, **Then** row-level isolation is enabled and a tenant-scoping policy exists on every tenant-owned table.
3. **Given** the migration was applied, **When** it is rolled back, **Then** the schema returns to empty (the migration is reversible).

---

### User Story 4 - Representative seed data exists for development (Priority: P2)

A seed routine populates the database with two distinct tenants and, for each, a realistic catalog: at least twenty courses connected by prerequisite chains, scheduled sections, program requirements, and two student transcripts. The same catalog's descriptive text is placed in the object store so later phases can build retrieval over it.

**Why this priority**: Every later phase — the engine, the planner, retrieval, the agent — needs realistic, multi-tenant data to develop and test against. Two tenants specifically make cross-tenant isolation testable. It is P2 because the stack and schema (P1) must exist first.

**Independent Test**: Run the seed routine against a migrated database and confirm two tenants exist with the required counts of courses, prerequisite relationships, sections, and transcripts, and that catalog text is present in the object store.

**Acceptance Scenarios**:

1. **Given** a migrated empty database, **When** the seed routine runs, **Then** exactly two tenants exist, each with at least twenty courses, prerequisite chains, sections, program requirements, and two transcripts.
2. **Given** the seed routine has run, **When** the object store is inspected, **Then** catalog descriptive text for each tenant is present.
3. **Given** the seed routine is run a second time on an already-seeded database, **Then** it behaves predictably (either refuses or resets) without producing corrupt or duplicated-but-inconsistent data.

---

### User Story 5 - Continuous integration proves the foundation green (Priority: P2)

On every change proposed to the repository, an automated pipeline lints the code, type-checks it, builds the service images, and runs a smoke test that brings the stack up and confirms health. The pipeline passes (is green) for the foundation as delivered.

**Why this priority**: The project's grading model adds quality gates "the same day you build the thing." Establishing the CI skeleton now means every subsequent feature lands behind enforced checks rather than bolting CI on at the end. P2 because it validates P1 deliverables that must exist first.

**Independent Test**: Open a trivial change and confirm the pipeline runs lint, type-check, image build, and a stack smoke test, and reports green.

**Acceptance Scenarios**:

1. **Given** a proposed change, **When** the CI pipeline runs, **Then** it executes linting, type-checking, image builds, and a stack smoke test.
2. **Given** the foundation as delivered, **When** CI runs against it, **Then** all checks pass (green).
3. **Given** a change that violates lint or type rules, **When** CI runs, **Then** the pipeline fails and identifies the offending check.

---

### Edge Cases

- **Secrets manager flaps during boot**: If the secrets manager is reachable but returns an error or times out, the application treats it the same as unreachable and refuses to boot (fail closed).
- **Migration applied to a non-empty/partially-migrated database**: Applying the baseline migration is idempotent at the version level — re-running it does not duplicate tables or error confusingly; the migration tool tracks applied state.
- **One service in the stack is unhealthy**: If a dependency (e.g., database) is not yet healthy, dependent services wait for it rather than crash-looping, and the overall bring-up reports which service is unhealthy.
- **Seed run before migration**: Running seed against an unmigrated database fails with a clear message rather than partially writing.
- **Restart durability**: Stopping and restarting the stack must not lose persisted data; ephemeral state may be lost but durable stores must survive.
- **Tracing backend absent**: If the tracing/observability backend is not configured, the application still boots; tracing degrades gracefully rather than blocking startup.

## Requirements *(mandatory)*

### Functional Requirements

#### Codebase structure

- **FR-001**: The repository MUST be organized into clearly separated layers for API/transport, application services, data-access repositories, pure domain logic, infrastructure adapters, background workers, and agent code, each independently importable, with strict one-directional dependencies (transport → services → repositories → domain; infrastructure wired in, never imported upward).
- **FR-002**: The repository MUST contain dedicated locations for tests (unit, evaluation, integration), database migrations, the two frontend applications, and the separate model-serving service.
- **FR-003**: The repository MUST include a contributor-facing readme stub, an example environment-variable file documenting every required configuration value, and an ignore file that excludes secrets, build artifacts, and dependency caches from version control.

#### Packaging & build architecture

- **FR-004**: The system MUST be organized as exactly **two independent code packages**, each with its own dependency manifest, lockfile, and isolated environment: (a) the backend package that powers both the API container and the worker container, and (b) a lean model-serving package isolated from the backend's dependencies.
- **FR-005**: The model-serving package MUST NOT depend on heavyweight training libraries or the backend's data/agent libraries; it carries only what is needed to serve exported model artifacts.
- **FR-006**: The system MUST produce **three deployable container images** — API, worker, and model-server — where the API and worker images are built from the shared backend package (same code, different entrypoint) and the model-server image is built from its isolated package.
- **FR-007**: Each container image MUST have its own build definition; the API and worker images MUST share a single build context and a single context-exclusion file at the repository root, while the model-server image MUST have its own build definition and exclusion file within its own directory.
- **FR-008**: All dependency management MUST go through a single, consistent package-management tool; container builds MUST install dependencies from the committed lockfile in a reproducible, frozen manner; lockfiles MUST be committed for each of the two packages.

#### Service stack

- **FR-009**: A single command MUST bring up the full local stack: API, worker, model-server, relational database with vector support, cache, object store, secrets manager, and experiment registry.
- **FR-010**: Every service in the stack MUST expose a health signal, and the bring-up MUST be able to report each service as healthy; dependent services MUST wait for their dependencies to be healthy before starting.
- **FR-011**: Durable stores (database, object store) MUST persist their data across stack restarts; configuration MUST NOT hardcode environment-specific URLs but resolve them from environment.

#### Secrets-gated startup

- **FR-012**: On startup, the application MUST obtain its secrets from the secrets manager; if the secrets manager is unreachable or errors, the application MUST refuse to boot and report a clear, non-sensitive reason (fail closed).
- **FR-013**: The application MUST NOT contain hardcoded secret values and MUST NOT read secret values from anywhere other than the secrets manager at startup; non-secret configuration MUST be typed and validated, rejecting unknown configuration keys.

#### Observability

- **FR-014**: Distributed tracing/observability MUST be initialized during application startup so that requests can be traced end-to-end in later phases; absence of a tracing backend MUST degrade gracefully and not block startup.
- **FR-015**: All application logs MUST be structured, MUST carry correlation identifiers (request and trace) and the owning tenant where applicable, and MUST never contain secret or sensitive values.

#### Baseline data model & isolation

- **FR-016**: A baseline migration MUST create all sixteen foundational tables: tenants, users, students, courses, prerequisites, corequisites, sections, program requirements, student transcript entries, plans, enrollments, waitlist, request queue, outbox, audit log, and notifications.
- **FR-017**: Every table that holds tenant-owned data MUST have row-level isolation enabled with a tenant-scoping policy enforced by the database, so that isolation does not depend on application code alone.
- **FR-018**: The baseline migration MUST be reversible and version-tracked, so applying it to a clean database creates the full schema and rolling it back returns the database to empty.
- **FR-019**: The schema MUST support the project's later needs by including: a per-tenant identifier on every tenant-owned table, vector-search capability in the database, and the structures required by the write/notify pattern (an outbox table and an audit-log table).

#### Seed data

- **FR-020**: A seed routine MUST populate two distinct tenants, each with at least twenty courses connected by prerequisite chains, scheduled sections, program requirements, and two student transcripts, all correctly tenant-scoped.
- **FR-021**: The seed routine MUST place each tenant's catalog descriptive text into the object store so later retrieval features can index it.
- **FR-022**: The seed routine MUST behave predictably when run against an already-seeded or unmigrated database (clear failure or deterministic reset), never producing inconsistent partial data.

#### Continuous integration

- **FR-023**: An automated pipeline MUST run on every proposed change and MUST, at minimum, lint the code, type-check it, build the service images, and run a smoke test that brings the stack up and confirms health.
- **FR-024**: The pipeline MUST pass (green) for the foundation as delivered and MUST fail with an identifiable cause when a check (lint, type, build, or smoke) does not pass.

#### Decision record

- **FR-025**: A decisions record MUST be started capturing the foundational, defensible choices made in this phase (at minimum: the two-package / three-image build architecture and the database-enforced isolation approach), so later contributors understand why.

### Key Entities *(include if feature involves data)*

- **Tenant**: A university deploying Keel; the isolation boundary. Every tenant-owned record belongs to exactly one tenant.
- **User**: An authenticated person within a tenant (e.g., registrar/admin or student-linked identity).
- **Student**: A student within a tenant, associated with a program and a credit cap.
- **Course**: A catalog course within a tenant (code, name, credits, difficulty, offered terms).
- **Prerequisite / Corequisite**: Directed relationships between courses within a tenant.
- **Section**: A scheduled offering of a course (term, year, meeting times, capacity, enrolled count).
- **Program Requirement**: A requirement group satisfied by credits/courses, per program, per tenant.
- **Student Transcript Entry**: A student's completed/in-progress course with grade and pass status.
- **Plan**: A versioned, student-owned set of planned courses, at most one active per student.
- **Enrollment / Waitlist**: A student's registration or waitlist position in a section.
- **Request Queue Item**: An institutional request (e.g., petition, major change, graduation application) awaiting human resolution.
- **Outbox Event**: A pending notification/email recorded atomically with a write, for reliable delivery.
- **Audit Log Entry**: An immutable record of who did what, when, in which tenant.
- **Notification**: A message destined for a user.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: From a fresh clone, a single command brings all eight services to a healthy state with zero manual configuration steps beyond providing the documented environment file.
- **SC-002**: With the secrets manager reachable the application boots; with it unreachable the application refuses to boot 100% of the time and never starts in a degraded state.
- **SC-003**: Applying the baseline migration to an empty database creates all 16 tables and enables row-level isolation on 100% of tenant-owned tables; rolling it back returns the database to empty.
- **SC-004**: After seeding, the database contains exactly 2 tenants, each with ≥20 courses, prerequisite chains, sections, program requirements, and 2 transcripts, and the object store contains catalog text for each tenant.
- **SC-005**: The CI pipeline runs lint, type-check, image build, and a stack smoke test on every proposed change and reports green for the delivered foundation; an intentional lint or type violation makes it fail with an identifiable cause.
- **SC-006**: No secret value appears anywhere in version control, logs, or traces (verified by inspection); all configuration values required to run are documented in the example environment file.
- **SC-007**: Stopping and restarting the stack preserves 100% of data in durable stores.
- **SC-008**: The codebase enforces the layered dependency direction — no upward imports from a lower layer to a higher one exist in the delivered scaffold.

## Assumptions

- **Scope boundary**: This phase delivers scaffolding only. The deterministic engine, the conversational agent, trained models, retrieval, guardrails, and frontends are explicitly out of scope and arrive in later phases; the app and model-server may be "empty but healthy."
- **Two-package / three-image architecture**: The backend (API + worker) is a single shared code package; the model-server is a separate isolated package. This is a deliberate choice — API and worker run identical code with different entrypoints, so duplicating their dependencies would add cost without benefit, while the model-server must stay lean and isolated from heavyweight libraries.
- **Local-first**: The target environment for Phase 0 is local developer machines and CI runners using the containerized stack; cloud deployment is out of scope for this phase.
- **Single database instance**: One relational database instance with vector support and row-level isolation serves all tenants for the foreseeable MVP scale; sharding/schema-per-tenant is a documented future concern, not built now.
- **Secrets in development**: A development secrets manager is bootstrapped with placeholder secrets so the stack runs locally; production secret provisioning is out of scope for this phase.
- **Seed data may be illustrative**: Seed catalogs and transcripts are representative/synthetic and exist to enable development and testing, not to model any real institution.
- **CI provider**: A standard hosted CI runner capable of building images and running the containerized stack is available.
