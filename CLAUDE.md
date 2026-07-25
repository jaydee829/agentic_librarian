# CLAUDE.md — Shelfwright (agentic_librarian)

Institutional memory lives in `docs/project_notes/` (bugs, decisions/ADRs, key_facts,
issues) — read AGENTS.md for the memory protocols. Testing fundamentals (TDD, pyramid,
anti-hardcoding, dual-verification) live in `docs/testing_strategy.md`. This file adds the
verification mentality proven during the Phase 6 hardening (2026-07): it caught a verified
data-loss bug, a cold-start auth race, a review-gate bypass, and an operation-flip hole
that every single-pass review missed.

## Verification mentality (additions to testing_strategy.md)

1. **Assertion completeness beats test level.** The #96 contributor-loss bug lived for
   weeks inside a test that executed the exact buggy path but asserted only trope counts.
   When you write or extend a test, assert on EVERY side effect the operation promises —
   not just the one the task is about. A test that runs the right code and checks the
   wrong things is worse than no test: it certifies the bug.
2. **Warnings are errors.** `filterwarnings = ["error::sqlalchemy.exc.SAWarning"]` is
   suite policy. Never downgrade it; when a warning appears, it has already found a bug
   twice (silent non-flush #96; backref-append-before-add in new code). Extend the policy
   to new warning classes when a framework starts telling you something.
3. **Test invariants directly.** The house pattern for "no session may be open during X":
   a counting fixture asserting `open_sessions_during_scout == 0` (see
   `test_two_phase_sessions.py`, `test_embed_warming.py`). When a design rule matters,
   write the probe that measures the rule itself, not a proxy.
4. **Dialect-specific SQL: compile-inspect locally, execute in CI.** App models are
   Postgres-only (pgvector, JSONB) — never run them on sqlite. pg-dialect statements
   (ON CONFLICT, advisory locks) get local unit tests that compile with
   `postgresql.dialect()` and assert the SQL, plus `db_integration` tests that execute
   for real. Guard pg-only runtime calls behind `session.get_bind().dialect.name`.
5. **`db_integration` tests execute FIRST in CI — treat the PR's first CI run as a merge
   gate, not a formality.** Local runs deselect them (no Postgres) unless the compose db
   is up (`POSTGRES_HOST=localhost` override works). If you re-seam a mocked boundary,
   grep the integration suites for patches of the OLD seam — they break in CI first.
6. **Operator tools that mutate prod data get e2e-shaped workflow tests.** The
   dedup gate's same-second report-overwrite bug was caught by a test that drove the real
   dry-run→apply CLI round-trip. For anything with a review→apply workflow: plan and
   apply must be tested as the operator will actually run them, and apply must consume
   (and cross-check against) exactly what was reviewed — refuse on ANY drift, including
   operation changes on the same row (tag tokens with their operation).
7. **Destructive data operations use structural distinguishers only** (the #69 lesson,
   memory `verify-backfill-distinguisher`): never classify rows by a sometimes-populated
   column. Plan → print/persist the full id list → apply exactly the plan. Dry-run
   reports are the artifact the human approves; the gate is only real if apply refuses
   when reality drifted from the report.
8. **Adversarial review passes pay for themselves on concurrency and time-window code.**
   Tests structurally cannot see: races (cold-start init, thread-pool contention), what
   an event loop's accidental serialization used to protect, drift between two CLI
   invocations, retry×timeout arithmetic vs platform deadlines. After the per-task and
   whole-branch reviews, run one more pass with an explicit "assume something was missed;
   hunt runtime failure modes tests can't see" charter — in Phase 6 it found real bugs
   twice after both human-configured reviewers and Gemini came back clean.
9. **Verify claimed baselines with `git stash`.** "These test failures are pre-existing"
   is a claim: prove it by stashing the diff and re-running. The suite has a small set of
   known env-dependent failures (live-network, `db` hostname, optional `claude_agent_sdk`)
   — name them explicitly in reports instead of hand-waving "some failures".
10. **Report honestly.** State what was executed vs collected vs reasoned-by-inspection.
    "CI is the gate for X" is a fine sentence; claiming an unrun test passed is not.
    Comments and ADRs must not overclaim invariants the code doesn't have — name the
    residuals (this bit us three times on one pool-sizing premise before we learned).
11. **Rehearse operator tooling against the PRE-state schema, not just the post-state.**
    A migration-gating tool (dedup, backfill, pre-flight check) runs by design against
    the schema that exists BEFORE the migration it clears the way for — but the branch's
    ORM models already describe the AFTER. Every test environment that runs the full
    migration chain first will pass while prod fails with UndefinedColumn on the first
    entity load (found live, phase 6.3: the dedup gate selected `deep_enriched_at` from
    a prod that didn't have it yet). Rules: gate-path queries are column-explicit for any
    model the same branch alters (never entity-load it — state the invariant in a module
    comment), and the tool gets a fixture test that DROPS the new columns/constraints to
    mirror the true pre-migration schema.

## Mechanics

- Tests: `.venv/Scripts/python -m pytest ...` from the repo root (Windows host venv).
- Lint AND format before every commit: `uvx ruff check <files>` **and**
  `uvx ruff format <files>` — CI pre-commit enforces format; check alone is not enough.
- Full unit suite before each commit; focused tests while iterating.
- No `[skip ci]` anywhere in commit messages (see bugs.md 2026-06-17 / GH #90).

## Local test suite is green by default — no stash-verify dance (ADR-063)

`pytest`'s `addopts` now carries CI's exact filter — `-m "not api_dependent and not slow and
not live"` — so a bare local `.venv/Scripts/python -m pytest test/unit` runs the same
selection CI gates on and comes back **green** (live/key-dependent tests deselected; the two
`claude_agent_sdk` tests `importorskip`-skip when the optional `claude` extra isn't installed).
There is nothing pre-existing to filter out by eye, so **any failure is a real failure** —
investigate it, don't `git stash` to "confirm it's baseline" (that dance is retired). This
supersedes the earlier reading of guidance #9 for the unit suite.

The excluded tests still run in CI (deploy.yml / lint.yml pass the same `-m` on the CLI, which
overrides `addopts`, and install `.[dev,claude]`). To run them yourself: override the marker
filter, e.g. `-m api_dependent` (needs real Gemini/Hardcover keys, and an authenticated
`claude` CLI for `test_claude_grounded_scouts_produce_styles_and_tropes`), and
`pip install -e '.[claude]'` for the `claude_agent_sdk` tests.
