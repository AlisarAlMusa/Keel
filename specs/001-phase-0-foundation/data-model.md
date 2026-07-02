# Data Model: Phase 0 — Foundation

The baseline schema delivered by `migrations/versions/0001_baseline.py`. Sixteen tables. **Row-Level Security is enabled and forced on every tenant-owned table.** Domain value objects (`domain/models.py`, Pydantic v2, per `docs/SPEC.md` §1) are the in-code vocabulary; the ORM models (`infra/orm.py`) map to these tables; repositories translate between them.

## Conventions

- **PK**: `id UUID DEFAULT gen_random_uuid()` (via `pgcrypto`/`gen_random_uuid`), unless a natural composite key is noted.
- **Tenant column**: every tenant-owned table has `tenant_id UUID NOT NULL REFERENCES tenants(id)`.
- **Timestamps**: `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`; `updated_at` where mutated.
- **Enums**: stored as `TEXT` with `CHECK` constraints (string enums in domain) to keep migrations simple and avoid Postgres ENUM migration friction.
- **Credits**: `INTEGER`. **GPA/grade**: `NUMERIC(3,2)`. **Money**: never float (none in Phase 0).
- **Time**: section meeting times stored as `JSONB` list of `{day, start_min, end_min}` (matches `TimeSlot`).

## RLS pattern (applied to every tenant-owned table)

```sql
ALTER TABLE <t> ENABLE ROW LEVEL SECURITY;
ALTER TABLE <t> FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON <t>
  USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
```

- The app connects as a **non-superuser** role (`keel_app`) so policies are enforced (`FORCE` also covers the table owner).
- Each request/worker job runs `SET LOCAL app.tenant_id = '<uuid>'` inside its transaction; `current_setting(..., true)` returns NULL when unset → no rows visible (fail closed).
- `tenants` itself is **not** tenant-owned (it is the tenant registry) — no RLS; access restricted to platform-operator paths.

## Tables

### 1. `tenants` *(not tenant-owned — no RLS)*
| col | type | notes |
|-----|------|-------|
| id | UUID PK | |
| slug | TEXT UNIQUE NOT NULL | e.g. `northane` |
| name | TEXT NOT NULL | |
| status | TEXT NOT NULL DEFAULT 'active' | CHECK in ('active','suspended','erased') |
| widget_origin_allowlist | JSONB NOT NULL DEFAULT '[]' | origins for widget token (Phase 5) |
| created_at | TIMESTAMPTZ | |

### 2. `users` *(tenant-owned, RLS)*
| col | type | notes |
|-----|------|-------|
| id | UUID PK | |
| tenant_id | UUID FK→tenants | |
| email | TEXT NOT NULL | unique per tenant: `UNIQUE(tenant_id, email)` |
| role | TEXT NOT NULL | CHECK in ('admin','student') |
| display_name | TEXT | |
| created_at | TIMESTAMPTZ | |

### 3. `students` *(tenant-owned, RLS)*
| col | type | notes |
|-----|------|-------|
| id | UUID PK | |
| tenant_id | UUID FK→tenants | |
| user_id | UUID FK→users NULL | links to auth identity |
| program_code | TEXT NOT NULL | |
| max_credits_per_term | INTEGER NOT NULL DEFAULT 18 | credit cap |
| current_term | TEXT NOT NULL | CHECK in ('fall','spring','summer') |
| current_year | INTEGER NOT NULL | |
| created_at | TIMESTAMPTZ | |

### 4. `courses` *(tenant-owned, RLS)*
| col | type | notes |
|-----|------|-------|
| id | UUID PK | |
| tenant_id | UUID FK→tenants | |
| code | TEXT NOT NULL | `UNIQUE(tenant_id, code)` |
| name | TEXT NOT NULL | |
| credits | INTEGER NOT NULL | CHECK > 0 |
| difficulty | INTEGER NOT NULL | CHECK between 1 and 5 |
| offered_terms | JSONB NOT NULL DEFAULT '[]' | subset of terms |
| description | TEXT | source for RAG corpus |
| created_at | TIMESTAMPTZ | |

### 5. `prerequisites` *(tenant-owned, RLS)*
| col | type | notes |
|-----|------|-------|
| id | UUID PK | |
| tenant_id | UUID FK→tenants | |
| course_code | TEXT NOT NULL | the dependent course |
| requires_code | TEXT NOT NULL | must precede |
| min_grade | NUMERIC(3,2) NULL | optional grade floor |
| | | `UNIQUE(tenant_id, course_code, requires_code)` |

### 6. `corequisites` *(tenant-owned, RLS)*
| col | type | notes |
|-----|------|-------|
| id | UUID PK | |
| tenant_id | UUID FK→tenants | |
| course_code | TEXT NOT NULL | |
| coreq_code | TEXT NOT NULL | same term or earlier |
| | | `UNIQUE(tenant_id, course_code, coreq_code)` |

### 7. `sections` *(tenant-owned, RLS)*
| col | type | notes |
|-----|------|-------|
| id | UUID PK | |
| tenant_id | UUID FK→tenants | |
| course_code | TEXT NOT NULL | |
| term | TEXT NOT NULL | CHECK in terms |
| year | INTEGER NOT NULL | |
| slots | JSONB NOT NULL DEFAULT '[]' | list of TimeSlot |
| capacity | INTEGER NOT NULL | CHECK >= 0 |
| enrolled | INTEGER NOT NULL DEFAULT 0 | CHECK 0..capacity |
| created_at | TIMESTAMPTZ | |

### 8. `program_requirements` *(tenant-owned, RLS)*
| col | type | notes |
|-----|------|-------|
| id | UUID PK | |
| tenant_id | UUID FK→tenants | |
| program_code | TEXT NOT NULL | |
| group_name | TEXT NOT NULL | e.g. 'Core' |
| required_credits | INTEGER NOT NULL | |
| eligible_course_codes | JSONB NOT NULL DEFAULT '[]' | course codes satisfying group |

### 9. `student_transcript` *(tenant-owned, RLS)*
| col | type | notes |
|-----|------|-------|
| id | UUID PK | |
| tenant_id | UUID FK→tenants | |
| student_id | UUID FK→students | |
| course_code | TEXT NOT NULL | |
| term | TEXT NOT NULL | |
| year | INTEGER NOT NULL | |
| grade | NUMERIC(3,2) NULL | NULL = in progress |
| passed | BOOLEAN NOT NULL DEFAULT false | |

### 10. `plans` *(tenant-owned, RLS)*
| col | type | notes |
|-----|------|-------|
| id | UUID PK | |
| tenant_id | UUID FK→tenants | |
| student_id | UUID FK→students | |
| name | TEXT NOT NULL | |
| version | INTEGER NOT NULL DEFAULT 1 | monotonic per (student,name) |
| status | TEXT NOT NULL DEFAULT 'draft' | CHECK in ('draft','active','archived','stale') |
| plan_data | JSONB NOT NULL DEFAULT '{}' | list of PlannedCourse |
| validated_at | TIMESTAMPTZ NULL | last verifier-valid time |
| created_at | TIMESTAMPTZ | |
| | | partial unique: at most one `active` per (tenant_id, student_id) via partial unique index |

### 11. `enrollments` *(tenant-owned, RLS)*
| col | type | notes |
|-----|------|-------|
| id | UUID PK | |
| tenant_id | UUID FK→tenants | |
| student_id | UUID FK→students | |
| section_id | UUID FK→sections | |
| status | TEXT NOT NULL DEFAULT 'enrolled' | CHECK in ('enrolled','dropped') |
| idempotency_key | TEXT NOT NULL | `UNIQUE(tenant_id, idempotency_key)` |
| created_at | TIMESTAMPTZ | |

### 12. `waitlist` *(tenant-owned, RLS)*
| col | type | notes |
|-----|------|-------|
| id | UUID PK | |
| tenant_id | UUID FK→tenants | |
| student_id | UUID FK→students | |
| section_id | UUID FK→sections | |
| position | INTEGER NOT NULL | |
| created_at | TIMESTAMPTZ | |

### 13. `request_queue` *(tenant-owned, RLS)*
| col | type | notes |
|-----|------|-------|
| id | UUID PK | |
| tenant_id | UUID FK→tenants | |
| student_id | UUID FK→students | |
| type | TEXT NOT NULL | CHECK in ('petition','major_change','graduation') |
| payload | JSONB NOT NULL DEFAULT '{}' | |
| status | TEXT NOT NULL DEFAULT 'pending' | CHECK in ('pending','approved','rejected') |
| created_at | TIMESTAMPTZ | |
| resolved_at | TIMESTAMPTZ NULL | |

### 14. `outbox` *(tenant-owned, RLS)*
| col | type | notes |
|-----|------|-------|
| id | UUID PK | |
| tenant_id | UUID FK→tenants | |
| kind | TEXT NOT NULL | CHECK in ('email','notification') |
| payload | JSONB NOT NULL DEFAULT '{}' | |
| published_at | TIMESTAMPTZ NULL | NULL = unpublished |
| created_at | TIMESTAMPTZ | |

### 15. `audit_log` *(tenant-owned, RLS)*
| col | type | notes |
|-----|------|-------|
| id | UUID PK | |
| tenant_id | UUID FK→tenants | |
| actor | TEXT NOT NULL | who (user id / 'system') |
| action | TEXT NOT NULL | |
| before | JSONB NULL | |
| after | JSONB NULL | |
| created_at | TIMESTAMPTZ | append-only |

### 16. `notifications` *(tenant-owned, RLS)*
| col | type | notes |
|-----|------|-------|
| id | UUID PK | |
| tenant_id | UUID FK→tenants | |
| student_id | UUID FK→students | |
| kind | TEXT NOT NULL | e.g. 'seat_open' |
| body | TEXT NOT NULL | |
| read_at | TIMESTAMPTZ NULL | |
| created_at | TIMESTAMPTZ | |

## pgvector

The `vector` extension is created in the baseline migration (`CREATE EXTENSION IF NOT EXISTS vector`). The RAG corpus table (with an `embedding vector(N)` column, tenant-tagged) is **deferred to Phase 2** when retrieval is built — Phase 0 only guarantees the extension is available so later migrations can add the column without surprises.

## Tenant-owned tables (15 of 16 get RLS)

`users, students, courses, prerequisites, corequisites, sections, program_requirements, student_transcript, plans, enrollments, waitlist, request_queue, outbox, audit_log, notifications`.

`tenants` is the only non-tenant-owned table (the registry itself).

## Migration acceptance

- `alembic upgrade head` on a clean DB → all 16 tables exist; `vector` + `pgcrypto` extensions present; RLS enabled+forced and a `tenant_isolation` policy present on all 15 tenant-owned tables.
- `alembic downgrade base` → schema empty (reversible).
- Verified in CI smoke by querying `pg_tables` count and `pg_policies` for the 15 policies.
