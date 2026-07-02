# AI Engineering Bootcamp — Engineering Standards & Code Review Rules

> Compiled from: Engineering Standards Guide, Week 3 & Week 4 Code Review Lessons Learned.  
> Use this as a context source for Claude Code. Every rule here reflects what code reviewers will check and what production codebases require.

---

## 1. Async All the Way Down

**Rule:** Every I/O operation in a request path must be async. No exceptions.

- Use `httpx.AsyncClient` for HTTP — never `requests` (blocking, no async version).
- Use `asyncio.gather()` to run independent async calls in parallel, not sequentially.
- Use `await asyncio.sleep()` — never `time.sleep()` (blocks the event loop).
- Use async SQLAlchemy 2.x or `asyncpg` for database calls.
- Push CPU-bound work (heavy ML inference, large file parsing) to `asyncio.to_thread()` or a worker queue — the event loop cannot help with CPU work.
- A route declared `async` that calls blocking libraries is a synchronous server lying about being async.

```python
# WRONG
@app.post("/plan-trip")
async def plan_trip(query: str):
    weather = requests.get(WEATHER_URL).json()  # blocks event loop

# RIGHT
@app.post("/plan-trip")
async def plan_trip(query: str):
    async with httpx.AsyncClient() as http:
        weather, flights = await asyncio.gather(
            http.get(WEATHER_URL),
            http.get(FLIGHTS_URL),
        )
```

---

## 2. Dependency Injection with `Depends()`

**Rule:** Every shared resource a route needs must be declared as a `Depends()` parameter — never constructed inside the route body.

- Database sessions, the current user, LLM clients, and the agent executor are all dependencies.
- Dependencies can depend on other dependencies (e.g., `get_current_user` depends on `get_session`).
- Use `yield` in a dependency to scope a resource to the request and guarantee cleanup even on exceptions.
- Never call `SessionLocal()` or construct an LLM client inside a route function.
- In tests, use `app.dependency_overrides[dep] = lambda: FakeDep()` — no monkey-patching needed.

```python
# dependencies.py
async def get_session() -> AsyncSession:
    async with SessionLocal() as session:
        yield session  # closes automatically after request, even on exception

async def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_session),
) -> User:
    return await load_user_from_token(token, session)

# routes.py
@app.post("/plan-trip")
async def plan_trip(
    query: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    ...
```

---

## 3. Singletons via Lifespan

**Rule:** Expensive per-process objects (DB engine, ML models, embedding models, LLM clients, HTTP client pools) must be created once in `lifespan`, stored on `app.state`, and exposed via dependencies.

- Never load models or create engines at module import time — it breaks tests and crashes if config is missing.
- Never load models or create clients inside request handlers — catastrophic latency.
- Dispose resources cleanly on shutdown (after `yield`).

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    app.state.model = joblib.load(settings.MODEL_PATH)
    app.state.embedder = SentenceTransformer(settings.EMBEDDING_MODEL)
    app.state.llm = AsyncOpenAI(api_key=settings.OPENAI_KEY)
    app.state.engine = create_async_engine(settings.DATABASE_URL)
    yield
    # shutdown
    await app.state.engine.dispose()

app = FastAPI(lifespan=lifespan)

# dependencies.py
def get_model(request: Request):
    return request.app.state.model
```

**Scoping rules:**
- **Per-process (lifespan):** DB engine, ML models, embedders, LLM clients, HTTP pools, agent executor.
- **Per-request (yield in dependency):** DB session, transaction, current user.
- **Per-call (no caching):** anything computed from input data.

---

## 4. Caching

**Rule:** Use the right cache for the right use case. Wrong caching serves stale data and creates hard-to-debug bugs.

### `lru_cache` — deterministic, in-process, pure functions
- Use for: settings loaders, model path resolvers, anything pure and expensive to compute.
- Do **not** use for: mutable arguments, time-dependent functions, anything that should expire.

```python
from functools import lru_cache

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
```

### TTL Cache — external responses that go stale
- Use `cachetools.TTLCache` with an explicit TTL.
- Always use a `Lock` to prevent thundering herd (100 simultaneous cache misses → 1 API call, not 100).
- Document the TTL choice — it is a deliberate decision.

```python
from cachetools import TTLCache
from asyncio import Lock

weather_cache = TTLCache(maxsize=500, ttl=600)  # 10 minutes
weather_lock = Lock()

async def get_weather(city: str) -> dict:
    if city in weather_cache:
        return weather_cache[city]
    async with weather_lock:
        if city in weather_cache:  # double-check inside lock
            return weather_cache[city]
        result = await fetch_weather_from_api(city)
        weather_cache[city] = result
        return result
```

**Decision tree:** pure + small input set → `lru_cache` | stable for a time window → `TTLCache` | must survive restarts / shared across replicas → Redis | high cost of stale data → don't cache.

---

## 5. Configuration — `pydantic-settings`

**Rule:** All config lives in a single `Settings` class. No `os.getenv()` calls scattered through the codebase.

- Required values use `Field(...)` — app refuses to start if they're missing.
- `extra="forbid"` — a typo in `.env` (e.g. `OPNAI_KEY`) raises an error at startup instead of silently setting `None`.
- `lru_cache` on the `get_settings()` loader makes it a singleton.
- In tests, construct `Settings(openai_key="test", ...)` directly — no env vars needed.

```python
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
    )
    openai_key: str = Field(..., min_length=1)
    database_url: str
    cheap_model: str = "gpt-4o-mini"
    strong_model: str = "gpt-4o"
    embedding_model: str = "all-MiniLM-L6-v2"
    weather_cache_ttl: int = 600

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
```

---

## 6. Types, Pydantic, and Boundaries

**Rule:** Validate external data at the boundary (entry point) once. Trust types inside the system. No defensive checks inside business logic.

- HTTP request bodies → Pydantic model on the FastAPI route.
- Agent tool inputs → Pydantic model as `args_schema` on each tool.
- LLM structured outputs → Pydantic model for parsing.
- Webhook payloads → Pydantic model.
- Database rows → SQLAlchemy models; convert to Pydantic at the API boundary.

```python
class DestinationProfile(BaseModel):
    climate: Literal["tropical", "temperate", "cold", "arid"]
    cost_index: float = Field(..., ge=0, le=100)
    top_activities: list[str] = Field(..., min_length=1, max_length=10)
    region: str

# Function trusts its inputs — no defensive isinstance checks
def classify_destination(profile: DestinationProfile) -> ClassificationResult:
    features = profile_to_features(profile)
    ...
```

---

## 7. Errors, Retries, and Failure Isolation

**Rule:** Every external call needs all three layers: timeout, retry with backoff, and structured error return from tools.

### Layer 1 — Timeouts
```python
async with httpx.AsyncClient(timeout=10.0) as client:
    response = await client.get(WEATHER_URL)
```

### Layer 2 — Retries with Exponential Backoff (use `tenacity`)
- Retry only transient errors (network, timeout) — never retry 4xx responses.
- Always set a maximum attempt count.

```python
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    reraise=True,
)
async def fetch_weather(city: str) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(WEATHER_URL, params={"city": city})
        r.raise_for_status()
        return r.json()
```

### Layer 3 — Structured Error Returns from Agent Tools
- Don't let tool failures crash the agent. Return a structured error the LLM can reason about.

```python
class ToolError(BaseModel):
    error: str
    retryable: bool

async def live_conditions(city: str) -> dict | ToolError:
    try:
        return await fetch_weather(city)
    except httpx.HTTPStatusError as e:
        return ToolError(error=f"weather API returned {e.response.status_code}", retryable=False)
    except (httpx.TimeoutException, httpx.NetworkError) as e:
        return ToolError(error=f"weather API unreachable: {e}", retryable=True)
```

**Don'ts:** Don't catch broad `Exception` and swallow it. Don't retry 4xx. Don't retry without a max attempt count. Don't let a webhook failure break the user-facing response.

---

## 8. Code Hygiene

### Project Structure
Organize by concern. Someone new should find the agent code in under 5 seconds.

```
app/
  main.py           # FastAPI app, lifespan, mount routers
  config.py         # Settings class
  dependencies.py   # shared Depends() functions
  routes/
    auth.py
    trips.py
  services/
    agent.py        # LangGraph agent assembly
    rag.py
    classifier.py
  tools/
    rag_search.py
    live_conditions.py
  models/
    db.py           # SQLAlchemy models
    schemas.py      # Pydantic request/response schemas
  db/
    session.py
    migrations/     # Alembic
tests/
```

- One file = one clear responsibility. If you can't describe a file's job in one sentence, split it.
- Meaningful names everywhere: `chunk_tweets_for_embedding()` not `process_data()`.
- Split prompts into separate files by purpose: `priority_prompt.py`, `rag_prompt.py`, not one `prompts.py`.
- Never hardcode environment-specific URLs — use env vars with sensible fallbacks.

### Logging
- Never use `print()` in production code. Use structured logging (`structlog` or stdlib `logging`).
- Logs must go to a persistent file, not just stdout (stdout is gone when the container restarts).
- Each log line should be a JSON object with named fields — searchable and filterable.

```python
import structlog
log = structlog.get_logger()

async def run_agent(query: str, user_id: int):
    log.info("agent.run.start", user_id=user_id, query_length=len(query))
    try:
        result = await agent.ainvoke({"input": query})
        log.info("agent.run.success", user_id=user_id)
        return result
    except Exception as e:
        log.exception("agent.run.failure", user_id=user_id, error=str(e))
        raise
```

### Tooling
- Use `uv` instead of `pip` — faster, handles lockfiles, reproducible environments.
- Set up `ruff` (or `flake8` + `black`) in pre-commit. Configure once, stop arguing about whitespace.
- README = architecture diagram + how to run locally + required config + where the interesting code is.

---

## 9. Authentication & Authorization

**Rule:** Every endpoint with user-scoped data must be protected. Auth layer is not optional.

### Tokens
- A JWT carries a small payload (user id + expiry) signed with a secret. Signature = tamper-proof. Payload = base64, not encrypted. **Never put sensitive data inside a JWT.**
- Tokens travel in the `Authorization` HTTP header with the `Bearer` scheme: `Authorization: Bearer <token>`.
- Access tokens are short-lived. Refresh tokens are long-lived and exchanged for new access tokens.

### Status Codes
- **401 Unauthenticated** — no token, expired token, bad signature. We don't know who you are.
- **403 Forbidden** — we know who you are, and you're not allowed. Returning the wrong one is a bug that signals copied auth code.

### In FastAPI
- Every protected endpoint must have a `Depends()` that validates the token.
- Be able to point at the exact route dependency that protects each endpoint and the function that issues tokens at login.

---

## 10. Database Persistence

**Rule:** Persistence is a sequence of specific calls, not a vibe. Be able to trace every write end-to-end.

### Registration trace (every step must be accountable)
1. Route receives request.
2. Pydantic model validates it.
3. Service/CRUD function builds the ORM object.
4. `session.add()` and `session.commit()` — know the exact line.
5. Response model hides sensitive fields on the way out.

- If data only exists in a Python list/dict, it's in memory — restart the container and it's gone.
- Verify with pgAdmin, DBeaver, TablePlus, or `psql` — "the API returned 200" ≠ "the row is in the table."

### SQLAlchemy + Alembic
- **SQLAlchemy** = ORM — query and write with Python objects instead of raw SQL.
- **Alembic** = migration tool — tracks schema versions. `upgrade()` applies, `downgrade()` reverts.
- Every schema change needs a committed Alembic migration. "I deleted the volume" is not a migration strategy.
- Every ORM class maps to one table via `__tablename__`. Know which table each class lives in.
- Model real-world relationships: `user_id` foreign key on `AgentRun`, `relationship()` on `User`.

---

## 11. Agent Architecture

**Rule:** "LangGraph handles it" is not an answer. Know the wiring.

### Tools
- Each tool has: a clear name, a docstring, and a typed `args_schema` (Pydantic model).
- The docstring is what the LLM reads to decide whether to use the tool. Vague docstrings → wrong tool picks.
- `args_schema` = contract for arguments. `coroutine` = the async function that runs. Two different things.
- Defining a tool ≠ giving it to the agent. Point to the exact line where tools are registered: `create_react_agent(..., tools=[...])` or `ToolNode(tools)`.

### LangGraph nodes
- **LLM node** — decides what to do next (call a tool or finish).
- **Tool node** — executes the chosen tool with the LLM's arguments and returns the result to state.
- If your graph only has an LLM node, your tools are decorative.

### Prompts
- Prompts belong in source control, not pasted into a chat window.
- Version them, review them, be ready to defend the wording.
- Know how your prompts are templated with user input.

### Tracing (LangSmith)
- Wire every tool and chain into LangSmith so each agent run is a tree of timed, inspectable steps.
- `@traceable` decorator = per-function hook that records the call as a span.
- When something goes wrong (wrong tool picked, hallucinated arguments, infinite loop) — the LangSmith trace is where you find it. `print()` will not save you.

---

## 12. API Contracts — Don't Leak Your Database

**Rule:** Define separate output shapes for each endpoint. Never expose internal fields to the client.

- **DTO / `response_model`** — the output Pydantic model for an endpoint must differ from the ORM model where the DB has more fields than the client should see (password hashes, internal flags, soft-delete timestamps).
- If `/users/me` returns the password hash, that's a leak.

### Streaming (SSE)
- A streaming response sends tokens as they're generated over an open HTTP connection (server-sent events).
- Know the difference from a buffered response and when to use each.

### `Generic[T]`
- Lets you write one container class for many types: `PaginatedResponse[User]`, `PaginatedResponse[AgentRun]`.
- If you used it, be ready to explain why one generic class was better than two separate classes.

---

## 13. RAG-Specific Rules

- Know your vector store type: filesystem (Chroma) ≠ in-memory ≠ server-based (pgvector, Qdrant, Pinecone). Filesystem is fine for prototyping; not for production.
- Know exactly what metadata you persist alongside each vector.
- Be able to narrate the full pipeline: **ingest:** load → clean → chunk → embed → store. **Retrieval:** query → embed → similarity search → rerank (if any) → prompt assembly → generate.
- Know how many chunks/results you return at each step and why.
- Know how often each function runs (once at startup vs. per request vs. scheduled).
- **Query rewriting:** automatically transform vague user questions into more specific search queries before hitting the vector store.
- If you have two answer paths (grounded vs. fallback), be explicit about which runs when and why.
- **Leakage:** features computed across the full dataset before splitting, evaluation data in the retrieval index, prompts that include ground-truth answers — all are leakage. Not just an ML-pipeline problem.

---

## 14. ML Pipeline Rules

- Own every stage: feature engineering, train/test split, model training, validation, threshold calibration, final metrics.
- `stratify` in `train_test_split` preserves class balance across splits — required for imbalanced datasets.
- Metrics must be interpreted, not just displayed: which class is weakest? What does a false positive cost vs. a false negative in this domain?
- Justify every preprocessing choice: vectorizer, stop words, embedding model, batching strategy.
- If you imported PyTorch or `sentence-transformers`, know why and what the trade-off is (size, GPU, latency) vs. a hosted API.
- scikit-learn `model.predict()` is CPU-bound — wrap in `asyncio.to_thread()` if inference is heavy.

---

## 15. Docker & Deployment

- **Networks** — let containers reach each other by service name. They do not provide persistence.
- **Volumes** — persist data so it survives container restarts and removal. These are completely different problems.
- Never hardcode environment-specific URLs. Use env vars: `${REACT_URL:-http://localhost:3000}`.
- **CORS** (`CORSMiddleware` in FastAPI) — browser mechanism that allows/blocks cross-origin requests. Get it wrong and the frontend can't talk to the backend.
- Defend every line of your Dockerfile and compose file. "I copied it" is not an answer.
- In production, services authenticate to each other (OAuth2 client credentials, mTLS, service-account tokens) — not anonymously.

---

## 16. Tests

**Rule:** Test the critical path. Not 100% coverage — fast tests that fail loudly when something important breaks.

### What to test
1. **Pydantic schemas** — valid and invalid input. Cheap and high-value.
2. **Tools** — mock the LLM and external APIs, test the logic.
3. **One end-to-end happy path** through the full agent with all external calls mocked.

### Run automatically
- GitHub Actions (or equivalent) runs tests on every push and pull request.
- A test that doesn't run automatically doesn't exist.

```yaml
# .github/workflows/test.yml
name: tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -r requirements-dev.txt
      - run: ruff check .
      - run: pytest -q
```

---


## Pre-Submit Checklist

- [ ] Every route, tool, and external call is async. No `requests`, no `time.sleep`, no blocking I/O in the request path.
- [ ] Every dependency (DB session, LLM, model, current user) is declared with `Depends()`. No globals constructed inside routes.
- [ ] Heavy resources (model, embedder, engine) load once in `lifespan` and dispose on shutdown.
- [ ] `lru_cache` on deterministic helpers. TTL cache on at least one external call where it makes sense.
- [ ] All config goes through a `Settings` class. No `os.getenv()` outside of it. `extra="forbid"` is set.
- [ ] Every external boundary has a Pydantic model. Tools have typed inputs and typed outputs.
- [ ] Every external call has a timeout, retries with backoff, and structured error returns from tools.
- [ ] Code is split into modules by concern. Logging is structured. Linter and formatter run on every commit.
- [ ] Pydantic schemas, tool logic, and one end-to-end agent flow are tested. Tests run in CI.
- [ ] Every protected endpoint has a `Depends()` that requires a valid token.
- [ ] App returns 401 for missing/invalid tokens; 403 for permission failures — not the other way around.
- [ ] Every schema change has a committed Alembic migration.
- [ ] Data persistence verified in a DB client (pgAdmin, DBeaver, psql) — not just "the API returned 200."
- [ ] Container restart does not lose data.
- [ ] Every tool has a typed `args_schema` and a clear docstring.
- [ ] Tools are registered with the agent at an identifiable line.
- [ ] LLM node and tool node are distinct and both present in the graph.
- [ ] System prompt is in source control.
- [ ] LangSmith traces are enabled and have been reviewed.
- [ ] `response_model` on each endpoint differs from the ORM model where needed.