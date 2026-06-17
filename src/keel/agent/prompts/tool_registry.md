# Tool Registry
#
# NOTE: This file is for human reference ONLY.
# It is NOT injected into the LLM context — tool descriptions that the LLM
# sees live as docstrings on the @tool functions in src/keel/agent/tools/.
# The Pydantic field descriptions (Field(description=...)) on each input schema
# are what appear in the LLM's parameter schema at call time.
# Edit this file when you add, change, or remove a tool so the team has a
# single place to understand the full tool surface without reading code.

---

## advising tools (read-only)

### audit_degree
- **When to call**: Student asks about progress, completed courses, eligible courses, credits remaining, graduation timeline.
- **What it returns**: Completed requirements, remaining credits, list of courses currently eligible to take (engine-computed, deterministic).
- **What it does NOT do**: It does not propose a plan or predict risk.
- **Source**: Engine audit — no LLM involved.

### rag_search
- **When to call**: ANY question about prerequisites, course descriptions, academic policies, registration deadlines, or degree requirements. Always before answering factual university questions.
- **What it returns**: Relevant passages from the university's catalog and policy documents, reranked by Cohere.
- **What it does NOT do**: It does not check student eligibility or enrollment status.
- **Source**: pgvector + Cohere rerank.

### predict_risk
- **When to call**: After propose_plan, to score a specific proposed course list. Can also be called directly with a user-specified list.
- **What it returns**: on_track / at_risk label + confidence score + deterministic reasons (threshold-based, not LLM) + LLM-written mitigation suggestions.
- **What it does NOT do**: It does not decide feasibility — that is the engine's job.
- **Source**: ONNX graduation-risk model (model-server) + deterministic feature thresholds.

### gpa_estimate
- **When to call**: Student explicitly asks for a GPA estimate. Hard-caveated — never for planning decisions.
- **What it returns**: LLM-generated estimate only. Not a prediction. Always includes a caveat.
- **What it does NOT do**: Does not gate feasibility. Not authoritative.
- **Source**: LLM only (no model).

---

## planning tools (read-only writes to plans table, no approval gate)

### propose_plan
- **When to call**: Student asks to plan courses for a term.
- **What it returns**: Up to 3 engine-verified candidates (balanced / graduation-focused / lighter), each with workload band and risk score, LLM-ranked.
- **What it does NOT do**: Does not enroll the student. Does not return invalid plans.
- **Source**: Engine (eligible pool + verify + repair loop) + model-server risk scores + LLM ranking.

### simulate_whatif
- **When to call**: Student asks "what if I had already completed X?" — hypothetical degree audit.
- **What it returns**: A read-only explanation of what the degree audit would look like with those courses completed.
- **What it does NOT do**: Does not change the transcript. Not a real plan.

### save_plan
- **When to call**: Student wants to save a course list as a plan.
- **What it returns**: plan_id of the saved, engine-verified plan.
- **What it does NOT do**: Does not enroll. Does not activate the plan.

### load_plan
- **When to call**: Student asks to view a previously saved plan.
- **What it returns**: Plan terms + courses. Re-validates if catalog changed.

### activate_plan
- **When to call**: Student wants to mark a plan as their active plan.
- **What it returns**: Confirmation. Only one active plan per student (partial unique index).

### swap_course
- **When to call**: Student wants to replace one course in a saved plan with another.
- **What it returns**: Confirmation + re-verification result. Idempotent.

---

## enrollment tools (write-path — require student approval)

### stage_enrollment
- **When to call**: Student says "enroll me", "register me", "sign me up" for specific section(s).
- **What it returns**: action_id of the pending action. Graph suspends; student must approve.
- **Safety**: Validates capacity NOW before staging. Execute node re-validates before writing. Frozen payload — LLM cannot swap section IDs after staging.

### stage_waitlist_join
- **When to call**: Student wants to join the waitlist for a full section.
- **What it returns**: action_id + position estimate + consent note about auto_enroll.
- **Safety**: Same approval gate as enrollment. auto_enroll=True means one approval covers future seat-fill (re-verified at fill time).

### stage_waitlist_leave
- **When to call**: Student wants to leave a waitlist.
- **What it returns**: action_id of the pending removal. Requires approval.
