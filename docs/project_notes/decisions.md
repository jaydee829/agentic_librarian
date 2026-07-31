# Architectural Decision Records

This file documents key architectural decisions, their context, and trade-offs.

## Templates

### ADR-XXX: Decision Title (YYYY-MM-DD)

**Context:**
- Why the decision was needed
- What problem it solves

**Decision:**
- What was chosen

**Alternatives Considered:**
- Option 1 -> Why rejected
- Option 2 -> Why rejected

**Consequences:**
- Benefits
- Trade-offs

## Decisions

### ADR-001: Model Context Protocol (MCP) for Data Access (2026-01-27)
**Context:**
- Need a standardized way to expose database and internal utilities to specialized agents.
**Decision:**
- Adopt Model Context Protocol (MCP) as the communication layer for data tools.
**Consequences:**
- Pros: Seamless integration with MCP-compliant agents, unified tool interface.

### ADR-002: A2A Protocol for Agent Collaboration (2026-01-27)
**Context:**
- Need robust multi-agent coordination for search, filtering, and ranking.
**Decision:**
- Use the A2A Protocol (Linux Foundation standard).
**Consequences:**
- Pros: Standardized discovery, secure messaging, structured delegation.

### ADR-003: Postgres + pgvector for Storage (2026-01-27)
**Context:**
- Need to store both relational metadata and semantic embeddings (tropes, styles).
**Decision:**
- PostgreSQL with `pgvector` extension.
**Consequences:**
- Pros: Single database for relational and vector data, solid ecosystem.

### ADR-004: MLOps Stack Selection (2026-01-27)
**Context:**
- Need orchestration, versioning, and experiment tracking.
**Decision:**
- Dagster (Orchestration), DVC (Data Versioning), MLFlow (Experiment Tracking).
**Consequences:**
- Pros: Industry-standard tools for reproducibility and monitoring.

### ADR-005: Standardized Testing Strategy (2026-01-28)
**Context:**
- Need to ensure all agents and contributors follow consistent testing practices (TDD, coverage, use-case driven).
**Decision:**
- Adopt a formal testing strategy documented in `docs/testing_strategy.md`, enforced via agent protocols and a standardized `.agent/workflows/test.md` workflow.
**Consequences:**
- Pros: Higher code quality, better maintainability, consistent verification across different agents.
- Cons: Slightly higher initial overhead for new contributors.
### ADR-006: Secure Credential Management & Interactive Prompting (2026-01-28)
**Context:**
- Need to prevent hardcoded credentials and ensure users are prompted for missing information in interactive environments.
**Decision:**
- Use `python-dotenv` for local configuration.
- Implement interactive prompting for missing `POSTGRES_USER` and `POSTGRES_PASSWORD` in `DatabaseManager`.
- Raise `ValueError` if credentials remain missing in non-interactive environments.
**Consequences:**
- Pros: Enhanced security by removing fallbacks, improved user experience in local development.

### ADR-007: Local-Only Service Exposure for Development (2026-01-28)
**Context:**
- Need to minimize the attack surface of Docker services during local development.
**Decision:**
- Bind Postgres and MLFlow ports to `127.0.0.1` in `docker-compose.yml`.
**Consequences:**
- Pros: Services are not exposed to the local network by default.
- Cons: Requires manual reconfiguration for remote access or container-to-container access from outside the default bridge network.

### ADR-008: Data Versioning Scope (2026-01-30)
**Context:**
- Misconfiguration was found where orchestration code was tracked by DVC instead of Git.
**Decision:**
- DVC MUST only be used for data files (e.g., `data/raw/*.csv`).
- Orchestration code and other Python source files MUST be tracked exclusively by Git.
**Consequences:**
- Pros: standard dev ergonomics, better code reviews, avoidance of "missing file" errors during local development.

### ADR-009: Encapsulate Ingest Logic in HistoryIngestor (2026-01-30)
**Context:**
- CSV cleaning and model mapping were becoming scattered and harder to test in isolation.
**Decision:**
- Create a `HistoryIngestor` class to centralize cleaning (via `cleaning.py`) and SQLAlchemy object generation (`to_models`).
**Consequences:**
- Pros: Cleaner Dagster assets, easier unit testing of the ingest pipeline, clear path for Phase 2 enrichment.

### ADR-010: Contextual Year Inference for Ambiguous Dates (2026-01-30)
**Context:**
- The raw CSV contains ambiguous dates like "4-Jan" without a year.
- These dates appear in clusters that share a year with unambiguous dates (e.g., "1/7/2020").
**Decision:**
- Use contextual inference (`ffill` and `bfill`) on extracted years from unambiguous dates within the same CSV to fill missing years.
**Consequences:**
- Pros: Automated reconstruction of historical dates without manual data entry.
- Cons: **Dependency on Row Order**. If the CSV rows are not chronologically clustered, the inferred year may be incorrect. Downstream logic must be aware that `date_completed` may be an estimate in these cases.
### ADR-011: Dual-Pathway Audiobook Scouting & MLFlow Benchmarking (2026-01-30)
**Context:**
- Audiobook metadata (especially duration) is often inconsistent across sources.
- Comparison between "web scraping + parsing" vs "direct LLM knowledge" is needed to determine the most reliable and cost-effective method.
**Decision:**
- Implement two concurrent scouting pathways:
    - **Pathway A (Scraping)**: Google Custom Search -> BeautifulSoup Scraping -> LLM Extraction.
    - **Pathway B (Direct)**: Gemini Model with built-in Search Grounding.
- Log both results, latency, and success metrics to MLFlow for benchmarking.
**Consequences:**
- Pros: Data-driven decision making for metadata sources, fallback robustness.
- Cons: Increased API cost during the experimentation phase.

### ADR-012: Trope Deduplication via Semantic Similarity (2026-02-06)
**Context:**
- Book scouts often return inconsistent tags (e.g., "Enemies-to-Lovers" vs "Enemies to Lovers").
- We need a way to group these into standardized tropes to avoid sparse vector space and fragmented recommendations.
**Decision:**
- Use `text-embedding-004` (Gemini) for trope vectorization.
- Implement a `TropeManager` that checks for exact name matches first, then uses cosine similarity with a default threshold of `0.85` to deduplicate incoming tags.
**Consequences:**
- Pros: Automated standardization, reduces noise in the database, improves recommendation relevance.
- Cons: Small risk of false positives (merging distinct tropes) if the threshold is too low.

### ADR-013: Hybrid Data Access and Coarse-Grained MCP Tools (2026-02-06)
**Context:**
- Need to balance performance for batch ingestion (Flow 1) with agentic flexibility for recommendations (Flow 2).
- Pure MCP for batch ingestion introduces significant overhead and complex transaction management.
**Decision:**
- **Flow 1 (ETL)**: Use direct SQLAlchemy/ORM access for deterministic, high-performance batch processing.
- **Flow 2 (Agents)**: Use Model Context Protocol (MCP) for agent discovery and interaction.
- **Tool Design**: Implement "Coarse-Grained" MCP tools that encapsulate complex logic (e.g., search + filter + pgvector math) into single atomic operations.
**Consequences:**
- Pros: High performance for data pipelines, reduced latency/cost for agents, robust ACID compliance for complex recommendation transactions.
- Cons: Duplicate logic definitions (SQLAlchemy models vs MCP schemas), though minimized by sharing core internal scouts/managers.

### ADR-014: Standardized Use of Single With Statements (2026-02-06)
**Context:**
- Nested `with` statements (e.g., `with A: with B:`) are less readable and trigger linting errors (SIM117).
- Consistency across the codebase is required to satisfy pre-commit checks.
**Decision:**
- Always use a single `with` statement with multiple contexts separated by commas (e.g., `with A, B:`).
- This applies to database sessions, file handles, and mock patches.
**Consequences:**
- Pros: Cleaner code, guaranteed compliance with `ruff` (SIM117), reduced indentation levels.

### ADR-016: Explicit Session Flushing for Dependency Management (2026-02-06)
**Context:**
- In complex transactions, new entities are often created and immediately used as foreign keys for subsequent records (e.g., creating a Work and then checking for its Edition).
- SQLAlchemy does not populate the `id` field of a new object until the session is flushed to the database.
**Decision:**
- Explicitly call `session.flush()` after adding a new entity if its ID is required for a subsequent query or relationship within the same transaction.
**Consequences:**
- Pros: Prevents "ID is None" race conditions and data integrity issues.
- Cons: Minor performance overhead of an extra database round-trip (though usually negligible compared to the risk of data corruption).

### ADR-017: Abstract Scout Architecture (2026-02-06)
**Context:**
- As we add more metadata sources (OpenLibrary, StoryGraph, etc.), the monolithic `MultiSourceScout` and loose functions become difficult to maintain and test.
- Need a standardized way to define new sources with consistent error handling and initialization.
**Decision:**
- Adopt a hierarchical abstract architecture:
    - `BaseScout` (ABC): Core contract and shared utilities.
    - `APIScout`: Specialized for structured REST/GraphQL APIs.
    - `LLMScout`: Specialized for unstructured/semantic data using LLMs.
- All metadata sources MUST be implemented as classes inheriting from this hierarchy.
- **ScoutManager**: A central coordinator will handle the registration and merging of multiple scouts.
**Consequences:**
- Pros: Highly modular, easy to add/remove sources, standardized error handling, better testability via class mocking.
- Cons: Slightly more boilerplate for simple APIs.

### ADR-015: Prohibition of Broad Except-Pass Blocks (2026-02-06)
**Context:**
- The use of `except Exception: pass` (or broad `except: pass`) swallows all errors, including keyboard interrupts and unexpected logic failures, making debugging difficult.
**Decision:**
- Broad `except-pass` blocks are strictly prohibited.
- Error handling must be specific (e.g., `except ValueError`) or log the error before continuing.
- When using libraries that provide safety flags (like `errors="coerce"` in Pandas), rely on those instead of broad try-except blocks.
**Consequences:**
- Pros: Better error visibility, easier debugging, more robust code.
- Cons: Requires more explicit handling of edge cases.

### ADR-018: Dual-Verification Pattern for Environment-Dependent Logic (2026-02-06)
**Context:**
- Development occurs in varied environments (Local Windows without Docker vs Docker-ready containers).
- Database-dependent logic (vector search, complex SQL) is difficult to verify without a live instance.
**Decision:**
- Adopt a **Dual-Verification Pattern** for all database-dependent components:
    1.  **Mock Verification**: Use unit tests with mocks to verify code logic and flow. These must run in all CI environments.
    2.  **Live Verification**: Use `@pytest.mark.db_integration` tests to verify actual SQL and data behavior. These run only when a database is reachable.
- Both test types must be implemented simultaneously to ensure parity.
**Consequences:**
- Pros: Guaranteed logic verification in CI, robust data verification in staging/local-docker, clear documentation of environmental dependencies.
- Cons: Increased testing overhead (writing tests twice).

### ADR-019: 4-Agent Specialist Mesh (2026-02-13)
**Context:**
- Monolithic agents are difficult to tune and suffer from "prompt bloat."
- Need to separate "Strategic Planning" from "Data Scouting" and "Nuanced Ranking."
**Decision:**
- Adopt a 4-agent cognitive mesh:
    1. **Librarian**: Orchestrator (Delegation).
    2. **Analyst**: Strategist (Parameter extraction).
    3. **Explorer**: Scout (External discovery).
    4. **Critic**: Matchmaker (Ranking & Feedback).
**Consequences:**
- Pros: Specialized tuning for each agent, modular reasoning, easier to debug failure points.

### ADR-020: Google AI Agent SDK for Mesh Communication (2026-02-13)
**Context:**
- Need a standardized protocol for discovery and delegation between agents.
- The project is already in the Google/Gemini ecosystem.
**Decision:**
- Use the **Google AI Agent SDK** (implements the A2A protocol) to power the agent services.
**Consequences:**
- Pros: Native A2A support, seamless Gemini integration, scalable to Vertex AI Agent Engine.

### ADR-021: Association Object for Contributor Roles (2026-02-17)
**Context:**
- Need to support multiple roles per work (e.g., Author, Editor, Translator) which a simple junction table or direct relationship cannot handle well.
**Decision:**
- Refactor `work_contributors` into a full association object `WorkContributor` with a `role` field.
**Consequences:**
- Pros: Robust support for anthologies and translated works.
- Cons: Slightly more complex queries (extra join).

### ADR-022: Relational Style Model & Vectorization (2026-02-17)
**Context:**
- Literary and performance styles (pacing, tone, voice diff) were stored in `JSONB` blobs, making them difficult to deduplicate or use for vector similarity.
**Decision:**
- Create a standardized `Style` model with embeddings.
- Link Authors, Narrators, and Works to this model via association tables (`AuthorStyle`, `NarratorStyle`, `WorkStyle`).
**Consequences:**
- Pros: Semantic deduplication via `StyleManager`, enabling high-precision vector-based recommendations.

### ADR-023: Informed Scouting (Contextual baseline) (2026-02-17)
**Context:**
- Scouting a book's style without knowing the author's general profile leads to redundant or inconsistent "deltas."
**Decision:**
- Pass the existing Author's styles from the database into the LLM prompt as a "baseline."
- Instruct the LLM to only report stylistic deviations (deltas) from this baseline for the specific work.
**Consequences:**
- Pros: Cleaner work-specific metadata, higher accuracy in identifying "stylistic drift."

### ADR-024: Style Inheritance & Override Pattern (2026-02-17)
**Context:**
- Most books by an author share their core style, but some vary (e.g., Running Man vs The Stand).
**Decision:**
- Implement an inheritance pattern in MCP tools (`get_work_details`):
    1. Retrieve `WorkStyle` overrides first.
    2. Inherit missing attributes from the primary `AuthorStyle` profile.
**Consequences:**
- Pros: Most granular data is always used; avoids massive data duplication.

### ADR-025: Strict Import Hierarchy (2026-02-17)
**Context:**
- Circular dependencies were common between Models, Scouts, and Assets.
**Decision:**
- Move most just-in-time (JIT) imports to the top level to establish a clear hierarchy (Models -> Managers -> Scouts -> Assets).
- Use service layers or manual `sys.path` injection only where absolutely necessary to maintain tool alignment.
**Consequences:**
- Pros: Better IDE support, predictable load order.
- Cons: Requires careful management of model dependencies.

### ADR-026: Temporal Re-read Decay Logic (2026-02-17)
**Context:**
- Need to allow recommendations of previously read books without spamming the user with recent reads.
**Decision:**
- Implement a 2.0-year "decay" threshold. Books read >2 years ago are marked as `is_re_read_candidate`.
**Consequences:**
- Pros: Surfaces beloved classics while maintaining discovery of new titles.

### ADR-027: Hybrid Trope-Style Semantic Search (2026-02-17)
**Context:**
- Recommendations based on tropes alone are too plot-focused; styles alone are too vibe-focused.
**Decision:**
- Combine Trope and Style vector similarity into a single discovery tool (`search_internal_database`).
- Average vectors within categories and merge candidate sets.
**Consequences:**
- Pros: Recommendations that match both the "what" (plot) and "how" (prose/tone) of user taste.

### ADR-028: Tiered Feedback (Mood vs. Preference) (2026-02-17)
**Context:**
- User mood ("not in the mood for violence") shouldn't permanently block tropes or books.
**Decision:**
- Handle "Moods" as transient session constraints passed to the Critic for rank penalties.
- Handle "Preferences" (Already Read, Dismissed) as persistent database status updates.
**Consequences:**
- Pros: Responsive to immediate state without losing long-term accuracy.

### ADR-029: Trope-RAG Justification (2026-02-17)
**Context:**
- Users need to know *why* a book was suggested to build trust in the agent.
**Decision:**
- Retrieve trope names, general descriptions, and book-specific "justification" evidence from the DB.
- Force the Critic agent to anchor its reasoning in these specific facts.
**Consequences:**
- Pros: Transparent, grounded, and evidence-based recommendations.

### ADR-030: Lazy Initialization for Global Service Managers (2026-02-17)
**Context:**
- Instantiating global managers (like `DatabaseManager`) at the module level caused crashes during test collection in environments without full configuration (CI).
**Decision:**
- Use lazy initialization for all heavy service managers. Defer credential validation and resource allocation until the first actual use.
- Add override hooks (`set_db_manager`) to allow test-time dependency injection.
**Consequences:**
- Pros: Safe imports across all environments, improved testability.
- Cons: Slightly more complex internal state management.

### ADR-031: Composite Primary Keys for Style Link Tables (2026-02-18)
**Context:**
- An author or work can have multiple style attributes (e.g., 'pacing' and 'tone') associated with the same `style_id` or different ones.
- The previous schema `(author_id, style_id)` as the primary key prevented linking the same style to different attributes for the same entity.
**Decision:**
- Include `attribute_type` in the primary key for `AuthorStyle`, `NarratorStyle`, and `WorkStyle`.
- The new composite primary key is `(entity_id, style_id, attribute_type)`.
**Consequences:**
- Pros: Enables rich, multi-dimensional style tagging; aligns with the `WorkContributor` pattern.
- Cons: Requires database migration for existing installations.

### ADR-032: SQL-Level Vector Similarity with pgvector (2026-02-18)
**Context:**
- Calculating cosine similarity in-memory by loading all tropes or styles is a performance bottleneck as the database grows.
**Decision:**
- Use `pgvector`'s `cosine_distance` operator directly in SQLAlchemy queries (`.order_by(Style.embedding.cosine_distance(vec))`).
- Implement an `lru_cache` for embedding generation to reduce API costs and latency.
**Consequences:**
- Pros: Significant performance gains (uses database indexing), lower memory usage, reduced API costs.
- Cons: Tightens dependency on `pgvector` functionality.

### ADR-033: Eager Loading to Prevent N+1 Queries in Agent Tools (2026-02-18)
**Context:**
- MCP tools like `get_unacted_suggestions` were performing recursive queries for work metadata, tropes, and styles within loops.
**Decision:**
- Use SQLAlchemy `joinedload` and `selectinload` to eagerly fetch all required relationships in a single optimized query.
**Consequences:**
- Pros: Massive reduction in database round-trips; improved response time for the Librarian agent.
- Cons: Slightly more complex query definitions.

### ADR-034: Isolated Database for `db_integration` Tests (2026-05-30)
**Status:** Accepted and implemented (2026-05-30) in `test/conftest.py` — a dedicated `*_test` database is auto-created and all tables are truncated before each `db_integration` test.

**Context:**
- The `db_integration` suite had never been executed before the stack ran under Docker on a single machine. Once it ran, several isolation problems surfaced:
  - Tests run against the **same** database the application uses (`POSTGRES_DB=agentic_librarian`), so they pollute real data.
  - There is **no cleanup** between tests, so rows accumulate across runs. This caused non-deterministic passes (e.g. `search_internal_database` returned results from a *previous* test's committed rows rather than the current test's seed).
  - The MCP tools intentionally open their own independent sessions and commit (coarse-grained design, ADR-013), so a single outer transaction cannot wrap a test for rollback — the tools' commits escape it.

**Decision:**
- Run `db_integration` tests against a **dedicated test database** (e.g. `agentic_librarian_test` via a `TEST_POSTGRES_DB` / `DATABASE_URL` override), never the application database.
- Reset state with a **function-scoped autouse fixture that truncates all tables** before each `db_integration` test (the session-scoped fixture from the schema-creation work continues to create the schema + `vector` extension once).

**Alternatives Considered:**
- Per-test transactional rollback (bind the session to a SAVEPOINT) → rejected: the MCP tools open new sessions on the engine and commit, so their writes bypass and survive the test's transaction.
- `drop_all` + `create_all` per test → rejected: slower than `TRUNCATE`, and still unsafe against the app DB without a separate database.
- Status quo (shared DB, commit seed before tool calls — the stopgap applied in commit 43ed411) → insufficient: it makes the current assertions pass but leaves data pollution and run-to-run drift.

**Consequences:**
- Pros: Deterministic, repeatable integration tests; no risk to application/dev data; aligns with the Dual-Verification mandate (ADR-018).
- Cons: Requires provisioning a separate database in local/compose/CI environments and a truncation fixture; marginally more setup per test run.

### ADR-035: Internal (DB) vs External (Web) Search Clarification & Phased Mesh Delivery (2026-05-30)
**Context:**
- The spec and `agents/search_strategies.py` used "Internal" (Mode A) and "External" (Mode B) to mean *where the web search runs* — in-process `google-genai` grounding vs a separate A2A microservice. **Both are web search.** This conflicts with the intuitive meaning ("internal" = our database) and caused real confusion.
- The 4-agent mesh does not actually run yet: no `model` is set on the `LlmAgent`s, there is no ADK Runner/session entrypoint, and the Explorer has no search capability. Phase 3 was marked COMPLETED but only the *components* exist; they are not wired to run.

**Decision:**
- **Adopt functional naming.** *Internal = retrieval from our Postgres DB* (existing/read/suggested books — already wired via the Critic's `search_internal_database` and the Librarian's `get_unacted_suggestions` / `get_user_trope_preferences`). *External = web discovery via search grounding* (finding new/unread books — the Explorer's job, currently the gap).
- The earlier "Mode A (in-process grounding) vs Mode B (external A2A service)" distinction is an **implementation detail of External web discovery**, not a functional split. The MVP uses Mode A (in-process Gemini grounding); the external A2A service (Mode B) is deferred.
- **Deliver the runnable mesh as four phased specs** (each its own spec → plan → implementation), order 1 → (2, 3) → 4:
    1. **Mesh runtime foundation** — set models on all agents (via `GEMINI_MODEL`), build the ADK Runner/session + a `run_recommendation(prompt)` entrypoint so the Librarian executes and delegates.
    2. **Explorer = external web discovery** — grounded web search on the Explorer; verify real new-book discovery ("ENV-015 part 2").
    3. **Internal retrieval readiness** — DB-backed tools (preferences, vector search, history) return real results against a seeded DB (needs a real Flow 1 enrichment run); rename internal/external in code + docs.
    4. **End-to-end recommendation + Trope-RAG** — full Librarian→Analyst→Explorer→Critic chain yields a justified, logged recommendation; e2e test.

**Consequences:**
- Clearer architecture and naming; removes the Internal/External ambiguity.
- ENV-015 part 2 is scoped to Spec 2 (Explorer external discovery).
- `search_strategies.py`'s A/B benchmark is decoupled from the live mesh (kept as an experiment or removed later).
- Phase 3's "COMPLETED" status was component-level, not runnable-mesh-level; these four specs close that gap.

### ADR-036: The Librarian is a Multi-Turn Conversational Agent (2026-05-30)
**Context:**
- The Librarian should feel like talking to a librarian who remembers the exchange and knows the user's reading history (use_cases.md Levels 4–6: follow-ups, "I already read that" corrections, social signals). A single prompt→response call is too restrictive, and responses may legitimately be a list of authors, authors + an example book, or specific titles depending on the request.

**Decision:**
- The recommendation runtime hosts the Librarian in an ADK `Runner` with a **reusable session**. The public abstraction is a multi-turn `LibrarianConversation` (`start_conversation` → `send` → `send` …) that reuses one `(user_id, session_id)` so the agent remembers prior turns. `run_recommendation(prompt)` remains a one-shot convenience over the same path.
- **Two layers of memory:** *within-conversation memory* = the ADK session; *durable "knows everything you've read"* = the Postgres reading-history DB via the agents' tools, independent of any conversation.
- **Response shape** (authors, authors + an example book, or specific books) is Librarian instruction/behavior decided per request — not constrained by the runtime.
- Spec 1 uses `InMemorySessionService` (ephemeral conversations); `DatabaseSessionService` (Postgres, resumable conversations across restarts) is a deferred upgrade.

**Consequences:**
- Supports the Level 4–6 conversational use cases within a session; the durable user profile persists in the DB regardless of session lifetime.
- Conversations are not resumable across app restarts until `DatabaseSessionService` is adopted.
- Realized by ADR-035 Spec 1; design at `docs/superpowers/specs/2026-05-30-mesh-runtime-foundation-design.md`.

### ADR-037: Pin ADK at 2.1.0 for the Mesh; Defer Upgrade (2026-05-31)
**Context:**
- Spec 2 needs grounded web search on the Explorer, which is a *sub-agent*. ADK forbids built-in tools in sub-agents, but `GoogleSearchTool(bypass_multi_tools_limit=True)` converts the built-in search into a function-calling tool that is allowed there.
- A live spike on the **installed ADK 2.1.0** verified this works — both standalone and via Librarian→Explorer (`AgentTool`) — returning real recent (2024) titles. Note: `use_interactions_api` (the newer documented form) is **not** a real parameter in 2.1.0 (it is silently ignored); `bypass_multi_tools_limit` works on its own.

**Decision:**
- Keep `google-adk` pinned at **2.1.0** for now. Implement the Explorer's grounded search with `GoogleSearchTool(bypass_multi_tools_limit=True)` (no Interactions API).
- Do **not** upgrade ADK mid-feature: the Spec 1 mesh runtime (`Runner`, `LlmAgent`, sessions, `AgentTool`) is built and verified against 2.1.0, and ADK is fast-moving with pinned siblings (`a2a-sdk`, `google-genai`).

**Consequences:**
- Pros: minimal, spike-verified path; no risk to the just-merged Spec 1 runtime.
- **Tech debt (deferred):** a benign `[EXPERIMENTAL] JSON_SCHEMA_FOR_FUNC_DECL` warning; we forgo newer native multi-tool / Interactions-API ergonomics and easy grounding-citation surfacing. **An ADK upgrade is its own future task** — do it when a newer capability is actually needed (e.g. grounding citations for Spec 4), with a full re-verification of the mesh.

**Update (2026-05-31, during Spec 4 brainstorming):** Investigated whether to upgrade ADK for `output_schema`+tools. Findings: **2.1.0 is already the latest stable** on PyPI (line: 2.1.0 › 1.34.1 › …; nothing newer to upgrade to short of unreleased git `main`). The capabilities we thought we lacked actually shipped *below* 2.1.0 and are present: **`output_schema` together with tools** (improvements in 1.26.0, 2026-02-26) and **grounding/citation metadata + interactions-API** (1.27.0, 2026-03-12). Empirically verified in the container: `LlmAgent(tools=[...], output_schema=...)` constructs cleanly and the LlmAgent source states *"The ADK supports using output_schema and tools together … enforcing structure only on the reply."* So the earlier "forgo output_schema" framing was based on stale general docs, not our installed version. **Decision:** stay on 2.1.0, **use `output_schema`+tools natively** (Spec 4), and raise the dependency floor to `google-adk>=2.1.0` / `google-genai>=1.72` so a fresh resolve keeps the capability. The `bypass_multi_tools_limit` approach for `google_search` in a *sub-agent* remains correct (a separate built-in-tools-in-sub-agents constraint). Grounding-citation surfacing is available if a later spec wants it — no upgrade required.

### ADR-038: Security Review is a Per-Spec Practice (2026-05-31)
**Context:**
- The system is an agentic mesh with live web grounding (Spec 2) and a mutable database. The two classic exposures are SQL injection (DB) and prompt injection (LLM mesh acting on untrusted web text). A PR #20 review prompted making security review a standing practice rather than ad hoc.
- Current posture audited: SQLi is mitigated by construction (SQLAlchemy ORM, parameterized queries, no raw/`text()` SQL); crash-on-bad-input was the class closed by the `get_work_details` UUID guard.

**Decision:**
- Run a lightweight threat-model checklist (untrusted inputs, trust boundaries, tool input validation, write authorization, secret handling) during **each spec's review**, and log findings in `docs/project_notes/security.md`.
- Logged two open findings now: **SEC-001** (prompt injection via the Explorer's web grounding) and **SEC-002** (write-tool authorization). Both are slated for **Spec 4**, where the full write-path mesh comes together.

**Consequences:**
- Pros: security gets a recurring, low-overhead review without front-loading a heavy security spec or slipping the recommendation MVP; findings are tracked, not lost.
- Cons: concrete hardening of SEC-001/002 is deferred to Spec 4 — acceptable given the single-user, bounded blast radius today.

### ADR-039: Remove the Orphaned `search_strategies.py` Experiment; Functional Naming is Canonical (2026-05-31)
**Context:**
- `agents/search_strategies.py` defined `InternalSearchAgent` (Mode A: in-process genai
  grounding) and `ExternalA2AAgent` (Mode B: simulated A2A) — both *web* search in the old,
  confusing sense flagged by ADR-035. It was a standalone MLflow experiment with no
  production importer, used the quota-dead `gemini-2.0-flash`, and was superseded by the
  Spec 2 Explorer (grounded `GoogleSearchTool`).

**Decision:**
- Remove the module and its unit test. Functional naming is canonical: **internal =
  retrieval from our Postgres DB** (`search_internal_database`, `get_user_trope_preferences`,
  `get_unacted_suggestions`, `check_reading_history`); **external = web discovery** (the
  Explorer). `agents/services.py` already conforms — no rename needed there.

**Consequences:**
- Less dead code and one fewer source of the internal/external ambiguity. The deferred
  in-process-vs-A2A (Mode A/B) comparison, if ever revisited, is an implementation detail of
  external discovery (ADR-035), not a functional split.

### ADR-040: One-Shot Recommendation is a Fixed-Order SequentialAgent Pipeline (2026-05-31)
**Context:**
- The fully LLM-driven Librarian orchestration was non-deterministic (REC-016): one-shot calls
  sometimes asked a clarifying question instead of answering, and delegation runs sometimes ended
  on a tool/transfer event yielding "(no response)". Web discoveries (no DB id) could not be ranked
  by the Critic.

**Decision:**
- `run_recommendation` runs a fixed-order ADK `SequentialAgent` pipeline (Analyst →
  InternalCandidates → Explorer → Enrichment → Critic → Logger) and returns
  `state["recommendation"]`. The sequence is code, not an LLM decision, so ordering is deterministic
  and the final text is read from session state (not the last event). The conversational multi-turn
  Librarian (ADR-036) is unchanged for interactive chat.
- Web discoveries are de-duped + enriched + persisted (`enrich_and_persist_work` + the shared
  `persist_enriched_work`) so the Critic ranks them with DB-backed Trope-RAG.

**ADK 2.1.0 mechanics (verified empirically during implementation):**
- `output_schema` works together with **function** tools, so the **Analyst** uses `output_schema=Targets`.
  But the **Explorer's `google_search` is a built-in tool**, and Gemini rejects combining a built-in tool
  with function-calling (which is how `output_schema` is enforced) — so the Explorer has NO `output_schema`;
  it emits a JSON `{"books":[...]}` object as text, parsed by the pipeline's Enrichment step.
- Custom (non-LLM) pipeline steps write state via `Event(actions=EventActions(state_delta={...}))`; direct
  `ctx.session.state` mutation does NOT persist in 2.1.0.
- `SequentialAgent` logs a benign deprecation warning (the `Workflow` replacement is not shipped in 2.1.0);
  it remains the correct API for our pinned version.

**Consequences:**
- Deterministic, testable one-shot recommendations; discoveries become first-class catalog Works.
- Security hardening (SEC-001/002) is deferred to Spec 5 but structured for: discoveries are consumed as
  data and all writes funnel through MCP tools (`enrich_and_persist_work` is the single new write surface).
- The live end-to-end test (`test_recommendation_e2e.py`) is `api_dependent`; live verification is gated on
  Gemini quota (free-tier). Each pipeline piece is independently covered by deterministic offline tests.

### ADR-041: Pluggable Agent Backend (ADK + Claude Agent SDK) (2026-06-01)
**Context:**
- The ADK mesh calls models via API keys (Gemini), which cannot reach the user's Claude Max
  *subscription* quota — only the Claude Agent SDK's auto-detected Claude Code auth can. The Gemini
  free-tier 429 wall repeatedly blocked live verification.

**Decision:**
- Introduce a `RecommendationBackend` Strategy seam at the `run_recommendation` entrypoint
  (`AGENT_BACKEND=adk|claude`, default `adk`). `ADKBackend` wraps the existing SequentialAgent pipeline
  verbatim; `ClaudeBackend` is explicit Python sequencing of Claude Agent SDK `query()` calls (Analyst →
  internal candidates → Explorer-with-web-search → enrich → Critic → log), exposing the SAME in-process MCP
  tools via `create_sdk_mcp_server`. Prompts (`agents/prompts.py`), schemas (`agents/schemas.py`), and pure
  helpers (`agents/candidates.py`) are shared so the two backends never drift; the shared Explorer prompt was
  made tool-agnostic ("use your web search tool") so it reads correctly for both google_search (ADK) and
  WebSearch (Claude).
- Structured agent output on the Claude backend uses **JSON-as-text parsed by `coerce_schema_value`** (the
  SDK's `output_format` semantics were undocumented at v0.2.87; the text approach is the robust fallback).
- Embeddings stay on Gemini (pgvector / `gemini-embedding-001`) for both backends — separate, low-volume
  quota. `claude-agent-sdk` is an optional extra; the `claude` CLI is installed in the devcontainer and
  authenticated in-container (one-time manual login) for Max-quota calls.

**Consequences:**
- The recurring Gemini quota wall is bypassable by flipping one config value; the ADK work is preserved as
  the default backend. Two agent implementations to maintain. Using subscription quota for a personal app is
  a ToS gray area (acceptable for personal use; not a supported product path). Conversational Librarian on
  Claude and security hardening (SEC-001/002) remain out of scope.
- Live validation of the Claude backend is deferred until the `claude` CLI is authenticated; the
  `allowed_tools` web-search identifier ("WebSearch" vs "web_search") must be verified on the first live run
  (issues.md REC-019). Each non-LLM piece is covered by deterministic offline tests.

### ADR-042: Model Routing (grounding vs non-grounding) + Transient-Error Retry (2026-06-01)
**Context:**
- The first live e2e run crashed on an uncaught `503 UNAVAILABLE` ("model experiencing high demand") from
  `gemini-2.5-flash` — Google appears to be squeezing gemini-2.5 free-tier capacity. gemini-3.1-flash-lite is
  stable with markedly higher free-tier throughput, but is below 3 Flash on quality and free-tier Search
  *grounding* on the 3.x family is currently unreliable/ambiguous. Two distinct needs (grounded discovery vs
  plain generation) were sharing one model config (`GEMINI_MODEL`), so they couldn't be routed separately.

**Decision:**
- Split model config into two roles. `GEMINI_MODEL` (default **gemini-3.1-flash-lite**) drives the
  NON-grounding mesh agents (Analyst, Critic, Librarian) — high throughput, off the squeezed 2.5 capacity.
  New `GROUNDING_MODEL` (default **gemini-2.5-flash**, honouring `EXPLORER_MODEL` as a back-compat alias)
  drives everything that uses Gemini Search grounding: the Explorer agent AND the LLM scouts
  (StyleScout/LLMTropeScout/audiobook), which were previously (incorrectly) pinned to `GEMINI_MODEL`.
- Add a single shared `HttpRetryOptions` (`llm_retry.py`: 5 attempts, exp backoff, codes 429/500/502/503/504)
  applied everywhere Gemini is called: ADK agents via `Gemini(model=..., retry_options=...)`, and the scout /
  embedding `genai.Client(http_options=...)`. This rides through transient demand spikes instead of crashing
  the run (resolves REC-020). Embeddings stay on `gemini-embedding-001`.

**Consequences:**
- The 503 that crashed the run is now retried with backoff; non-grounding load shifts to the higher-limit
  3.1-flash-lite. Critic ranking quality on 3.1-flash-lite is unverified vs 2.5 — if it regresses, bump
  `GEMINI_MODEL` back to a flash-class model (config-only). If free-tier 3.x grounding later proves reliable,
  the Explorer/scouts can move to `GROUNDING_MODEL=gemini-3.x` for higher limits. Gemma 4 31B was considered
  as a high-limit grounding option but rejected: Gemma on the Gemini API generally lacks the grounding tool
  and is a smaller model (weaker for the grounding/reasoning roles).

### ADR-043: Hardcover Lookup via Fuzzy Search + Book-by-Id (2026-06-01)
**Context:**
- `HardcoverScout` filtered editions with three exact-match clauses (`book.title _eq` AND
  `edition_format _eq "ebook"` AND US `country _eq`). These almost never all matched real data, so
  Hardcover (priority-1 scout) silently contributed nothing to web-discovered books (REC-022). Hasura
  blocks `_ilike`/fuzzy operators on the editions filter, but Hardcover exposes a fuzzy `search` query.

**Decision:**
- Two-step lookup: (1) `search(query: <title>, query_type:"Book")` — by title only (adding the author
  surfaces companion "workbook" entries) — then select the hit whose `author_names` matches and that
  has the most `users_read_count`, excluding companion titles; (2) `books(where:{id:{_eq}})` for
  description/pages/contributions/cached_tags/editions. Format/country preference is applied in Python
  over the returned editions (prefer requested format + US, else format, else any). Note: the live API
  exposes a scalar `contribution` field on `contributions` (not `author_role { name }`).

**Consequences:**
- Hardcover now returns real metadata for known titles, including ones whose stored title differs
  (`&` vs "and", articles) since matching is fuzzy. Two API calls per book instead of one (acceptable —
  priority-1 short-circuits the other scouts; Hardcover quota is generous). Companion/workbook hits are
  filtered heuristically; a future refinement could weight series/edition signals.

### ADR-044: GroundedLLM Seam — Enrichment Scouts Follow AGENT_BACKEND (2026-06-02)
**Context:**
- ADR-041 made the recommendation mesh backend-selectable, but the enrichment LLM scouts stayed on
  Gemini. An `AGENT_BACKEND=claude` run (and the Flow-1 ETL) still hit the Gemini free-tier
  `generate_content` daily cap (20/day), stretching a full reading-history ingest into ~weeks (REC-024).

**Decision:**
- Introduce a `GroundedLLM` provider seam (`scouts/grounded_llm.py`: `generate(prompt, grounded=)`) with
  `GeminiGroundedLLM` (google_search) and `ClaudeGroundedLLM` (Agent SDK WebSearch, run synchronously via
  `asyncio.run`), chosen by `get_grounded_llm()` reading the SAME `AGENT_BACKEND` knob. `LLMScout` takes
  the provider (injectable); all four scouts call `self._llm.generate(...)`. Prompts, JSON parsing, merge
  and persistence are unchanged. Embeddings stay on Gemini (separate, higher quota — not the bottleneck).

**Consequences:**
- One knob flips the whole pipeline (recommendation + batch ETL) between Gemini and Claude; default is
  byte-for-byte Gemini. Claude extraction quality vs Gemini grounding is validated by a live check.
  `ClaudeGroundedLLM.generate` must be called from a synchronous context (it uses `asyncio.run`); scouts
  always are. A full ETL on Claude issues many WebSearch calls — validate Agent SDK rate limits on a
  small batch first.

### ADR-045: Conversation Seam on the Backend Protocol + CLI Chat Harness (2026-06-05)
**Context:**
- No command-line way to exercise the conversational piece. `RecommendationBackend` (ADR-041) is
  one-shot only; multi-turn (`LibrarianConversation`, ADR-036) exists only on ADK, and the Claude
  backend is a fixed pipeline of independent `query()` calls (ADR-040) with no conversational mode.
  Spec: docs/superpowers/specs/2026-06-05-cli-chat-design.md.

**Decision:**
- Extend the strategy seam with `start_conversation(user_id, on_event) -> BackendConversation`
  (`send`/`close`), so multi-turn means the SAME thing on both backends: a stateful Librarian session
  calling DB/web tools on demand. ADK wraps the existing `LibrarianConversation` (+ optional event
  callback in `asend`); Claude gains a true conversational mode via a persistent `ClaudeSDKClient`
  session on a background event-loop thread (PR #26 async precedent). Rejected: CLI-managed transcript
  replay over the one-shot pipeline (re-runs the full pipeline per turn: slow, quota-hungry, logs a
  spurious Suggestion per turn).
- **Full mesh parity on Claude** (amended same day, user decision): the Claude conversational
  Librarian delegates to the SAME specialist mesh as ADK via programmatic SDK subagents
  (`ClaudeAgentOptions(agents={"analyst"/"explorer"/"critic": AgentDefinition(prompt=<specialist
  instruction>, tools=<scoped like the ADK mesh>)})`, invoked through the `Task` tool — the analogue
  of ADK's `AgentTool`). A single-agent variant was rejected: it doesn't exercise the specialist
  prompts, and ADK-vs-Claude conversation comparisons would conflate backend with architecture.
  Cost accepted: slower turns / more Max quota per turn. VERIFY live (REC-019 pattern): subagent
  visibility of the in-process MCP server via `AgentDefinition.mcpServers=["librarian"]`.
- New `librarian` console script (argparse REPL; `--once`, `--backend`, `--quiet`, `--no-mlflow`)
  printing replies plus a compact key-event trace (`on_event(kind, detail)`).
- Each conversation is one MLflow run (experiment `librarian_conversations`: params backend/model/
  mode, per-turn latency metrics, `transcript.jsonl` artifact) owned by the CLI-layer
  `ConversationRecorder` — backends stay pure. Degradation posture: MLflow failures warn once and
  never block the chat; transcript falls back to a local gitignored `.chat_logs/<ts>.jsonl`
  (informed by the 2026-05-31 MLflow 403 bug).

**Consequences:**
- The conversational piece becomes testable on both quota pools, and ADK-vs-Claude conversations are
  comparable in the MLflow UI. The Claude one-shot pipeline is untouched. The new protocol method is
  additive (existing callers unaffected).

### ADR-046: Product Roadmap — Walking Skeleton First, Gemini-First Prod, BYOK-Ready Schema (2026-06-05)
**Context:**
- Three heavy lifts loomed unordered: web front end (Phase 4), GCP deployment, multi-user
  support. Target: friends/family soon, then open signup with a cost-recovery subscription.
  Couplings: the Claude backend's Max OAuth doesn't deploy; auth-provider choice is
  GCP-informed; multi-user expires security.md's single-user assumptions.
  Spec: docs/superpowers/specs/2026-06-05-product-roadmap-design.md.

**Decision:**
- Sequence: (0) GCP walking skeleton (Cloud Run + Cloud SQL/pgvector + CD, existing FastAPI
  scaffold, catalog restored, access-gated) → (1) multi-user foundation (users + user_id +
  default-user migration, Firebase Auth, usage metering keyed by user and key-source,
  KMS-encrypted user_credentials placeholder) → (2) front end → friends/family beta →
  (3) productization (Stripe, quotas, BYOK feature, Claude prod enablement, security
  re-review) → open signup.
- Prod LLM: Gemini-first; AGENT_BACKEND stays env-configurable in prod so Claude-via-API
  (viable per the cited mid-June 2026 Anthropic billing change) is config + container +
  metering, not architecture. BYOK: schema-ready in Lift 1, feature in Lift 3.
- Shared catalog / per-user history. MLflow+Dagster stay dev-only. DEBT-001: bulk
  enrichment remains operator-run local Dagster until demand justifies a Cloud Run Job.

**Alternatives Considered:**
- FE-first, GCP last (big-bang deploy) -> Rejected: saves the unfamiliar infra for the end
  where it is hardest to debug, and friends need auth before anything is shareable — so
  multi-user had to precede the beta regardless; skeleton-first converts deployment into
  continuous delivery from week one.
- FE before the multi-user foundation -> Rejected: would build the FE twice (once
  single-user, once against the auth'd contract).
- Claude API in prod now -> Deferred: ~5-15x flash-tier Gemini per conversation plus a
  second vendor to meter before any revenue; kept one config-change away via the
  AGENT_BACKEND seam.
- Both-LLM user-selectable tier at launch -> Rejected: dual metering and parity
  maintenance before product-market fit.

**Consequences:**
- Deployment becomes continuous from week one instead of a big-bang finale; the FE is
  built once against the auth'd contract; the most invasive work (schema) happens while
  the surface is smallest. Each lift gets its own spec/plan cycle; security.md must be
  formally re-reviewed before open signup.

### ADR-047: Lift 0 walking-skeleton infrastructure (Cloud Run + Cloud SQL + WIF CD) (2026-06-05)
**Status:** Accepted (2026-06-05)

**Context:**
- The approved roadmap (ADR-046) deferred five Lift-0 decisions: DB cost posture, access
  gate, CD shape, region/budget, and provisioning style.
- Spec: `docs/superpowers/specs/2026-06-05-lift0-walking-skeleton-design.md`.

**Decision:**
- **Database:** Cloud SQL Postgres 16 `db-f1-micro` (~$12/mo floor, accepted).
- **Access gate:** Cloud Run IAM gate (`--no-allow-unauthenticated`) until Firebase Auth
  (Lift 1) — zero throwaway code.
- **CD:** GitHub Actions auto-deploy on merge to `main` via Workload Identity Federation
  (keyless, repo-pinned).
- **Region/budget:** us-central1; $25/mo budget with 50/90/100% email alerts.
- **Provisioning:** Scripted-gcloud (`infra/` numbered scripts +
  `docs/runbooks/gcp-walking-skeleton.md`).
- **Secrets:** Secret Manager holds the full `DATABASE_URL`; Cloud Run injects secrets
  verbatim — no string composition in the app.
- **Image:** Prod image (`Dockerfile.api`) is separate from the dev image.
- **API surface:** Ported scaffold (`/health`, `/health/db`, `/history`) plus `GET /works`.

**Alternatives Considered:**
- Neon/Supabase free-tier Postgres → $0/mo but a second vendor plus a migration back to
  Cloud SQL for multi-user anyway; rejected.
- IAP or an app-level bearer gate → both discarded by Lift 1's Firebase Auth; the IAM gate
  is zero throwaway code; rejected.
- Cloud Build triggers → a second CI system, deploy visibility outside GitHub; rejected.
- Terraform from day one → two new systems at once for one environment; deferred to
  ~Lift 3.
- Stop-Cloud-SQL-when-idle → saves ~$10/mo now, breaks the moment friends have accounts;
  rejected.

**Consequences:**
- ~$12–16/mo run cost; `main` is always what's deployed (path-filtered).
- The service is invisible to unauthenticated traffic until Lift 1.
- Infra changes go through PR-reviewed scripts; drift from console click-ops is possible
  (accepted until Terraform).
- `/history` remains unpaginated (consciously deferred to the Lift 2 API-contract work —
  `/works` paginates; tracked follow-up INF-029).

### ADR-048: Lift 1 — multi-user foundation (Alembic, Firebase Auth, context-bound scoping, usage metering) (2026-06-06)
**Status:** Accepted (2026-06-06)

**Context:**
- The roadmap (ADR-046) sequences multi-user before the front end: the invasive schema
  work happens while the surface is small.
- Lift 0 (ADR-047) is live; production has a real database that must now be ALTERed for
  the first time. The single-user assumptions recorded in security.md begin to expire.
- Spec: `docs/superpowers/specs/2026-06-06-lift1-multi-user-design.md`.

**Decision:**
- **Alembic** for all schema changes from now on; autogenerated frozen baseline
  (`6c2cdc370222`) + hand-written multi-user migration (`c804d02d6fbb`); the test
  conftest builds schema via `upgrade head`, so CI proves the migrations forever.
- **users / usage / user_credentials** tables; `user_id` NOT NULL on
  reading_history/suggestions, backfilled onto the fixed `DEFAULT_USER_ID`.
- **Firebase Auth** verified by firebase-admin (ADC) in a FastAPI dependency;
  `SIGNUP_MODE` env toggle (`invite` → 403 strangers, `open` → auto-create);
  claim-by-email (requires `email_verified`) links invited rows on first sign-in,
  one-shot (a re-created Firebase account cannot re-claim a linked row).
- **Context-bound scoping:** identity rides a ContextVar set only by trusted code;
  MCP tool signatures gain NO user parameter (SEC-001 extension); tools fail closed.
  The Claude conversation captures identity at construction and re-applies it on its
  background loop thread (explicit, mutation-tested).
- **Usage metering:** one row per LLM call from both backends; best-effort in Lift 1
  (a metering failure warns and the conversation continues).
- **user_credentials** is schema-only (BYOK + Cloud KMS land in Lift 3).

**Alternatives Considered:**
- user_id as a tool parameter → relies on LLM behavior, which SEC-001 forbids; rejected.
- Per-user MCP server instances → heavier, complicates the in-process pattern; rejected.
- IAP / manual JWT verification → wrong audience / reimplements firebase-admin; rejected.
- Per-conversation usage rollups → loses multi-vendor attribution Lift 3 needs; rejected.
- JIT-only or allowlist-only provisioning → the user wants a toggle; rejected.

**Consequences:**
- Every future schema change is a migration.
- The Cloud Run IAM gate stays on through Lift 1 (two-token testing via
  X-Serverless-Authorization) and opens in Lift 2.
- The CD smoke can no longer reach `/health/db` (Firebase-gated) — it asserts
  401-enforcement instead; DB connectivity verification moves to the operator runbook.
- Prod usage rows start flowing when Lift 2 deploys the mesh.

### ADR-049: Semantic CSS design tokens + dark mode (2026-06-17)
**Context:**
- Dark mode (beta item E1) needed a color system; the frontend had ~46 hardcoded hex across 9 CSS files
  and no theming layer.
**Decision:**
- Introduce semantic CSS custom-property tokens in `index.css` (`:root` light = the prior colors;
  `:root[data-theme="dark"]` override). Component CSS uses `var(--token)` only. `theme.ts` resolves
  localStorage→OS, persists, sets `data-theme` on `<html>`; `main.tsx` applies before render; TopBar toggle.
- Split text-on-color tokens by role (`--on-accent`/`--on-danger` vs `--on-badge`) for WCAG AA contrast
  once accent/danger invert to light in dark mode.
**Consequences:**
- Light mode is pixel-identical (tokens = prior values); the token layer is the foundation the planned
  "Visual Identity v2" redesign extends (palette + type/spacing scales + component restyle).
- PR #54 (`3d2dafe`).

### ADR-050: Enrichment status DERIVED from trope presence (no status column) (2026-06-16)
**Context:**
- Beta C1/C2 needed History to show whether a book's deep (background) enrichment finished; `Work` has no
  status/`created_at` column.
**Decision:**
- Derive "enriched" from trope presence (deep pass is the only thing that adds tropes) rather than add an
  `enriched_at`/status column + backfill. History shows tropes once present, else an "Enriching…" chip.
**Alternatives Considered:**
- `enriched_at` timestamp / full status enum -> rejected: migration + backfill, and still can't cleanly
  show a true "failed" without a timeout sweep.
**Consequences:**
- No DB migration. Cannot distinguish failed/empty/never-ran from in-flight (perpetual "Enriching…") —
  logged as DEBT-035 for a future timeout sweep. PR #52 (`93c2d8f`).

### ADR-051: Deep-enrichment concurrency + memory tuning (enrich queue=4, Cloud Run=2Gi) (2026-06-23)
**Context:**
- The first real new-user bulk import OOM-stormed the deep-enrichment scouts (Trope/Style/Audiobook/
  DirectKnowledge, run via Cloud Tasks → `POST /internal/enrich/{work_id}`). The `librarian-enrich` queue
  had `maxConcurrentDispatches=1000` while Cloud Run `librarian-api` was `512Mi / cpu 1 /
  containerConcurrency=80 / maxScale=2`. Cloud Run only scales out as an instance nears its concurrency
  target (80), so a burst of deep tasks gets packed onto ONE 512Mi instance and shares it → 54 OOM kills
  (`Memory limit of 512 MiB exceeded`), 404×503 + 26×500 on `/internal/enrich`.
- No data was lost — Cloud Tasks `maxAttempts=100` retried every work through (116/116 enrich + 122/122
  import-row eventually 200, 0 permanent failures), and the fast/import phase is unaffected so the History
  UI looked complete. But it was wasteful (every 503 = a discarded LLM-scout run = wasted API spend +
  latency) and fragile (a larger import could exhaust 100 attempts and silently drop a work's enrichment).
**Decision:**
- Throttle the `librarian-enrich` queue to `max-concurrent-dispatches=4` / `max-dispatches-per-second=5`,
  and raise Cloud Run `librarian-api` memory `512Mi → 2Gi` (revision `librarian-api-00014-c87`). Applied
  2026-06-23 via `gcloud` (no code change). The **queue's concurrency is the tuning knob** — it targets
  only deep enrichment; budget **~½ GiB instance headroom per concurrent deep scout** (4 ↔ 2Gi).
**Alternatives Considered:**
- Lower Cloud Run `containerConcurrency` to force tasks across instances → rejected: it's service-wide and
  would also throttle the user-facing API on the same service.
- Serial (`max-concurrent-dispatches=1`) → rejected: too slow to drain. `8 / 4Gi` → deferred: more
  per-instance cost while active; `4 / 2Gi` is the balanced point (~4× serial throughput).
**Consequences:**
- Eliminates the OOM storm + retry waste for future imports; the affected user's data was already fully
  enriched (no re-run needed). Memory is billed only while enriching (service scales to zero when idle).
- **On any re-deploy or queue edit these settings must be preserved or the OOM returns** — flagged on the
  coordination board and in `key_facts.md` (Production). Pure infra (gcloud), no PR/code change; verified live.

### ADR-052: Genre/mood-as-trope fallback strategy — fast-pass opt-out + membership-based prune (2026-06-24)
**Context:**
- Two-phase import double-layered genre/mood "fallback" tropes onto works that also have real scout tropes
  (see bugs.md 2026-06-23). Needed (a) a persist fix so it can't recur and (b) a one-time prune of the
  existing pollution — without stripping the genre/mood matching signal from works that have NO real tropes
  (works have no embedding; matching = trope+style vectors only — memory `work-representation-embedding-gap`).
**Decision:**
- **Persist (Shape B, PR #67):** the FAST pass *opts out* of writing fallback tropes (`write_fallback_tropes`
  row flag, default True; `enrich_fast` passes False) rather than write-then-delete. The deep pass is the
  single authoritative trope write — real tropes, or the genre/mood fallback only if the deep scout returns
  none. Single-pass/one-shot callers keep the default (stopgap preserved).
- **Prune distinguisher (PR #69, supersedes #67's):** a fallback is identified by **genre/mood membership by
  name**, NOT by `justification`. A `work_tropes` link is a fallback iff `clean_trope_name(name)` is a
  non-empty subset of the work's case-folded `{genres ∪ moods}`; delete only on works that retain ≥1 genuine
  trope (cleans to something outside genres/moods). This *guarantees* no narrative trope is deleted.
- **Non-goal (deferred):** the work-representation refactor (give `Work` its own genre/mood embedding) — only
  then delete genre-as-trope fallbacks on *trope-less* works. Until then they keep their fallbacks.
**Alternatives Considered:**
- Persist "delete-on-deep-pass" (write fallbacks, deep pass deletes NULL ones) → rejected: write-then-delete
  churn + a fragile "delete NULL inside persist" contract.
- Prune by `justification IS NULL` (the original #67 design) → **rejected after a prod dry-run flagged real
  tropes for deletion** (see the 2026-06-24 bug entry): many real scout tropes have NULL justification
  (semantic-collapse "attractor" canonicals), so justification conflates real tropes with fallbacks.
**Consequences:**
- Imports no longer accumulate a throwaway fallback layer; the corrected prune removes ~394 genre/mood links
  across 123 works while keeping every narrative trope. Trade-off: during the fast→deep gap (or a permanently
  failed deep pass) an imported book shows genres/moods but no tropes until deep enrichment lands.
- Surfaced a separate, larger trope-quality issue: **semantic over-collapse** (the attractor canonicals) —
  filed as GH #70 (enhancement). That, not the fallback layer, is the main lever on recommendation quality.

### ADR-054: Style radar via embedding-projection binning + additive degrade-safe /analysis fields (2026-06-25)
**Context:**
- The Analysis viz upgrade (#13) wanted a "style radar" for the user's reading. Literary style is stored
  CATEGORICALLY (`Style` name + 1536-d embedding, linked via `AuthorStyle`/`WorkStyle` with an
  `attribute_type`), but a radar needs a numeric magnitude per axis.
- Wanted a binning that's honest, maintenance-free, and a small backend deliverable alongside the frontend.

**Decision:**
- Bin each categorical style value to 0–1 by **projecting its existing `Style.embedding` onto a per-axis
  bipolar anchor pair** (low-pole vs high-pole phrase), embedded once in the same space
  (`gemini-embedding-001`, SEMANTIC_SIMILARITY, 1536-d) and cached:
  `score = clamp(dot(v−low, high−low) / dot(high−low, high−low), 0, 1)`. Module: `api/analysis_style.py`.
- Only the **8 ORDINAL** attributes go on the radar (pace, density, depth, inner_focus, humor,
  warmth [= `emotional_distance` inverted], lexicon, world_building); **NOMINAL** ones (tone, prose style,
  dialogue, perspective) have no axis → they feed a **style word cloud**. Per read-work style =
  author `AuthorStyle` baseline overlaid with `WorkStyle` overrides; aggregate over the user's shelf.
- Surface two **ADDITIVE** fields on `GET /analysis` — `style_radar` + `style_cloud` — no schema/migration.
  The endpoint MUST NEVER 500 from scoring: no embedder OR any embed failure (bad/invalid key, quota,
  network) → radar degrades to all-null; the anchor cache is published only on full success (atomic rebind,
  guarded by a `threading.Lock` + double-checked locking).

**Alternatives considered:**
- Curated phrase→score lookup per axis → rejected: brittle to new scout phrases, needs upkeep. Projection
  reuses embeddings already stored and is maintenance-free.
- Give `Work` its own embedding / numeric style columns → larger refactor (the work-representation gap), deferred.

**Consequences:**
- A radar that self-calibrates as the style vocabulary grows; nominal styles aren't wasted (cloud).
- One embedding burst for the 16 anchors per process (cached). CI has no valid key → radar shows
  null / "Gathering your style…" (frontend degrades); prod has the key, so it populates live.
- Deferred fast-follows: per-author comparison radar (#71), DB-level `Style.name` canonicalization (#72).
- Shipped via PR #74. The "never 500 on embed failure" rule was missed initially and caused a CI failure
  (see bugs.md 2026-06-25) — the degrade path now has a unit test.

### ADR-053: Library-picker search via a committed static directory snapshot (2026-06-25)
**Context:**
- The library-links feature (#57) needs a "find your library" search for the Settings picker. The initial
  cut called OverDrive's unofficial Thunder `GET /v2/libraries?query=` — but **that endpoint ignores the
  `query` param entirely**: `query=seattle`, `query=king county`, and even `query=zzzzznomatch` all return
  the identical full list of **12,963 libraries** (same `totalItems`, same first page). So the picker was
  just showing the first 24 of *everything* regardless of input ("not all libraries showing up"). Probed
  `q`/`search`/`searchTerm`/`libraryName`/`name`/`websiteName` (all ignored) and `/v2/libraries/search`
  (treated as a slug → "Library not found"). Libby's real autocomplete host (`autocomplete.api.overdrive.com`)
  is gated (403, even with a libbyapp Origin/Referer).
**Decision:**
- Ship a **committed static snapshot** `src/agentic_librarian/availability/library_directory.json` (12,963
  `{slug, name}` records, ~850 KB), regenerated by `scripts/fetch_library_directory.py` (pages the directory
  at perPage=100 — the cap — across ~130 pages). `availability/directory.py` loads it once (`lru_cache`) and
  `search(q)` does a case-insensitive substring filter, prefix-matches ranked first, capped at 25. The
  `/libraries/search` endpoint calls `directory.search` (no network, so the old `ThunderError`→503 path is
  gone). Removed the now-dead, query-ignoring `overdrive.search_libraries`; `fetch_media` (real availability)
  stays.
**Alternatives Considered:**
- **Live fetch + server cache (option B)** — fetch all ~130 pages on first search, cache with a long TTL,
  self-updating. *Pros:* new libraries appear without re-running a script; no committed data file. *Cons:*
  first search on a cold Cloud Run instance pays ~130 requests (~30s) against the flaky unofficial endpoint;
  per-instance cold caches under autoscale; more moving parts. **Deferred → GH #75.**
- **Find the real search param / autocomplete endpoint** — rejected: none exists publicly (query ignored;
  autocomplete host 403-gated); reverse-engineering it would be fragile.
**Consequences:**
- Search is instant and has **zero runtime dependency** on the broken/fragile endpoint. Trade-off: the
  snapshot is near-static but goes **stale** until `scripts/fetch_library_directory.py` is re-run (acceptable —
  libraries change rarely; a scheduled refresh is the natural middle ground, noted in #75). ~850 KB of
  reference data is committed to the repo. Isolation of the unofficial Thunder API is unchanged (still only
  `overdrive.py`, now availability-only).

### ADR-055: Same-origin Firebase auth helper via FastAPI reverse-proxy (fixes Safari-mobile sign-in) (2026-06-26)
**Context:**
- A Safari-mobile user could not load the app (#78): Firebase Auth threw "Unable to process request due to
  missing initial state … storage-partitioned browser environment." Root cause = **storage partitioning**, not
  a code bug. The SDK loads its OAuth helper from `https://{authDomain}/__/auth/{handler,iframe}`; `authDomain`
  is `agentic-librarian-prod.firebaseapp.com` while the app is served **same-origin from Cloud Run** on
  `librarian-api-….run.app` — a different registrable domain. The helper writes the OAuth "initial state" to
  `sessionStorage` on the `firebaseapp.com` origin; Safari ITP isolates that as third-party, so the state is
  gone on return. The error names `signInWithRedirect` even though we call `signInWithPopup` because Safari/iOS
  proactively inits the redirect/iframe path (`_shouldInitProactively`) — so popup↔redirect swaps don't help.
  This is industry-wide (Firefox TCP, Chrome Privacy Sandbox), not Safari-only; the only real fix is a
  **same-origin auth helper**.
**Decision:**
- Reverse-proxy Firebase's `/__/auth/*` helper through the FastAPI container that already serves the SPA, and
  set the browser `authDomain` to the app's **own** origin at runtime (`window.location.host`). New router
  `api/firebase_auth_proxy.py` (`GET /__/auth/{path}`) forwards to `https://{FIREBASE_AUTH_UPSTREAM}/__/auth/...`
  (env, default `agentic-librarian-prod.firebaseapp.com`) — **fixed path prefix + fixed upstream host, never
  derived from request input** (no open-proxy/SSRF). Async `httpx`, streamed passthrough of status/Content-Type/
  cache headers, relax upstream `X-Frame-Options: DENY`→`SAMEORIGIN`, registered **before** the SPA catch-all.
  Frontend `auth/firebase.ts` resolves `authDomain = window.location.host` on a non-localhost browser host (env
  fallback for dev/tests), keeps `signInWithPopup` primary, adds `getRedirectResult` on load. Needs **no Google
  OAuth client edits** (registered `redirect_uri` stays on `firebaseapp.com`; proxy forwards the final leg
  same-origin) and **no pipeline change**. Spec: `docs/superpowers/specs/2026-06-26-safari-mobile-auth-fix-design.md`.
**Alternatives Considered:**
- **Migrate frontend to Firebase Hosting (Option B)** — Hosting serves `/__/auth/` natively + free CDN + first-class
  custom domains, but adds a second deploy surface, API rewrites, and CORS/header-forwarding; too big for a
  production hotfix. **Deferred → GH #79** (the deliberate future-evolution path).
- **Stand up a custom domain now (Option C)** — forces a domain decision now to fix a bug and still needs the proxy
  (= A + a domain purchase). A nicer domain doesn't require Hosting anyway (Cloud Run domain mapping). **→ #79.**
- **Switch popup→redirect / vice-versa** — doesn't address partitioning; rejected.
**Consequences:**
- Fixes #78 for real Safari-mobile users with a small, bounded, reversible change on the existing single-origin
  architecture; pre-empts the same breakage on Chrome/Firefox. Auth state stays per-browser (no shared server
  state → no new concurrency class); the proxy is stateless (worst case a transient 502 for one user). Trade-off:
  auth-helper traffic (per-sign-in, tiny, cacheable) now rides Cloud Run instead of Firebase's CDN — fine at this
  scale; CDN-level static delivery is #79. **Forward-compatible:** runtime `authDomain` carries unchanged onto a
  future custom domain or Hosting migration; under Hosting the proxy can simply be retired.

### ADR-056: Word cloud rebuild on @isoterik/react-word-cloud's useWordCloud hook (2026-06-26)

**Context:**
- GH #83: the Analysis word clouds (signature tropes + style) shipped in the viz upgrade (#74) read like a ranked list — a `flex-wrap` of words sorted largest→smallest with big gaps, no packing, no rotation, and wordy/`/`-joined labels.
- We wanted a real packed "Wordle" cloud (spiral packing, rotation, accentuated size) without re-inventing the layout, while keeping our `--cat-*` palette + Literata + light/dark theming and degrading safely in jsdom tests.

**Decision:**
- Build on **`@isoterik/react-word-cloud` v1.3.0** (d3-cloud under the hood) via its low-level **`useWordCloud` hook**, rendering the SVG `<text>` ourselves so colour/font/theme stay under **className + CSS** control (SVG `fill` can't resolve `var()` as a presentation attr — same constraint as Recharts).
- Cloud-local text preprocessing (`wordCloudText.ts`): split `/`-joined labels, strip a leading article, merge case-insensitive duplicates summing counts. Full names are preserved everywhere else.
- Size via a power curve with a **width-responsive max** (`clamp(round(width*0.11), 36, 60)`) so the largest word caps ~38px on mobile / 60px desktop and never dominates a narrow column; deterministic ~70/30 rotation via a stable per-word hash.

**Alternatives Considered:**
- **`adorable-word-cloud`** → rejected: pre-1.0 (v0.1.2), untouched since Aug 2024, hard-depends on ALL of d3 (`^7.9.0`), no custom-render escape hatch for theming.
- **Raw `d3-cloud`** → same engine, but we'd hand-write the React glue, async handling, and types `react-word-cloud` already provides.
- **Python word-cloud packages** → rejected: generate a static raster, breaking the client-side JSON data flow and giving no light/dark theme reactivity.

**Consequences:**
- One new runtime dep (scoped d3 modules, not full d3). Drop-in: `<WordCloud items={Ranked[]} />` contract unchanged; both call sites + `AnalysisView` untouched.
- **`useWordCloud@1.3.0` silently ignores a `random` option** (it neither destructures nor forwards it to d3-cloud's `computeWords`), so a seeded layout isn't available via the hook — placement uses `Math.random` (fresh arrangement per page load; fine for a cloud). See bugs.md 2026-06-26. **Layout stability across re-renders/theme toggles instead comes from MEMOISING the hook inputs** (the mapped words + `useCallback`'d accessors) so the layout effect only re-fires on a real word/width change — not a seed.
- jsdom can't run d3-cloud's canvas, so the component degrades to a `role="img"` aria-summary that unit tests assert on; real visual proof is the QC harness (mobile + desktop, both themes).

### ADR-057: Canonical host via Cloud Run domain mapping on apex shelfwright.app (2026-07-11)
**Context:**
- #78 (ADR-055) set `authDomain = window.location.host`, so every serving hostname must be
  registered on the Web OAuth client or new users hit `redirect_uri_mismatch` (prod incident
  2026-07-06, bugs.md). Cloud Run exposes ≥2 hostnames; the durable fix is ONE canonical host.
- The product is now named Shelfwright; the operator registered `shelfwright.app` (Cloudflare).
- Options costed in the 2026-07-07 #79 spec: Firebase Hosting (~$1/mo, but splits the
  single-origin app and routes SSE through Hosting rewrites), Cloud Run domain mapping
  (~$1/mo, least change), LB + serverless NEG (~$19–20/mo floor).
**Decision:**
- **Cloud Run domain mapping on the apex `shelfwright.app`** (service `librarian-api`,
  `us-central1`). Single origin preserved — the #78 auth proxy and SSE `/chat` are untouched;
  `authDomain` simply resolves to the canonical host. `www` 301s to the apex at Cloudflare
  (proxied record + redirect rule; never reaches Cloud Run). Apex records are grey-cloud
  (DNS only) — orange-cloud blocks Google's managed-cert issuance.
- `run.app` hosts stay registered and serving through the transition; only
  `shelfwright.app` is handed out.
- **LB + serverless NEG documented as the trigger-based upgrade path** (SLA / unsupported
  region / CDN / custom TLS policy), additive swap — see the #79 spec.
**Consequences:**
- New-user OAuth registration churn ends: one canonical host, registered once
  (Firebase Authorized domains + Web OAuth redirect URI/JS origin).
- Accepts Cloud Run domain mapping's "Preview" status at friends-and-family scale.
- Rollback = delete the mapping; `run.app` never stops serving.

### ADR-058: Startup migration guard — a schema-behind deploy fails the revision (2026-07-12)
**Context:**
- deploy.yml has no alembic step by design (migrations are operator-manual, lift1 runbook), but
  nothing enforced the migrate-before-merge ordering: a merged migration PR deployed against an
  un-migrated prod DB → runtime 500s the /health smoke can't see (GH #92, 2026-07-02 review).
- Alternatives: a workflow-side DB query (hands prod DB credentials to CI) or a
  /health/migrations smoke assertion (checks only after traffic has shifted; and the deployer
  cannot mint Firebase tokens to pass the auth gate).
**Decision:**
- `db/migration_guard.py`, called in the FastAPI lifespan: compare the DB's `alembic_version`
  to the image's migration head (`alembic/` + `alembic.ini` now ship in the prod image). On
  mismatch (or missing alembic_version table, or ≠1 heads) the container exits → the Cloud Run
  revision never goes ready → the deploy fails while traffic keeps serving the old revision.
- DB **unreachable** only warns and continues: transient DB blips must not kill scale-from-zero
  cold starts, and the in-runner docker smoke boots with a bogus DATABASE_URL by design.
- `MIGRATION_GUARD=off|0|false` is the emergency bypass (e.g. deploying the fix for a bad
  migration). The test suite defaults it off in conftest; the guard's unit tests re-enable it.
- DB **ahead** (version unknown to the image) → warn + continue — preserves the runbook's
  migrate-before-merge window (old-revision cold starts keep serving after prod is migrated)
  and lets an older image roll back after a migration without `MIGRATION_GUARD=off`; only
  DB **behind** (known non-head revision) fails the revision.
**Consequences:**
- Migrations stay manual; the mismatch is now loud and self-rolls-back instead of silent 500s.
- Any deploy path is covered (workflow or manual gcloud), with no DB credentials in CI.
- A forgotten alembic COPY in Dockerfile.api fails the runner smoke test (guard raises on a
  missing script dir), so the guard cannot silently vanish from the image.

### ADR-059: Off-loop tool execution + sessions never span external calls (2026-07-12)
**Context:**
- ADK FunctionTool runs sync tools inline on the uvicorn event loop; every mesh tool was
  sync, the auth dependency did sync verify+DB per request, and import commit enqueued up
  to 2000 Cloud Tasks synchronously — one slow operation stalled every user on the
  instance (GH #93). Enrichment/availability held DB sessions open across scout/LLM/
  Thunder calls — minutes idle-in-transaction, pool exhaustion, a wide #95 TOCTOU window
  (GH #94). Chat's add-book ran the full 6-scout enrichment inline (~1-2 min in-chat).
**Decision:**
- `make_async_tool` (signature-preserving `asyncio.to_thread` wrapper) on all 11 mesh
  FunctionTools; auth's verify+DB body via to_thread (ContextVar set stays in the
  coroutine); Cloud Tasks clients cached at module level; commit's enqueue loop off-loop.
- The session rule: read-session → external work with NO session → fresh write-session
  that re-checks dedup. Applied to two_phase fast/deep, availability (three-phase batch),
  and the chat discovery tool.
- Chat add-book re-routed through the two-phase path (fast pass + queued deep pass) —
  user-approved contract: the Librarian announces background analysis and never anchors
  trope-based recommendations on a deep-pending work in the same turn. Tool contract
  (`str | None`) unchanged for the pipeline/Claude-backend callers.
- Pool overflow started at 5, not the PR-A interim 10 or a tight 2: embedding calls
  (search tools, persist-time standardize_trope/style) still ran inside sessions, so a
  Gemini 429 burst could stretch sessions to minutes even after #94.
**Consequences:**
- One user's enrichment can no longer brown out the instance; chat adds return in seconds.
- The deep pass now re-scouts outside any transaction: a late transient failure re-pays
  nothing already persisted, and dedup re-checks close the widened race window.
- to_thread runs on the loop's default executor, pinned to 32 workers in the API lifespan
  (Python's own default is min(32, cpus+4) ≈ 5-6 on Cloud Run's 1 vCPU — too small once
  every auth resolve and tool body shares it); the enrich/import queues (4/5 concurrent)
  remain the heavy-work throttles.
- GH #123 resolved (2026-07-12): `collect_embedding_texts` (etl/persist.py) warms
  `get_cached_embedding`'s LRU for every trope/style text a row will standardize BEFORE
  two_phase's write session opens, and the two mcp search tools warm their target
  trope/style texts before their read session opens — so persist-time
  standardize_trope/style and the search tools' `_get_embedding` calls are cache hits,
  not network round-trips, inside any session. Pool overflow tightened 5 → 2
  (db/session.py); two residuals remain: a warm-FAILURE degrades to in-session embeds via
  `_safe_standardize` (bounded: enrich queue is 4-concurrent; PR-D's requeue sweep is the
  recovery), and `/analysis` embeds ~24 radar anchor texts inside its session once per
  process (first call only, then module-cached).

### ADR-060: Constraint-backed get-or-create + advisory-lock works dedup + gated backfill (2026-07-12)
**Context:**
- Every "look up or create" site in the codebase was a bare SELECT-then-INSERT with no DB
  backstop — a race between two concurrent writers (two chat sessions adding the same book,
  a bulk import overlapping a fast-add) could both miss the SELECT and both INSERT,
  producing duplicate Authors/Narrators/Editions/reading_history/suggestions rows. `log_suggestion`
  (mcp) had ZERO dedup while the import worker's `_upsert_suggestion` was idempotent — this
  asymmetry is #88's likely root cause. Editions had three get-or-create sites, one
  (`etl/ingest.py`'s `to_models` generator) entirely unguarded. The corrected #69 fallback-trope
  predicate (structural: clean-name ⊆ genres∪moods, case-folded) had already taught the
  project's hardest lesson about backfills — never trust a sometimes-populated column as a
  class label; group on real relationships or normalized values instead.
- Works have no natural cross-table unique key (title+author spans the Work/Author/
  WorkContributor tables), so a DB-level unique constraint isn't directly expressible the
  way it is for Authors/Narrators/Editions.
- Spec: `docs/superpowers/specs/2026-07-12-phase6-3-data-integrity-design.md` (PR-D design,
  "Dedup backfill (THE USER GATE)"); user ground rule (2026-07-12): prod backfills/migrations
  require a dry-run report + user approval before applying.
**Decision:**
- **Five unique indexes** added in migration `48e3762d6c0c`: `uq_authors_name_lower`
  (`lower(name)`), `uq_narrators_name_lower` (`lower(name)`), `uq_editions_work_format`
  (`work_id, format` with `NULLS NOT DISTINCT`, PG16), `uq_reading_history_user_edition_date`
  (`user_id, edition_id, date_completed`), `uq_suggestions_active` (`user_id, work_id` WHERE
  `status = 'Suggested'` — partial, so historical non-active suggestions can repeat).
- **`works.deep_enriched_at` backfill** — the same migration structurally stamps
  `deep_enriched_at = now()` for every work with any `work_tropes` row (evidence it already
  went through the deep pipeline) immediately after adding the column, so the #97
  `--requeue-unenriched` sweep's first run reports only genuine gaps instead of the entire
  pre-existing catalog.
- **`db/get_or_create.py`** — two helpers, both SAVEPOINT-scoped (`session.begin_nested()`)
  so a rolled-back insert doesn't kill the caller's outer transaction: `get_or_create`
  (query → insert → on `IntegrityError` re-query with the SAME `filter_by(**filters)`
  predicate — correct when the DB constraint matches the filter 1:1, e.g. editions,
  reading_history, suggestions) and `insert_or_requery` (for sites where the constraint is
  NOT an exact-match predicate, e.g. authors/narrators' `lower(name)` unique fires on a
  case-variant that an exact `filter_by(name=...)` re-query would miss — callers keep their
  existing case-insensitive first-query and supply a matching `requery` callable for the
  recovery path). Adopted at: authors/narrators and all edition sites in `persist.py`,
  `two_phase.add_read_event`, reading_history (date-guard plus the constraint as backstop),
  suggestions (worker's `_upsert_suggestion` AND `log_suggestion`, which gains dedup for the
  first time — closes #88's root cause).
- **Works dedup = advisory lock, not a unique constraint**: `enrich_fast`'s write session
  runs `SELECT pg_advisory_xact_lock(hashtext(:k))` (key = normalized `title|author`,
  Postgres-only, skipped on sqlite test URLs) before its dedup re-check, serializing
  concurrent same-book creators without needing a cross-table DB constraint. Xact-scoped —
  released on commit.
- **The gated backfill** (`etl/dedup_backfill.py` + `scripts/clean_catalog.py
  --dedup-for-constraints`): a `plan_dedup`/`apply_dedup` split, read-only plan then apply-
  exactly-what-was-planned (never re-derived at apply time, so a row that vanished between
  plan and apply is skipped and counted, not silently re-planned). Seven structural
  classes — duplicate authors/narrators (survivor = most-linked row, ties broken by lowest
  id; Author/Narrator have no `created_at` so "oldest" isn't available), duplicate editions
  (per `(work_id, COALESCE(format,''))`), exact-duplicate reading_history and Suggested
  suggestions (keep oldest), orphan authors (zero `work_contributors` AND zero
  `author_styles` — computed against CURRENT state, not simulated against this run's own
  not-yet-applied author merges, by design), and `duplicate_works_report_only` (normalized
  title+author collisions — REPORT ONLY, never applied; work merges are complex enough that
  the operator triages case by case). Every class groups on real relationships or
  normalized values — never a sometimes-populated column — the #69 lesson made structural
  policy here, not just a one-off fix.
- **Apply-time drift cross-check** (added after PR-D's own dedup-planner review surfaced
  the gap): `--apply` is a separate invocation from the reviewed dry-run and re-plans from
  scratch against prod's live state. `plan_id_set`/`plan_delta` turn a plan into a per-class,
  **operation-tagged** id set (`merge:`/`repoint:`/`delete:`/`report:` prefixes, so an id
  whose operation flipped between the reviewed report and the fresh plan — e.g. a concurrent
  write turns a reviewed `repoint` into a `delete` — surfaces as a new token, not an
  unchanged bare id) and the CLI refuses `--apply` outright if the fresh plan contains
  anything the operator's reviewed report doesn't cover, writing a fresh report for
  re-review instead of proceeding. Ids that only vanished (rows deleted/changed since
  review) are fine and apply normally under ordinary `skipped_stale` accounting.
- Full rollout mechanics (pg_dump, dry-run review, apply, `alembic upgrade head`, merge,
  post-checks): `docs/runbooks/phase6-3-schema-rollout.md`.
**Alternatives Considered:**
- A cross-table unique constraint on works (normalized title+author) → rejected: the
  natural key spans Work/Author/WorkContributor, so it isn't expressible as a single-table
  index the way editions/authors/narrators are; the advisory lock plus a report-only sweep
  covers the concurrency risk without a schema contortion.
- Simulating orphan-author detection ahead of this run's own not-yet-applied merges →
  rejected: would make the plan's ids depend on the plan's own outcome, breaking "apply
  exactly what plan showed"; a documented re-run-until-clean loop is simpler and honest
  about the ordering.
- Trusting the dry-run's in-memory plan at apply time (no re-plan, no drift check) →
  rejected during PR-D's own review: the two invocations are minutes apart in practice and
  live traffic could create genuinely new duplicates in the gap: applying blind would defeat
  the user-gate's purpose.
**Consequences:**
- `IntegrityError` on a get-or-create race is now a recoverable re-query, not a crash or (in
  request-serving code) a 500 — the exact bug class the SELECT-then-INSERT pattern always
  risked.
- `log_suggestion` deduplicates for the first time, closing #88's likely root cause; note
  left on #88 when closing.
- The gate refuses to apply a stale or drifted plan rather than silently including duplicates
  the operator never reviewed — the review step is load-bearing, not decorative.
- `etl/ingest.py`'s `to_models()` generator remains intentionally unguarded: it's exercised
  only by `test_ingest.py`, has no live call site (the real ETL write path is
  `enriched_metadata` → `persist_enriched_work`, already constraint-backed), and is
  adjudicated dead code rather than retrofitted — a future removal is a cheap, safe cleanup,
  not urgent.
- The `duplicate_works_report_only` class is a standing, not-yet-actioned list after this
  rollout — a genuine work-merge tool is future work if the list proves large enough to
  matter in practice.
- Orphan-author counts can recur after any future author-merge (this rollout's own dedup
  apply included) — the runbook documents a re-run-until-clean loop rather than promising a
  single pass is sufficient.

### ADR-061: History format edit = edition repoint + targeted "format-completion" pass (2026-07-18)

**Context:**
- Users need to correct a history entry's format (e.g. logged as ebook, actually listened as audiobook). `format` lives on `Edition` — a SHARED catalog object under `uq_editions_work_format` — not on the per-user `ReadingHistory` row, and audiobook is enrichment-significant (narrators/audio minutes come only from the audiobook scouts; each format's edition carries its own ISBN/pages).
- Spec: `docs/superpowers/specs/2026-07-18-history-format-edit-design.md`; shipped via PR #147.

**Decision:**
- A format change **repoints** the history row to the sibling `(work_id, format)` edition (case-insensitive reuse, then `get_or_create`); the old edition is never mutated or deleted.
- Collisions with `uq_reading_history_user_edition_date` → **409, keep both rows** (human-readable detail; whole PATCH rolls back). Pre-checks run on computed final values BEFORE any ORM mutation (autoflush — see bugs.md 2026-07-19).
- Missing metadata on the target edition triggers an **async targeted completion pass** (`POST /internal/complete-edition/{work_id}?format=…`, existing enrich queue + OIDC gate): fast API scouts for ISBN/pages/audio on ANY format, audiobook scouts + per-narrator styles for audiobooks, and **never** `LLMTropeScout`/author-work-style scouting. Persistence via `merge_edition_and_narrators`, extracted verbatim from `persist_enriched_work`.
- Completion-pass "empty" (including all-scouts-failed — `ScoutManager.enrich` swallows per-scout exceptions) is a FINAL 200, not a retry: the entry is already saved and a later format change re-triggers completion.

**Alternatives Considered:**
- Mutate the edition's format in place → corrupts other users' history + collides with the unique index. Rejected outright.
- Reuse the full deep pass (`enqueue_enrichment`) → re-buys the paid trope/author-style LLM pass per flip, muddies `deep_enriched_at`/poison-retry gating tuned for first-time enrichment, and is unnecessary — ISBN comes from the fast scouts' format-matched edition selection. Rejected.
- Sweep-only backfill → the requeue sweep targets un-enriched works, not enriched works missing per-format edition data; gaps would persist indefinitely. Rejected.

**Consequences:**
- Any format flip costs at most 2 API calls (+2–4 LLM calls for audiobook targets) instead of a full deep pass.
- Orphaned editions (no history rows) are left in place as catalog objects — harmless, reused on the next flip to that format.
- A transient all-scouts outage during completion leaves the edition metadata-less until a later format change re-triggers it (documented, deliberate; no dedicated backfill sweep).
- Legacy non-vocab format strings (e.g. "Audiobook", "e-book") remain in the catalog; the PATCH vocab-gate stops NEW junk and the edit view is a per-entry manual remedy — bulk normalization is GH #81's scope.

### ADR-062: Neutral pick removal ('Removed') + history→picks auto-resolution (2026-07-19)

**Context:**
- GH #130: removing a pick for shelf-tidying (duplicate, already in history) forced a false
  "Not for me" ('Dismissed') — a permanent negative record the Librarian charter acts on.
- Books added to history (add-a-book, chat, CSV import) left matching active picks stale;
  only the "✓ I read this" prefill flow cleared its pick, client-side and racily.

**Decision:**
- New terminal `Suggestions.status` value `'Removed'` (UI: "Not right now"): neutral, keeps
  the row (provenance/audit), frees the partial-unique active slot so the title may resurface.
- Pick auto-resolution in `two_phase.add_read_event` (the choke point all four history-writing
  paths share): active 'Suggested' row for (user, work) flips to 'Read' in the same
  transaction, on both branches (incl. already_logged). `POST /books` reports `pick_resolved`.
- Chat parity: `'Removed'` added to MCP `_SUGGESTION_STATUSES` + one charter line (both
  prompt copies) distinguishing neutral removal from 'Dismissed'.

**Alternatives Considered:**
- Hard-delete the suggestion row -> rejected: loses pitch provenance, irreversible fat-finger.
- Post-submit dialog / auto-resolve+undo on add-a-book -> rejected (user decision): the book
  was just logged as read; no realistic "no" answer. "Not right now" is the escape hatch for
  the rare twin-work miss.
- Resolution only in POST /books -> rejected: chat + import adds would keep leaving stale picks.

**Consequences:**
- Adding to history resolves any pick active at that moment ('Suggested' → 'Read' in the same
  transaction); a pick created LATER for an already-read work stays active (re-pitching read
  books is a supported re-read flow).
- 'Suggested'→'Read'/'Removed' flips cannot violate the partial unique index (predicate binds
  only 'Suggested') — no guarded-flush choreography needed, unlike the history-edit path.
- The client-side exact-id resolution in the "✓ I read this" prefill flow is retained as a
  belt-and-suspenders for fast-pass twin-work misses; double-resolution is idempotent.
- Historical 'Dismissed' rows are not reclassified.

### ADR-063: Known local-only test failures are a named set; CI is their gate (2026-07-25)

**Context:**
- A fixed ~7-test set fails on the local host but passes in cloud CI, and reviewers kept
  burning time `git stash`-ing + rerunning to "confirm they're pre-existing" (guidance #9).
- Two distinct causes were conflated as "some env failures":
  (A) live external API/network tests, and (B) an optional dependency missing locally.

**Decision:**
- Treat these as a NAMED, enumerated set (listed in CLAUDE.md "Known local-only test
  failures"), not a vague "~5 failures". When ONLY this set fails locally, name it and
  proceed — do NOT stash-verify. Stash-verification is reserved for UNEXPECTED failures.
- Cause A is the `api_dependent` pytest marker (already defined in `pyproject.toml`,
  intent: "skipped in pre-commit"): 5 tests in `test_agent_runtime.py` +
  `test_metadata_scout.py`. The marker is the correct differentiator; `-m "not api_dependent"`
  deselects them locally today.
- Cause B is the optional `claude` extra (`claude_agent_sdk`) not installed locally: 2 tests
  in `test_usage_recording_backends.py` that import `agents.backends.claude` WITHOUT the
  `pytest.importorskip("claude_agent_sdk")` guard their siblings use, so they ERROR not SKIP.

**Alternatives Considered:**
- Keep documenting a manual "stash to verify" dance -> rejected: it is the cost this ADR
  removes; a named set + CI-as-gate is cheaper and less error-prone.
- Add a brand-new marker for the live tests -> rejected: `api_dependent` already exists and
  already tags all 5; the gap is wiring, not taxonomy.
- Delete/disable the live + claude tests -> rejected: they are real coverage that must run
  in CI (with keys + the extra); the fix is skip-when-unavailable, not removal.

**Consequences:**
- CI remains the gate for all of these (it has the keys and installs the extra).
- Durable fix (DONE, same #151 branch): rather than the originally-sketched conftest hook,
  the simpler and more consistent fix was to put CI's exact filter in pytest `addopts`
  (`-m "not api_dependent and not slow and not live"`) so a bare local `pytest` matches the
  CI selection and is green, plus `importorskip("claude_agent_sdk")` on the two unguarded
  Cause-B tests. Chosen over the conftest `pytest_collection_modifyitems` approach because CI
  already uses this exact `-m` string on the CLI (which overrides `addopts`), so local now
  simply mirrors CI with one line and zero coverage risk. Result: `test/unit` = 835 passed,
  5 skipped, 5 deselected, 0 failed. CLAUDE.md's "stash-verify" subsection is now retired.
- `db_integration` deselection is unaffected — that path still works via the conftest hook
  (it is orthogonal to the `-m` marker filter).

### ADR-064: Monetization architecture — metered tiers, provider-adapter payments, BYOK (2026-07-26; #155 merged 2026-07-31)

- **Context:** #100 (Phase 6.4) required cost/abuse guards before onboarding dozens; the
  user chose to monetize: supporter subscriptions + tips, BYOK free tier.
- **Decision (three stacked PRs):**
  1. **#155 (MERGED `ae252f8`): metering + tiers + budgets.** Every LLM/embed call writes
     an attributed `usage` row (Cloud Tasks payloads carry `user_id`; handlers `as_user`);
     computed tier `free|supporter|byok` (`core/tiers.py`, from `users.subscriber_until` +
     credential-row existence); env-tunable budgets enforced fail-open at chokepoints
     (`core/budgets.py`, counting queries — no Redis): chat turns/day (20/200/×10) + 4k-char
     cap → 429/422 pre-stream; import rows/import (300/2000) + one-in-flight (24h-bounded)
     → 413/409; per-user + GLOBAL grounded-call budgets with next-UTC-day `schedule_time`
     deferral (never burns the #97 give-up count). Spec:
     `docs/superpowers/specs/2026-07-25-metering-tiers-budgets-design.md`.
  2. **#156 (OPEN, ON HOLD): payment-provider integration**, built as Ko-fi but mostly
     provider-neutral: `payments` audit table, pure `core/entitlements.py` (+33d monthly /
     +370d annual, `max(now, current)+days`), operator CLI (`user subscribe`,
     `payments list/match`), `GET /api/account`, avatar-dropdown AccountMenu. The
     provider-SPECIFIC surface is one webhook adapter (`api/kofi.py`) + env + link URLs.
  3. **#157 (OPEN, ON HOLD): BYOK** — KMS-encrypted user Gemini keys; parameter-threaded
     routing (mesh via `_ByokGemini` ADK `api_client` override + behavioral canary; scouts
     via `api_key`/`key_source`); byok bypasses app-key budget gates; embeddings stay
     app-keyed; NO silent fallback (503/422/409/task-200 error taxonomy). Payment-provider
     independent (stacks on #156 only for AccountMenu + `/api/account`).
- **Provider decision REOPENED (2026-07-31):** Ko-fi lacks auto-renewing annual (worked
  around via one-off ≥$25 → +370d, no auto-renew) and sends no cancellation events.
  Investigating Buy Me a Coffee and Polar (merchant of record, real lifecycle webhooks)
  before merging #156/#157. The adapter-shaped design bounds the swap cost.
- **Residuals (documented in PR bodies):** queue fairness deferred; `kms_key_name` column
  write-only (never repoint `KMS_KEY_NAME` at a different CryptoKey resource);
  Message/Usage.created_at unindexed day-window counts; webhook request-size unbounded
  below Cloud Run's 32MB.
- **Ops at #155 rollout:** flip the shared Gemini key to paid tier (interim:
  `GROUNDED_CALLS_PER_DAY_GLOBAL=450` — free tier grounding is 500 RPD).
