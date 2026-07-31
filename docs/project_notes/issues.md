# Work Log (Issues)

This file tracks work history and ticket references.

## Templates

### YYYY-MM-DD - TICKET-ID: Brief Description
- **Status**: Completed / In Progress / Blocked
- **Description**: 1-2 line summary
- **URL**: Link to ticket or PR
- **Notes**: Any important context

## Log

### 2026-07-13 - PHASE 6.1–6.3 COMPLETE: scaling hardening groupings 1–3 — 19 of 27 issues closed
- **Status**: Completed — all shipped, deployed, and issue-closed with evidence; prod schema
  migrated and dedup-gated sequence executed live.
- **Description**: Three spec→plan→subagent-driven cycles closed #89–#99, #101–#103,
  #108–#112 (+#123; #88 root-cause-fixed, frontend half open). 6.1 (PR #120): deploy.yml 2Gi
  pin, durable squash fix, SQL backups+PITR, ADR-058 startup migration guard, stranded-import
  retry. 6.2 (PRs #121/#122, ADR-059): working embedding cache, outbound timeouts, pool
  hygiene + consolidation, all mesh tools/auth/enqueues off the event loop, sessions never
  span external calls, chat add-book re-routed two-phase with the Librarian announcing
  background analysis. 6.3 (PRs #124/#126, ADR-060): contributor-merge data-loss fix +
  SAWarning-as-error, shared trope predicate, garbage-title gate, honest reading dates,
  cache upsert, embeds warmed out of sessions; migration `48e3762d6c0c` (5 uniques, 9 FK
  indexes, 13 timestamptz, `deep_enriched_at` backfilled 454/454) applied via the operator-
  gated sequence in `docs/runbooks/phase6-3-schema-rollout.md` — snapshot → dry-run →
  operator approval → drift-refusing apply (3 duplicate suggestions deleted, prod otherwise
  clean) → alembic → merge → deploy (rev 00032-lqq).
- **URL**: PRs #120, #121, #122, #124, #126; ADRs 058–060; CLAUDE.md (verification mentality).
- **Notes**:
  - Remaining Phase 6: **6.4 cost guards (#100 — contains the paid-tier decision — and
    #113)**, 6.5 frontend resilience (#104–#107), 6.6 ops maturity (#114, #115).
  - Open follow-ups: 3 duplicate work pairs (report-only, operator triage — ids in the
    2026-07-13 dedup report); first `--requeue-unenriched` post-check not yet run; operator
    chat smoke recommended; watch pool 5+2 during the next bulk import and deep-scout
    latency vs the 120s Gemini timeout.
  - Process note: the review layering (per-task → whole-branch on the most capable model →
    adversarial pass) caught real bugs at EVERY layer that all previous layers missed —
    incl. a cold-start auth race, a review-gate bypass, cross-class dedup row loss, and a
    pre-migration-schema crash found only by running the gate tool against live prod.

### 2026-07-12 - LAUNCH: Shelfwright rebrand + canonical domain (GH #79) — SHIPPED; issue scrub
- **Status**: Completed — live and verified.
- **Description**: Product renamed **Shelfwright**; https://shelfwright.app is the canonical host
  (Cloud Run apex domain mapping, ADR-057; `www` 301s to apex at Cloudflare). Repo renamed to
  `jaydee829/shelfwright` (WIF provider condition + deployer-SA binding updated — GitHub's rename
  redirect does NOT cover OIDC claims; post-rename deploy verified green). PyPI name claimed
  (`shelfwright` 0.0.1 stub from `packaging/pypi-stub/`). UI branding = Shelfwright; "the Librarian"
  stays as the chat persona. Fresh-user sign-in verified on the new domain — a single canonical host
  permanently closes the #116 redirect_uri_mismatch bug class.
- **URL**: PRs #117 (launch) + #118 (rename follow-ups); operator runbook `docs/runbooks/shelfwright-launch.md`
- **Notes**: Issue scrub done the same day: closed #79 (this launch), #13 (Phase 4 web/analysis —
  shipped via #43/#44/#74/#86/#59), #8 (Phase 1 infra, long complete). Verified still open before
  keeping: #89 (deploy.yml still pins 512Mi) and #90 (squash-merge setting still `COMMIT_MESSAGES`).
  All 27 scaling-review issues (#89–#115) remain open — Phase 6 not started.

### 2026-07-02 - REVIEW: full-codebase scaling review (beta → dozens of users) — 27 issues filed (#89–#115)
- **Status**: Review completed; all findings filed as GitHub issues; fixes not yet started.
- **Description**: Five-track review (backend API, DB/core, enrichment pipeline, frontend, infra/CI) of
  `origin/main` (`96d1000`) focused on bugs, architecture, and handful→dozens scaling. 27 issues filed
  (#89–#115) + a root-cause comment on #88 (duplicate Picks = `log_suggestion` never dedups). Roadmap:
  new "Phase 6 — Scaling Hardening" section in `plan.md` prioritizes them.
- **URL**: https://github.com/jaydee829/shelfwright/issues (filter `created:2026-07-02`)
- **Notes**:
  - **Act first (prod risk)**: #89 deploy.yml still pins 512Mi — every deploy reverts the OOM fix (verify
    live memory NOW); #91 no Cloud SQL automated backups; #90 durable `[skip ci]` squash fix (repo setting).
  - **Scaling blockers**: #93 event-loop blocking (sync MCP tools inline in chat — one enrichment freezes
    the instance for all users); #94 sessions held across LLM calls; #95 no unique constraints behind
    get-or-create (dup works/authors under concurrent import); #102 pool config vs db-f1-micro (~25 conns).
  - **Verified bugs**: #96 contributor-drop on existing works (SAWarning repro); #98 ScoutManager never
    returns falsy → garbage titles become communal Works (the 404 path is dead code); #99 import rows
    stranded `pending` after failed enqueue; #97 deep pass returns success on total failure (extends DEBT-035).
  - **Cost/quota**: #100 enrichment is unmetered on the shared Gemini key, no per-user caps; deep-queue
    drain math ≈ 3-5 works/min → 3000-task backlog ≈ 10-17 h; paid tier is a prerequisite for dozens of users.
  - **Frontend**: #104 systemic missing error/retry (eternal "Loading…" on cold start; Settings can wipe
    saved libraries); #105 races; #106 import-view resilience; #107 history search.
  - **DB hygiene**: #108 tz-naive datetimes, #109 FK indexes, #110 availability_cache race/eviction,
    #111 `has_real_trope` reuses the unsafe `justification` distinguisher #69 corrected, #112/#113 MCP-tool
    data-quality fixes; #114 monitoring/alerts/rollback; #115 low-severity checklist.
  - Reviewer calibration: multi-tenancy discipline, `/internal` OIDC gate, SSE lifecycle, import state
    machine, and WIF/deploy gating were all verified **solid** — the gaps are concentrated in blocking-I/O
    architecture, config drift, and missing enforcement (constraints/quotas/alerts), not in auth or tenancy.
  - Note: the CD anomaly root cause (#90) was independently re-derived by the review; bugs.md had already
    diagnosed it 2026-06-22 — key_facts.md's stale OPEN note (fixed in this PR) caused the false "unsolved" state.

### 2026-06-26 - Safari-mobile sign-in fix: same-origin Firebase auth helper (#78) — SHIPPED + DEPLOYED
- **Status**: Completed — merged + deployed; prod proxy verified live. (Remaining: a human Safari-device sign-in test.)
- **Description**: Safari-mobile users couldn't load the app (Firebase Auth "missing initial state" — storage partitioning, because `authDomain` `firebaseapp.com` ≠ the same-origin Cloud Run app host). Fix = reverse-proxy Firebase's `/__/auth/*` helper through FastAPI + runtime `authDomain = window.location.host`, so the helper is first-party. Option A of the brainstorm. Implemented via subagent-driven execution (backend + frontend each spec+quality reviewed + final whole-branch review).
- **URL**: PR #85 (`4fa31b1`); bug #78; future-evolution enhancement #79; ADR-055; spec `docs/superpowers/specs/2026-06-26-safari-mobile-auth-fix-design.md`; runbook `docs/runbooks/safari-auth-fix-rollout.md`
- **Notes**:
  - No Google OAuth client edits and no pipeline change; the serving host is already in Firebase Authorized domains. New `api/firebase_auth_proxy.py` (async httpx, fixed upstream + `/__/auth/` prefix, registered before the SPA catch-all, relaxes `X-Frame-Options`→`SAMEORIGIN`, path-traversal guard, forwards UA/XFF for anti-abuse). `httpx` promoted to a runtime dependency.
  - **Gemini review** addressed (`50980de`): the HIGH `X-Frame-Options` case-insensitivity flag was a non-issue (httpx lowercases header keys — verified on 0.28.1; added a mixed-case test); UA/XFF forwarding + path-traversal guard added.
  - **Deployed** 2026-06-26 (CD auto-fired on `4fa31b1`, no anomaly); prod proxy verified: `GET https://librarian-api-…run.app/__/auth/iframe.js` → 200 `text/javascript` (first-party, not the SPA shell).
  - **#79** captures the deliberate future path (custom domain + CDN, optionally Firebase Hosting). Option A is forward-compatible — runtime `authDomain` carries onto a custom domain/Hosting unchanged; under Hosting the proxy can be retired.
  - Root cause is industry-wide (Firefox TCP, Chrome Privacy Sandbox), not Safari-only — this is best practice, not a band-aid. See bugs.md 2026-06-26.

### 2026-06-26 - Line-ending normalization (`.gitattributes` `* text=auto eol=lf`)
- **Status**: Completed — PR #80 (`4f239fc`) merged.
- **Description**: `core.autocrlf=true` on Windows produced persistent CRLF-vs-LF churn (every text file showing as modified in `git status`). Added `* text=auto eol=lf` + binary rules and renormalized the 5 files that had CRLF committed in the index (line-ending-only). Kept separate from the Safari fix PR to keep that diff focused.
- **URL**: PR #80

### 2026-06-25 - Library Links + Live Availability (#57) — SHIPPED + DEPLOYED
- **Status**: Merged + deployed; migration applied on prod; working live.
- **Description**: "Where to get it" for recommended books — a live Libby/OverDrive availability badge for the user's saved libraries, free→local→retail links (Libby/Hoopla/Bookshop/Amazon), a `check_availability` chat MCP tool, and a Settings library picker. Subagent-driven (10 TDD tasks + per-task spec/quality review + final whole-branch review).
- **URL**: PR #73 (`3039e75`), search-fix PR #76 (`06d3660`); enhancement #75; ADR-053
- **Notes**:
  - Architecture: one isolated unofficial-Thunder client (`availability/overdrive.py`, `x-client-id=dewey`, swappable for the official partner API) + read-through `availability_cache` (4h TTL) shared by the recs REST endpoint AND the MCP tool + pure `links.py` (links never depend on Thunder → outage degrades to links-only) + `user_libraries` table (public slugs, not the keyring). Migration `c4f81a2d9b6e` (applied on prod). Spec/plan/runbook under `docs/superpowers/...2026-06-25-library-links-availability*` + `docs/runbooks/library-links-rollout.md`.
  - **#76 (search fix):** Thunder's `/v2/libraries?query=` **ignores the query param** — returns all ~12,963 libraries for any input (even gibberish); Libby's autocomplete host is 403-gated. So the picker only ever showed the first 24 of everything. Fix = a committed `{slug,name}` snapshot (`scripts/fetch_library_directory.py` → `availability/library_directory.json`) filtered server-side (`availability/directory.py`). Also moved the Settings Save button into the header. ADR-053; live-fetch alternative = #75.
  - Gemini-reviewed both PRs (non-blocking; logging + `requests` `params=` + single-pass sort applied). A CI-only `DetachedInstanceError` in the MCP-tool db_integration tests was caught + fixed (bugs.md 2026-06-25).
  - **Deferred (separate future issues):** checkout/holds + a Hoopla availability badge → need an OverDrive/Hoopla **partnership** (business gate, not engineering); #75 (live-fetch+cache directory); #56 (inbound web-link reading).

### 2026-06-24 - Catalog QC: contributor dedup + trope-name cleaning + fallback prune
- **Status**: Code merged; operator backfills partially applied (see Notes)
- **Description**: A run of backend catalog-quality cleanups on the bench (off `main`), all subagent-driven + Gemini-reviewed.
- **URL**: PRs #63, #64, #67, #69; enhancement #70
- **Notes**:
  - **#63** (`fix/author-trope-cleanup`) — dedup duplicate `Author`/`Narrator` rows (case/whitespace variants, role-preserving) + `clean_trope_name` + 3 persist guards + `scripts/clean_catalog.py` (`--contributors`/`--tropes` + `is_prod_url`/`--yes`). In-flight fixes: trope-split link fan-out P0, CLI dry-run ordering, deterministic survivor (CI flake), in-place re-points. **Operator applied `--contributors` on prod.**
  - **#64** (`ae0bc45`) — `clean_trope_name` preserves genuine free-text tropes **verbatim** (only genre/mood slugs are canonicalized/dropped); the genre pipeline was mangling real tropes (`/` split, casing). Dirty-trope set dropped 506→35, embedding calls 308→16. Perf nit: merged maps hoisted to module constants.
  - **#67** — two-phase fallback layer fix, "Shape B" + `--prune-fallbacks` (bugs.md 2026-06-23 / ADR-052).
  - **#69** (OPEN, dry-run validated) — corrected the prune distinguisher to genre/mood membership after a prod dry-run caught it deleting real tropes (bugs.md 2026-06-24). **✅ Operator APPLIED `--prune-fallbacks` + `--tropes` on prod 2026-06-24 (run from the #69 branch). ⚠️ Still MERGE #69 — `main`'s prune is still #67's unsafe `justification IS NULL` version until it lands.**
  - **#70** — enhancement: semantic over-collapse of tropes (the ~14 attractor canonicals). The real recommendation-quality lever; deferred.

### 2026-06-23 - FRONTEND-NEXT: frontend backlog / next directions
- **Status**: Open (backlog — picked from a "what's next" review; frontend is visually in a good place after Visual Identity v2 + nav/import polish)
- **Description**: Candidate next frontend work, split by whether it needs the backend bench.
- **URL**: GitHub issues #13, #57, #68, #56
- **Notes**:
  - **Frontend-only (no bench dependency):**
    - **Analysis viz upgrade (GH #13 "Phase 4: Web Interface and Analysis")** — `/analysis` already returns `top_tropes`/`genres`/`moods`/`formats`/`authors`/`narrators`; build a **Trope Cloud + charted genres/moods/formats** with these. (Adds Recharts, or do it CSS/SVG to stay dependency-light — a design choice.) Recommended starting point.
    - **"Links to works" MVP (GH #57)** — "Find on Amazon/Libby/Hoopla" buttons on rec/history cards generated from title+author search URLs (no backend). Deep links via ISBN need the bench later.
    - **GenreIcon regex tightening** — newly unblocked now that the bench standardized genres (tag-cleaning PRs #60/#61/#63/#64); ensure every canonical genre maps to an icon. See memory `genreicon-regex-revisit`.
    - **Visual Identity v2 small fast-follows** — `NewMarker kind="enriched"` (teal) variant; font `<link rel=preload>` + weight subsetting.
  - **Frontend + a small bench ask (coordinate):**
    - **"Why this rec" highlight + trope chips on Recommendations (GH #68)** — the reserved `.chip--special` glow tier (designed in VI v2 PR #59, currently unused) wired to real data; needs `tropes` + a "why"/driver flag in the `GET /recommendations` payload (additive, same precedent as the `genres` field). See memory `visual-identity-v2-design`.
    - **Author-Style radar (part of GH #13)** — needs `/analysis` to expose author/narrator style attributes (in the DB, not in the payload yet).
  - **Backend-heavy (NOT frontend):** GH #56 (read a URL in chat → enrich the listed books) — explorer/scout work for the bench.

### 2026-06-15..17 - BETA-FEEDBACK: ship + deploy operator's beta feedback (A1–E1)
- **Status**: Completed + deployed
- **Description**: Triaged the operator's friends-and-family beta feedback into 8 items and shipped each
  via its own brainstorm→spec→plan→subagent-driven PR (all Gemini-reviewed, squash-merged):
  #49 A2 default-3 recs + D1a Librarian `check_reading_history`; #50 A1 ≥1-new novelty guarantee +
  A3 re-read labels; #51 B1 chat activity trail; #52 C1/C2 enrichment visibility + tropes in History;
  #53 D1b history edit/delete; #54 E1 dark mode.
- **URL**: specs/plans under `docs/superpowers/{specs,plans}/2026-06-{10,15,16,17}-*`
- **Notes**: Deployed to prod 2026-06-17 — image `librarian-api:3d2dafe` via manual `workflow_dispatch`
  (see bugs.md CD anomaly). No migrations/new secrets for #50–#54. Follow-ons: DEBT-035 (below);
  "Visual Identity v2" redesign (extends the ADR-049 token layer).

### 2026-06-16 - DEBT-035: Detect stuck/failed background enrichment
- **Status**: Open (deferred from C1/C2 enrichment-visibility by decision)
- **Description**: Enrichment status is DERIVED from trope presence (no enriched_at/status column), so a deep pass that failed, found no tropes, or whose Cloud Task never fired is indistinguishable from one still in flight — History shows "Enriching…" indefinitely.
- **URL**: spec `docs/superpowers/specs/2026-06-16-enrichment-visibility-design.md` (D5)
- **Notes**: Future fix needs a creation/enqueue timestamp (Work has no created_at) + a timeout sweep or explicit status to flag long-pending works as failed/retryable; pairs with a retry action alongside D1b (history edit/delete).

### 2026-01-27 - MEM-001: Initialize Project Memory
- **Status**: Completed
- **Description**: Setting up `docs/project_notes/` and memory protocols.
- **URL**: N/A

### 2026-01-30 - ST-002: Phase 1 Step 3 - DVC & Orchestration Refactor
- **Status**: Completed
- **Description**: Fixed DVC tracking, refactored Dagster orchestration, and added unit tests for sensors.
- **URL**: [walkthrough.md](file:///C:/Users/Justin.Merrick/.gemini/antigravity/brain/49c17630-e020-4fd4-b696-bf6db697431f/walkthrough.md)

### 2026-01-30 - ETL-003: Phase 2 Step 1 - Ingest Refactoring
- **Status**: Completed
- **Description**: Refactored CSV cleaning, implemented `HistoryIngestor` class for model mapping, and updated Dagster orchestration.
- **URL**: [walkthrough.md](file:///C:/Users/Justin.Merrick/.gemini/antigravity/brain/5ce3f9cf-5c02-4fc9-b36b-dfd010ef9c9c/walkthrough.md)

### 2026-01-30 - ETL-004: Phase 2 Step 2 - MultiSourceScout Implementation
- **Status**: Completed
- **Description**: Implemented `MultiSourceScout` with dual-pathway audiobook scouting (Audible Scraping vs Direct LLM Knowledge) and MLFlow logging.
- **URL**: [walkthrough.md](file:///C:/Users/Justin.Merrick/.gemini/antigravity/brain/0e68aa32-bb55-4a26-824f-540a33780cf3/walkthrough.md)
- **Efficacy Test Plan**:
    1. Run `test_efficacy.py` in an environment with valid `GOOGLE_SEARCH_API_KEY` and `HARDCOVER_API_KEY`.
    2. Review MLFlow experiment `audiobook_scouting_comparison`.
    3. Metrics to compare: `pathway_a_latency` vs `pathway_b_latency`, `pathway_a_minutes` accuracy vs `pathway_b_minutes`.
    4. Goal: Determine if Direct LLM Knowledge (B) is reliable enough to replace Scraping (A).

### 2026-02-06 - ETL-005: Robust Metadata Year Parsing
- **Status**: Completed
- **Description**: Refactored `metadata_scout.py` to use regex for year extraction, improving robustness against various date formats.
- **URL**: [walkthrough.md](file:///C:/Users/Justin.Merrick/.gemini/antigravity/brain/d22419e7-1275-4f93-9170-efa7436e44cf/walkthrough.md)

### 2026-02-06 - ETL-006: Phase 2 Step 3 - TropeManager Implementation
- **Status**: Completed
- **Description**: Implemented `TropeManager` for semantic tag deduplication and embedding using TDD. Achieved 100% test coverage.
- **URL**: N/A

### 2026-02-06 - ETL-007: Phase 2 Step 4 - Dagster Orchestration
- **Status**: Completed
- **Description**: Orchestrated the ETL flow with Dagster using modular assets (`raw_history`, `enriched_metadata`, `vectorized_tropes`). Integrated `MultiSourceScout` and `TropeManager`.
- **URL**: N/A

### 2026-02-06 - ARC-008: Coarse-Grained MCP Strategy
- **Status**: Completed
- **Description**: Refined Phase 3 architecture to use a Hybrid Data Access model and Coarse-Grained MCP tools to handle complex transactions in Flow 2.
- **URL**: N/A

### 2026-02-06 - ARC-009: Phase 2.5 - Abstract Scout Refactor
- **Status**: Completed
- **Description**: Refactored metadata scouting into a hierarchical abstract architecture (`BaseScout` -> `APIScout`/`LLMScout`).

### 2026-02-13 - REC-010: Phase 3 Step 1 - MCP Server Core
- **Status**: Completed
- **Description**: Initialized FastMCP server with `find_recommendations` and `log_suggestion`. Implemented Dual-Verification Pattern with shared JSON fixtures.

### 2026-02-13 - REC-011: Phase 3 Step 2 - Agent Mesh Foundation
- **Status**: Completed
- **Description**: Refactored the Agent Mesh to use the Google ADK "Reasoning Dispatcher" pattern. Librarian now delegates to Analyst, Explorer, and Critic via `AgentTool`. Integrated coarse-grained MCP tools as specialist capabilities.
- **URL**: N/A

### 2026-02-17 - ARC-012: Phase 2.6 - ETL Hardening & Style Enrichment
- **Status**: Completed
- **Description**: Refactored contributor roles, implemented structured Style/Trope models with vectorization, and added "Informed Scouting" context.
- **URL**: N/A
- **Notes**: Includes `LLMTropeScout` and `StyleScout` with 9 author attributes.

### 2026-02-17 - REC-013: Phase 3 - Recommendation Engine Completion
- **Status**: Completed
- **Description**: Implemented temporal re-read logic, hybrid trope/style search, unacted suggestion persistence, and Trope-RAG justifications.
- **URL**: N/A
- **Notes**: Completed the full Specialist Mesh feedback loop (Librarian, Analyst, Explorer, Critic).

### 2026-05-30 - ENV-014: Centralize Dev to WSL2 + Compose Devcontainer
- **Status**: In Progress
- **Description**: Wired the dev container to `docker-compose.yml` (app + db + mlflow on one network), completed `.env.example`, added `.dockerignore`, and ignored `mlruns/`. Centralizing development onto a single machine under Claude Code.
- **URL**: https://github.com/jaydee829/agentic_librarian/pull/15
- **Notes**: Prior machine used a Conda env + a non-Docker agent harness; this machine uses the compose-based devcontainer (deps installed `--system` in-container, no conda). `key_facts.md` Local Development updated accordingly.

### 2026-05-30 - ENV-015: MVP Wiring Gaps (Deep-Dive Findings)
- **Status**: In Progress
- **Description**: Repository deep dive found two "implemented-but-not-wired" gaps to close for a working MVP.
- **URL**: N/A
- **Notes**:
    1. **[Resolved]** `StyleScout` and `LLMTropeScout` were implemented + unit-tested but never registered in `create_scout_manager()`, so live enrichment produced empty styles and no curated tropes (`vectorized_tropes` fell back to genres/moods). Now registered at priorities 5/6 (StyleScout after the audiobook scouts so `narrator_names` is populated first). Covered by mock unit tests + an `api_dependent` live smoke test.
    2. **[Resolved]** `ExplorerAgent` (`agents/services.py`) had no search tool wired. Spec 2 (PR #20) added `GoogleSearchTool(bypass_multi_tools_limit=True)` + its own `EXPLORER_MODEL` (gemini-2.5-flash) + an anti-hallucination instruction; grounded web discovery verified live (real 2024 titles). `search_strategies.py` left as-is.

### 2026-05-31 - REC-017: Audiobook Smoke Coverage Under Free-Tier Quota Constraint
- **Status**: Open (Spec 5 or later)
- **Description**: The Flow 1 smoke test cannot include an audiobook row without risking `429 RESOURCE_EXHAUSTED` on the free-tier Gemini API (20 requests/day). The audiobook path (AudiobookScout + DirectKnowledgeScout + StyleScout + LLMTropeScout) fires 4+ LLM calls per row, exhausting the daily budget on active dev days.
- **URL**: N/A
- **Notes**: Two options: (a) upgrade to a paid Gemini tier; (b) add a separate fixture-driven audiobook smoke that pre-seeds Audible HTML and only exercises the scraping + JSON extraction path (no quota-consuming generate_content calls). The physical-book smoke in `test_flow1_etl_live.py` covers the happy path end-to-end; audiobook is left for a future tier upgrade or fixture-based approach. See bugs.md for the rate-limit failure details.

### 2026-05-31 - REC-016: Spec 4 Requirements Surfaced by Spec 2 Live Runs
- **Status**: Resolved in Spec 4 (ADR-040)
- **Description**: Live Librarian→Explorer runs confirmed the Explorer's grounded discovery works and surfaced recommendation-flow work for Spec 4.
- **URL**: N/A
- **Notes**:
    1. **Web-candidate de-dup (Case 1)**: when the Explorer surfaces a title already in the DB (a prior suggestion or read book), resolve it to the existing `Work` (title/author match) and prefer the DB entry over the bare discovery.
    2. **Scout-enrichment of new discoveries (Case 2)**: a web-discovered book not in the DB has no tropes/styles; enrich it via the `ScoutManager` (Flow 1) so the Critic can rank/justify it — new machinery (e.g. an `enrich_work` tool, or a discover→enrich→persist→rank step).
    3. **Librarian one-shot orchestration**: `run_recommendation` is one *call*, not a forced answer; the conversational Librarian sometimes asks a clarifying question or delegates non-deterministically. Tune so a one-shot recommendation request commits to a best-effort recommendation.
    4. **Multi-agent final-response extraction**: in some delegation runs the Librarian ends on a tool/transfer event with no text, so `asend` returned "(no response)". Harden the runtime's final-text extraction for multi-agent chains.
    - Interim safety net shipped in Spec 2: `get_work_details` guards non-UUID ids (see bugs.md).
    - Items 1 (web-candidate de-dup) and 2 (scout-enrichment of discoveries) are handled by `enrich_and_persist_work`; items 3 (one-shot determinism) and 4 (final-response extraction) are handled by the fixed-order SequentialAgent pipeline returning `state['recommendation']` (ADR-040).
    - **[2026-06-01]** Item 3 (one-shot commitment) addressed: the SequentialAgent already enforces step
      order, and CRITIC_INSTRUCTION now tells the Critic to always commit to a best-effort recommendation
      (never ask a clarifying question / never return empty). Item 4 (multi-agent final-text extraction)
      remains open — re-evaluate after a post-enrichment-hardening e2e.

### 2026-05-31 - REC-018: Spec 4 Follow-ups (Critic ranking + live e2e verification)
- **Status**: Item 2 **Resolved (2026-06-02)** — the full live chain ran end-to-end: the ADK backend (`test_recommendation_e2e`) and the Claude backend (`test_claude_e2e`) each produced a justified recommendation and logged a Suggestion (verified in the DB). Item 1 (explicit candidate ranking) remains **Open** as a recommendation-quality refinement.
- **Description**: Refinements surfaced by the Spec 4 final review.
- **URL**: N/A
- **Notes**:
    1. **Explicit candidate ranking**: the pipeline gathers `state["candidate_ids"]` (internal + enriched discoveries) but the `CriticAgent` re-derives candidates via its own `search_internal_database`. Enriched discoveries are still rankable because they are persisted with embeddings (the Critic's vector search surfaces them), but ranking is not *guaranteed* to include a specific discovery. Refinement: feed `candidate_ids`/`targets` into the pipeline Critic (without disturbing the shared conversational Critic) and have it `get_work_details` each id. Pairs with the LoggerAgent TODO (log the Critic's ranked top pick, not gather-order `candidate_ids[0]`).
    2. **Live e2e verification pending**: `test_recommendation_e2e.py` (api_dependent) could not be live-verified during Spec 4 — Gemini free-tier quota was exhausted (429). Each pipeline piece is covered by deterministic offline tests, and the critical `CriticAgent` `output_key="recommendation"` wiring is asserted offline (`test_pipeline_assembly.py`), but the full live chain (real Analyst/Explorer/Critic) must be run once when quota is available to confirm a justified recommendation is produced and logged.

### 2026-06-01 - REC-019: Verify Claude Explorer web-search tool name on first live run
- **Status**: Resolved (2026-06-01) — first live `test_claude_e2e` PASSED. `allowed_tools=["WebSearch"]` is correct: Claude's Explorer performed real web searches and surfaced live titles (e.g. *A Taste of Gold and Iron*, *The Shadows Between Us*); the Critic produced a full Trope-RAG report and `log_suggestion` persisted it. Run on the Max subscription via the in-container `claude` CLI (auth persisted in the `claude_auth` volume + `CLAUDE_CONFIG_DIR`). Gemini enrichment sub-step hit the 2.5 daily cap and degraded gracefully (didn't fail the run).
- **Description**: The Claude backend's Explorer step passes `allowed_tools=["WebSearch"]` (Claude Code's built-in web-search tool; `allowed_tools` uses PascalCase CLI tool names, cf. `["Read", "Grep"]`). This cannot be verified without an authenticated `claude` CLI.
- **URL**: N/A
- **Notes**: On the first live `test_claude_e2e` run (after `claude` auth), confirm the Explorer actually performs a web search. If it does not, the correct identifier may be the server-tool name `"web_search"` — change `agents/backends/claude.py` accordingly. The shared `EXPLORER_INSTRUCTION` was made tool-agnostic ("use your web search tool") so it reads correctly for both the ADK (`google_search`) and Claude (`WebSearch`) backends.

### 2026-06-01 - REC-020: Pipeline should tolerate transient LLM 5xx (live e2e finding)
- **Status**: Resolved (2026-06-01) — shared `HttpRetryOptions` (5 attempts, exp backoff, codes 429/500/502/503/504) applied to every Gemini call: ADK agents via `Gemini(retry_options=...)` (ADK plumbs it into the genai client's `HttpOptions`, google_llm.py) and scout/embedding `genai.Client(http_options=...)`. Paired with model routing (ADR-042): non-grounding mesh on gemini-3.1-flash-lite, grounding on gemini-2.5-flash — two separate 15-RPM free buckets instead of one. Google Books enrichment-burst backoff is the remaining piece (tracked under REC-016).
- **Description**: The first full live recommendation e2e (ADK backend) ran the whole chain (Analyst → Explorer found real 2024 romance titles → enrichment → Critic) but crashed when a Gemini call returned `503 UNAVAILABLE` ("model experiencing high demand"). The error propagated uncaught from `LlmAgent` through `run_recommendation`. Google Books also 429'd from rapid enrichment calls (degrades gracefully — discoveries skipped).
- **URL**: N/A
- **Notes**: A transient 5xx from one agent's LLM call should not crash the whole recommendation. Add retry-with-backoff on transient 5xx (ADK retry config or a wrapper) for the mesh/pipeline LLM calls; consider light rate-limiting / backoff for the per-discovery Google Books enrichment burst. Re-running when Gemini is not under load should succeed. (The array-truthiness bug surfaced in the same run is fixed + regression-tested.)

### 2026-06-01 - REC-021: Style "name" can be a dict — enrich_and_persist_work crashes on persist (live e2e finding)
- **Status**: Resolved (2026-06-01) — StyleScout output normalized to {attr: str} (_flatten_style_map hoists one nested level, drops non-strings) and persist_enriched_work guards every style loop via _iter_style_items (skips+warns on non-string values). Unit + db_integration regression tests.
- **Description**: During the successful PR #22 live e2e, `enrich_and_persist_work` for one discovered book failed with `psycopg2.ProgrammingError: can't adapt type 'dict'`. A Style was looked up/created with `styles.name` = a nested analysis dict (`{'prose_density': '…', …}`) instead of a string label. The StyleScout's structured style analysis is being passed through as the Style *name*. Caught by the `except Exception` in `enrich_and_persist_work` (degrades gracefully — that work's styles aren't persisted; the run continued and still produced a recommendation).
- **URL**: N/A
- **Notes**: Fix in `persist_enriched_work` / the StyleScout→Style mapping (mcp/server.py + scouts): a Style needs a scalar string name; the prose-density-style analysis object should map to `Style.description` or be decomposed into named style rows, not used as the name. Add a regression test that persists a discovered work whose StyleScout output is a dict-shaped style.

### 2026-06-01 - REC-022: Hardcover enrichment silently returns nothing — over-strict `_eq` filters (live e2e finding)
- **Status**: Resolved (2026-06-01) — HardcoverScout rewritten as a 2-step fuzzy search->books-by-id lookup with author-matched, read-count-ranked hit selection (companion titles excluded); format/country preference applied in Python. ADR-043. Live-verified for a known title.
- **Description**: Verifying the PR #22 e2e, HardcoverScout (priority 1, the *primary* physical/ebook + audiobook-length/mood source) contributed nothing for every web-discovered book. The token is valid (HTTP 200), but the GraphQL query filters editions with three exact-match `_eq` clauses — `book.title _eq $title` AND `edition_format _eq $format` ("ebook") AND `country.name _eq "United States of America"` — which almost never all match real data: e.g. "The Spanish Love Deception" *exists* (8 editions) but their `edition_format` values are `""`/`null`/`"Paperback"` (never lowercase "ebook") and most lack the US country row; "The Serpent & The Wings of Night" returns 0 on exact title (the `&`/article differs from Hardcover's stored title). `_make_request` swallows non-200s, so failures are invisible. Note: Hardcover blocks `_ilike` ("not permitted on this server"), so fuzzy title matching needs another approach (normalized exact title, or `book` lookup then editions).
- **URL**: N/A
- **Notes**: Loosen the query in `HardcoverScout.search` (metadata_scout.py:195): drop/relax the `edition_format` and `country` `_eq` filters (select editions, then prefer a format/country match in Python — the scout already loops editions for format selection); normalize the title or query `books` by title then fetch editions. Add a test asserting a known title returns a non-empty result. Until fixed, ebook/physical discoveries rely on Google Books (rate-limited, REC-016) + LLM scouts only.

### 2026-06-01 - REC-023: A single scout's exception aborts the whole multi-scout enrichment (e2e verification finding)
- **Status**: Resolved (2026-06-01) — `ScoutManager.enrich` now wraps each `scout.search(...)` in try/except (warn + continue), so one scout raising no longer discards metadata already gathered by earlier scouts. Unit test `test_enrich_isolates_a_failing_scout`. **Verified end-to-end on the Claude backend:** with the Gemini grounding scouts (StyleScout/LLMTropeScout) hitting the daily 429 cap, four web-discovered works (Divine Rivals, Things We Left Behind, Cover Story, Kiln Me Softly) persisted with **Hardcover-sourced descriptions + page counts** (which were empty before this branch).
- **Description**: Verifying REC-021/REC-022 via a Claude-backend recommendation e2e, every `enrich_and_persist_work` aborted with `enrich_and_persist_work error: 429 RESOURCE_EXHAUSTED (gemini-2.5-flash)`. The REST scouts swallow errors (`_make_request` → `{}`), but the LLM scouts let `generate_content` exceptions propagate; `enrich()` had no per-scout isolation, so a grounding-scout 429 propagated out and the caller discarded the ENTIRE book — including the priority-1 Hardcover data gathered first.
- **URL**: N/A
- **Notes**: This is the defensive fix. The deeper issue (the Claude backend still depending on Gemini `generate_content` for enrichment) is REC-024.

### 2026-06-01 - REC-024: Claude backend's enrichment scouts still use Gemini (next spec)
- **Status**: Resolved (2026-06-02) — GroundedLLM seam (scouts/grounded_llm.py): GeminiGroundedLLM + ClaudeGroundedLLM chosen by AGENT_BACKEND, injected into LLMScout. All four LLM scouts route through it; AGENT_BACKEND=claude runs the scouts (recommendation + Flow-1 ETL) on the Max subscription. Embeddings stay Gemini. ADR-044.
- **Description**: PR #23 / ADR-041 abstracted only the recommendation MESH (Analyst/Explorer/Critic) to Claude + shared MCP DB tools. The per-discovery `enrich_and_persist_work` still runs the shared Flow-1 `ScoutManager`, whose LLM scouts (StyleScout/LLMTropeScout/DirectKnowledgeScout) do **Gemini-native grounding** (`gemini-2.5-flash` via `GROUNDING_MODEL`) and whose trope/style dedup uses Gemini embeddings. So a Claude-backend run is NOT free of the Gemini `generate_content` free-tier daily cap — under that cap the grounding scouts 429 and contribute nothing (REC-023 keeps Hardcover/REST data; styles/tropes are still lost).
- **URL**: N/A
- **Notes**: Next spec: give StyleScout/LLMTropeScout/DirectKnowledgeScout backend-selectable **Claude variants** (Claude + WebSearch for grounding) so a Claude run's enrichment is off Gemini `generate_content`. Embeddings (`gemini-embedding-001`, separate low-volume quota) may stay on Gemini initially. Mirror the `RecommendationBackend` strategy seam (ADR-041) at the scout layer.

### 2026-06-05 - PRS-025: Review + merge the 4 production-build fix PRs
- **Status**: Completed
- **Description**: Addressed Gemini Code Assist review findings on the open PRs from the production DB build, then squash-merged all four to main (in order #29 → #30 → #31 → #32). All fixes TDD'd (red→green) and verified with the full fast suite (198 passed) on merged main.
- **URL**: https://github.com/jaydee829/agentic_librarian/pulls?q=is%3Apr+29..32
- **Notes**:
    1. **#29** (embed throttle): also fixed lock-held-during-`time.sleep()` — slot now reserved atomically inside `_embed_lock`, sleep outside (Gemini suggestion taken verbatim). `test/unit/test_embed_throttle.py`.
    2. **#30** (null contributor name): also strip/validate `role` — whitespace-only or non-string roles fall back to "Author".
    3. **#31** (gitignore db dumps): merged as-is, no findings.
    4. **#32** (cleaning non-unique index): also fixed the latent `split_authors` misalignment — Author_X frame now built with `index=df.index` (see bugs.md 2026-06-05).
    - Gemini Code Assist consumer code review sunsets 2026-07-17 (new installs blocked 2026-06-18) — PR review automation will need a replacement (e.g. /code-review).

### 2026-06-05 - CLI-026: librarian CLI chat harness (ADR-045, PR #33)
- **Status**: Merged (d82f0cb); live verification pending
- **Description**: `librarian` console-script REPL with multi-turn conversations on BOTH backends — ADK wraps LibrarianConversation (+ event callback); Claude gains a true conversational mode (persistent ClaudeSDKClient session delegating to the analyst/explorer/critic specialist mesh via SDK subagents/Task tool). MLflow conversation capture (`librarian_conversations` experiment) with never-block degradation. Built spec→plan→subagent-driven TDD; 206 offline tests pass.
- **URL**: https://github.com/jaydee829/agentic_librarian/pull/33
- **Notes**:
    - **Live verification (updated 2026-06-05, same day)**: item (1) **resolved positive** — subagents DO see the in-process librarian MCP server; the first live test instead surfaced a permission-layer bug (`AgentDefinition.tools` scopes but doesn't grant — see bugs.md 2026-06-05) fixed in the follow-up PR, with a live re-probe returning the user's real 20-trope profile via `agent: analyst` delegation. Still pending: (2) explorer's `WebSearch` live exercise; (3) the full 2-turn smoke per backend (`test/integration/test_cli_chat_live.py`, api_dependent); (4) MLflow connect speed at CLI startup (escape hatch `--no-mlflow`).
    - Invocation in the app container: `python -m agentic_librarian.cli` always works; the `librarian` script is installed at `/home/appuser/.local/bin/librarian` (not on default exec PATH — add to PATH in Dockerfile/compose as a follow-up, or rebuild the image so the build-time editable install picks up [project.scripts]).
    - Known minor follow-ups from final review: no banner on `--once`; consider MLFLOW_HTTP_REQUEST_TIMEOUT in compose.

### 2026-06-05 - INF-029: Unify /history and /works pagination contract (Lift 2)
- **Status**: Open
- **Description**: `/history` endpoint is unpaginated while `/works` paginates (limit 1-200); unify the API contract during Lift 2 front-end work. Tracked per Lift 0 / ADR-047.
- **URL**: N/A
- **Notes**: Consciously deferred from Lift 0; address alongside the front-end API-contract work in Lift 2.

### 2026-06-05 - IMP-028: Single-title import (spec/single-title-import)
- **Status**: Merged pending live verification
- **Description**: `add_book_to_history` MCP tool (new-row-per-read model: re-reads
  insert read events; same-date duplicate guard; read count derived from row count) +
  conversational IMPORT flow on both Librarians + `librarian add` CLI subcommand.
  date_completed defaults to today. Spec: docs/superpowers/specs/2026-06-05-single-title-import-design.md.
- **URL**: N/A (PR pending)
- **Notes**:
    - **Phase-4 front end (logged per user decision)**: the web UI's add form must
      AUTO-FILL the completion-date field with today (visible + editable) rather than
      hiding the default — the default stays explicit to the user.
    - CSV drift accepted: DB is the history source of truth as of 2026-06-05 (see
      key_facts.md); bulk imports remain CSV/Dagster.

### 2026-06-05 - TUNE-027: Conversation tuning round (spec/conversation-tuning)
- **Status**: In Progress (live verification pending)
- **Description**: Explorer search budget (prompt) + maxTurns=25 guard; verification-by-enrichment (enrich_and_persist_work exposed to BOTH conversational Librarians; null = drop candidate, continue); internal-first routing with novelty triggers; series rule in critic+librarians; analyst on haiku; elapsed-seconds event trace. Spec: docs/superpowers/specs/2026-06-05-conversation-tuning-design.md.
- **URL**: N/A (PR pending)
- **Notes**:
    - **Tracked follow-up (schema, ask-first)**: add `series_name`/`series_position` to `works`, populated from Hardcover `featured_series` + a backfill pass over the existing catalog — makes the series rule deterministic instead of model-knowledge-based.

### 2026-06-09 - INF-030: Move per-turn chat DB writes off the event loop (Lift 2 Stage 4)
- **Status**: Open (deferred from Lift 2 Stage 1, PR #43)
- **Description**: The SSE chat turn (`chat/stream.py` `sse_turn`) issues synchronous INSERTs on the asyncio event loop — both `transcript.append_message` calls (`on_persist`) AND the `usage.record_llm_call` write in `runtime._record_event_usage`. Under concurrent `/chat` load this blocks the loop. Gemini flagged the transcript write HIGH on PR #43; the fix must cover BOTH writes coherently via `asyncio.to_thread(...)`.
- **URL**: PR #43 (review reply discussion_r3383988870); see also the `core/usage.py` latency note.
- **Notes**: Do alongside the Stage 4 DatabaseManager pool consolidation. `asyncio.to_thread` copies the current context, so verify `as_user`/`current_user_id` still resolves inside the worker thread. No live impact until the service deploys against Cloud SQL (Stage 4 opens the IAM gate).

### 2026-06-09 - DOC-031: Consolidated developer setup documentation
- **Status**: Open
- **Description**: Write one dev-setup doc covering the dual-checkout (C:\dev Windows clone vs the WSL clone mounted into the app container), the throwaway-`docker run` test command, Node-on-the-Windows-host for the frontend, `frontend/.env.local` Firebase config, and "pre-commit is the authoritative linter." `frontend/README.md` already covers frontend basics; this is the consolidated guide.
- **URL**: PR #44
- **Notes**: Target Lift 2 wrap-up (alongside the Stage 4 runbook); expand as we go.

### 2026-06-10 - ENR-033: Lift 2 Stage 3 — async two-phase enrichment (PR #45)
- **Status**: Completed (merged `bf986b7`, squash)
- **Description**: Two-phase book enrichment + add-book frontend. Fast pass: `POST /books` (Firebase-gated) runs API-only scouts (Hardcover/Google, ~secs), persists the Work + read-event immediately, then enqueues a Cloud Task. Deep pass: queue-OIDC-gated `POST /internal/enrich/{work_id}` runs the slow LLM scouts (idempotent via shared `persist_enriched_work`). Frontend: AddBookView (`/add` + nav) and the Recommendations "I read this" → prefilled add-book → mark-Read flow.
- **URL**: https://github.com/jaydee829/agentic_librarian/pull/45
- **Notes**:
    - Subagent-driven, 10 tasks TDD + final holistic review (ready-to-merge). Tests: backend 381 passed / 2 skipped (3 `api_dependent` live tests fail without real keys — not regressions); frontend 29 passed + build + lint.
    - New: `orchestration/definitions.py` fast/deep scout factories; `enrichment/two_phase.py`; `enrichment/tasks.py` (`enqueue_enrichment`, `tasks_v2` import contained to `_client()`); `api/books.py` (+ bool-rating reject, user-scoped read-event per ADR-048); `api/internal.py` (fail-closed OIDC gate, **hard-requires `ENRICH_OIDC_AUDIENCE`**); `recommendations.py` allows `Read`. Adds `google-cloud-tasks`.
    - Gemini review: one suggestion (`HttpMethod.POST` enum) declined with rationale — using the enum would pull `tasks_v2` into `enqueue_enrichment` and break the import-containment + hermetic unit test; the `"POST"` string is proto-plus-coerced. Documented at the call site (`63b15db`).
    - Test gotcha hit & fixed: see bugs.md 2026-06-10 (vitest persistent-mock override leak → use `...Once` variants).
    - **Stage 4 backlog (deferred from this stage):** open the Cloud Run IAM gate; provision the live Cloud Tasks queue + invoker SA + grant and set prod `ENRICH_*` env (receiver mandates the audience); wire prod deep-scout API keys; multi-stage Docker to serve the SPA same-origin; security.md + runbook; Playwright live e2e. Already-ticketed: INF-030 (off-loop writes + pool consolidation), INF-029 (`/history` pagination), DOC-031 (dev docs).
    - Follow-ups (non-blocking): `enrich_fast` read-then-insert dedup race (benign, mirrors `enrich_and_persist_work`); pre-existing SAWarning at `persist.py:173` & `:94`.

### 2026-06-09 - DEBT-032: Modernize ruff (version bump + known-first-party)
- **Status**: Resolved (2026-06-09, main `5ce82cc`) — committed straight to main (no PR; direct commit + `[skip ci]`, no deploy, to avoid spinning the deploy pipeline for a lint-only chore).
- **Description**: Two drift axes between the dev image and CI: (1) `.pre-commit-config.yaml` pins ruff **v0.4.4** but `[dev]` installs **ruff>=0.14.14** (version drift); (2) `agentic_librarian` is classified first-party by ruff in the editable-install image (separate import group) but third-party in CI's isolated pre-commit env (one group) — the recurring I001 mismatch.
- **URL**: N/A (direct-to-main, `5ce82cc`)
- **Notes**: Fixed as planned — added `[tool.ruff.lint.isort] known-first-party = ["agentic_librarian"]` so the image ruff and CI's isolated pre-commit env classify the package the same; bumped the pre-commit ruff pin 0.4.4 → 0.15.16; applied the one-time repo-wide reformat. The recurring I001 import-order mismatch is gone. See memory `ruff-firstparty-precommit`. (PR #44 had worked around it by hand-reordering two files under pinned ruff 0.4.4 — no longer needed.)

### 2026-06-10 - TEST-034: Stand up a Playwright e2e harness before open signups (Lift 3)
- **Status**: Open (deferred from Lift 2 Stage 4 by decision)
- **Description**: Spec `2026-06-09-lift2-front-end-design.md` §5 calls for Playwright happy-path e2e ("a couple of end-to-end happy-paths against a running stack"). Stage 4 consciously did NOT build it — Playwright is only *transitively* present (`@vitest/browser-playwright` in `package-lock.json`; no `@playwright/test`, no config, no browsers), and the costly part is automating the **Google-only Firebase sign-in** (real OAuth trips bot-detection/2FA → needs an Admin-SDK custom-token injection harness). Deep enrichment is also slow (~2m30s)/token-spending/non-deterministic, a poor automated-assertion fit.
- **URL**: N/A (Lift 2 Stage 4 brainstorm decision)
- **Notes**: Stage 4 instead shipped (a) a cheap CI guard — an unauthenticated **`GET /` serves the SPA shell** assertion added to `deploy.yml`'s live smoke (catches the same-origin static-serving regression with no browser/Firebase) — and (b) a documented **manual** live-verification checklist in the Stage 4 rollout runbook (sign-in → streamed chat turn → add-book → tropes appear ~2 min later → a metered usage row). **Do this before open signups (Lift 3):** install `@playwright/test` + config + browsers; build a Firebase-token-injection sign-in helper (Admin SDK custom token → app auth state, since there's no email/password path); cover the sign-in gate, a streamed chat turn, and add-book; keep it **operator-run, never CI** (real Firebase/LLM keys), per the live-test discipline rule. Pairs with the Lift 3 security posture re-review (open signup raises the stakes on regressions).

### 2026-06-25 - GH #13: Analysis viz upgrade (style radar + clouds + charts)
- **Status**: PR #74 CI-green, both Gemini review rounds addressed — awaiting merge
- **Description**: Single-scroll Analysis dashboard — snapshot + format **proportion bar**, an 8-axis **style radar**, **trope + style word clouds**, **genre/mood bar charts**. Adds **Recharts** + a `--cat-1..6` categorical CSS palette. Backend adds two **additive** `/analysis` fields (`style_radar`, `style_cloud`) — no schema/migration. design-work did both sides (operator-authorized).
- **URL**: PR #74; deferred → #71 (per-author comparison radar), #72 (`Style.name` canonicalization)
- **Notes**: Key technique = **embedding-projection style binning** (ADR-054). CI bug: an *invalid* `GOOGLE_SEARCH_API_KEY` in CI → unhandled anchor-embed 400 → `/analysis` 500'd all 5 db_integration tests; fixed by degrading to null on any embed failure (bugs.md 2026-06-25). Built brainstorm→spec→plan→subagent-driven (8 TDD tasks, each spec+quality reviewed + final whole-branch review) + 2 Gemini rounds (thread-safety; defensive None-guards). ⚠️ shares `frontend/src/api/client.ts` append with bench PR #73 — second merger resolves a trivial conflict.

### 2026-06-26 - GH #83: Word cloud improvements (packed/rotated/frequency-accentuated)
- **Status**: Merged to `main` via squash (#86); CI green. Frontend-only — no deploy/migration needed (the new cloud renders client-side from the existing `/analysis` payload).
- **Description**: Rebuilt the Analysis word clouds (signature tropes + style) from a `flex-wrap` list into a packed/rotated/frequency-accentuated cloud on `@isoterik/react-word-cloud`'s `useWordCloud` hook (own SVG render → `--cat` palette + Literata + theme via CSS). Cloud-local split `/` + strip articles + merge-sum; width-responsive font max (≈38px mobile / 60px desktop). Frontend-only — no backend/schema.
- **URL**: PR #86 (closes #83)
- **Notes**: ADR-056. Two findings (bugs.md 2026-06-26): `useWordCloud@1.3.0` silently ignores `random` (dead seed → removed); layout stability/no-loop = memoising the hook inputs. Built brainstorm→spec→plan→subagent-driven (5 tasks, each spec+quality reviewed + final whole-branch review, which caught the `random` gotcha). QC at mobile+desktop both themes via the harness (jsdom can't run d3-cloud's canvas). Gemini: applied round-width / `computedWords=[]` / clamp size `norm` (NaN guard) + test; declined an `Array.isArray` guard (typed input, consistent w/ sibling components). The predicted `docs/project_notes/*` append-overlap with Safari-auth PR #85 (which merged first, ADR-055) was resolved by merging `origin/main` into the branch — kept both ADR-055 + ADR-056 (`439a57f`).

### 2026-07-19 - GH #130: Picks neutral removal ("Not right now") + history→picks auto-resolution
- **Status**: Completed (PR pending merge)
- **Description**: New neutral 'Removed' suggestion status (third Picks button) + automatic
  pick-resolution in add_read_event covering add-a-book, chat, and CSV-import paths;
  POST /books reports pick_resolved and the add-book UI mentions the cleared pick.
- **URL**: https://github.com/jaydee829/shelfwright/issues/130
- **Notes**: ADR-062. Spec docs/superpowers/specs/2026-07-19-picks-neutral-removal-design.md.
  Dismissed-row history intentionally not reclassified.

### 2026-07-18 - History format edit (spec 2026-07-18, ADR-061)
- **Status**: MERGED 2026-07-19 (PR #147 squash `1e95517`; ALL CI GREEN incl. 26 db_integration
  tests with both autoflush regressions). NOTE: the squash landed without the branch's final
  project-notes commit — ADR-061 + the 3 bugs.md 2026-07-19 entries were restored on the
  GH #130 branch (this file's sibling docs).
- **Description**: PATCH /history accepts format (vocab-validated); repoints to sibling
  (work_id, format) Edition; 409 on uq_reading_history collisions (incl. the pre-existing
  date-edit 500 hole); async /internal/complete-edition pass fills ISBN/pages/audio +
  narrators/styles for audiobooks — never the paid trope/style deep pass.
- **URL**: https://github.com/jaydee829/shelfwright/pull/147
- **Notes**: merge_edition_and_narrators extracted from persist_enriched_work (shared).
  Built spec→plan→subagent-driven (8 tasks, per-task spec+quality reviews); the final
  whole-branch adversarial pass caught the autoflush 409/500 Critical every per-task
  review missed (bugs.md 2026-07-19 ×3: autoflush reorder, UUID tie-break test flake,
  TS1294 erasableSyntaxOnly). Gemini's one high comment rebutted with schema evidence
  (date_completed is NOT NULL; its "nullable" proof was a MagicMock artifact).
  Leave-as-is residuals: repoint-path N+1 (bounded), no null-format "—" frontend test,
  legacy non-vocab formats display wrong in the edit select (GH #81's bulk-normalization
  scope). Follow-up moment: "details filling in" affordance when narrators surface in UI.

## OPEN — Payment-provider decision (2026-07-31): Ko-fi vs Buy Me a Coffee vs Polar

- **Status**: #155 (metering/tiers/budgets) MERGED + deployed; **#156 (Ko-fi) and #157
  (BYOK) are OPEN and ON HOLD** pending the provider decision.
- **Why reopened**: Ko-fi has no auto-renewing annual subscription (our #156 workaround:
  one-off/shop product ≥ $25 → +370 days, but no auto-renew and no expiry nudges) and its
  webhook sends NO cancellation/lifecycle events (entitlement is inference: +33/+370-day
  grants with grace baked in). Candidates: Buy Me a Coffee (likely $4/mo to cover fees;
  monthly+annual memberships; webhook contract to verify) and Polar (merchant of record —
  handles VAT/sales tax; real subscription lifecycle webhooks incl. cancellations, which
  would eliminate the inference design; ~4%+40¢; heavier integration).
- **Swap cost is bounded by design** (ADR-064): #156's provider-specific surface is the
  webhook adapter (`api/kofi.py`) + env config + 3 link URLs in AccountMenu; the payments
  table, `core/entitlements.py`, CLI, `/api/account`, and the dropdown are neutral.
  #157 (BYOK) is provider-independent (needs only #156's AccountMenu + account endpoint).
- **Next**: researched comparison (fees, webhook contracts, annual, payouts, MoR/tax) →
  decide provider or hybrid → salvage plan for #156 (keep neutral core, swap adapter) and
  rebase/adapt #157.
