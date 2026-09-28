# SIH26122 / SETUAI --- V7 BUILD PLAN

## Two-Member Execution Plan

### Ownership

-   **Member 1:** Backend / Database / Algorithms / AI / Security
-   **Member 2:** Frontend / UX / API Integration / E2E QA

------------------------------------------------------------------------

# 1. Existing vs Modify vs Build

## KEEP

-   extraction pipeline
-   shared LLM client
-   multilingual support
-   clarification
-   four-tier matching
-   WBS bridge
-   deterministic validation
-   conflict detection
-   evidence fusion
-   review priority
-   human approval
-   approved actuals
-   CSV export
-   P6 mock adapter
-   audit chain
-   forecasting
-   knowledge graph
-   Ask Why
-   execution summary
-   current design system

## MODIFY

-   project/schedule context
-   schedules
-   activities
-   dependencies
-   execution events
-   approved actuals
-   audit logs
-   matching eligibility
-   WBS allocation
-   actuals reopen semantics
-   dashboards
-   Review Workspace
-   AppShell
-   RBAC
-   RLS
-   vector retrieval
-   Impact Preview

## BUILD

-   Projects
-   Schedule Versions
-   Stages
-   Memberships
-   Contractors
-   Work Packages
-   Quality Gates
-   Quality Evidence
-   Institutional Memory
-   Contractor Disputes
-   Compound Impact Scenarios
-   Supervising Agent
-   Agent Briefings
-   Audit Dossier
-   Project Switcher
-   Project Dashboard
-   Quality UI
-   Contractor UI
-   Memory UI

## REMOVE/DEPRECATE

-   latest-schedule fallback
-   unscoped global matching
-   legacy duplicate WBS split storage if unused
-   frontend-only authorization assumptions

------------------------------------------------------------------------

# 2. Phase Schedule

## Phase 0 --- Freeze and Safety

**Goal:** Protect the current working version.

Member 1: - tag v6; - freeze DB; - capture schema; - baseline API; -
baseline tests.

Member 2: - deploy frozen V6; - capture screenshots; - baseline
frontend; - verify all routes.

Deliverable: - rollback-ready V6.

------------------------------------------------------------------------

## Phase 1 --- Project Foundation

Member 1: - projects; - memberships; - schedule FK; - RLS; -
project-scoped repositories; - auth checks.

Member 2: - project context provider; - project switcher; -
project-aware API client; - shell changes.

Acceptance: - Project A/B isolation works.

------------------------------------------------------------------------

## Phase 2 --- State-Aware Matching

Member 1: - eligibility engine; - stage status; - completed activity
filter; - completed stage filter; - reopen workflow; - actual-finish bug
fix.

Member 2: - state badges; - stage tree; - reopen UI; - candidate
explanation.

Acceptance: - completed activities never enter normal matching.

------------------------------------------------------------------------

## Phase 3 --- Progress/EVM

Member 1: - weights; - stage rollup; - project rollup; - milestone
gates.

Member 2: - dashboard; - progress charts; - milestone timeline.

Acceptance: - deterministic weighted project progress.

------------------------------------------------------------------------

## Phase 4 --- XER / Contractors

Member 1: - XER baseline import; - contractor schema; - work packages; -
claim attribution; - contractor analytics.

Member 2: - XER upload; - contractor screens; - work package screens; -
filters.

Acceptance: - XER directly creates schedule version.

------------------------------------------------------------------------

## Phase 5 --- Quality

Member 1: - quality tables; - gate evaluator; - validation rules; -
evidence.

Member 2: - quality dashboard; - evidence upload; - review integration.

Acceptance: - quality-gated completion is blocked until clearance.

------------------------------------------------------------------------

## Phase 6 --- Compound Impact

Member 1: - multi-source propagation; - scenario persistence; -
stage/project impact.

Member 2: - multi-select simulator; - delay inputs; - result
visualization.

Acceptance: - deterministic multi-delay simulation.

------------------------------------------------------------------------

## Phase 7 --- Memory

Member 1: - incidents; - taxonomy; - embeddings; - retrieval.

Member 2: - memory UI; - related incidents; - creation workflow.

Acceptance: - relevant historical context is retrievable and grounded.

------------------------------------------------------------------------

## Phase 8 --- Supervising Agent

Member 1: - tools; - orchestrator; - trigger system; - structured
output; - restrictions.

Member 2: - briefing UI; - evidence panels; - recommendation display.

Acceptance: - agent has no mutation authority.

------------------------------------------------------------------------

## Phase 9 --- Dossier

Member 1: - dossier generator; - audit collection; - hash verification.

Member 2: - dossier UI; - preview; - auditor workspace.

Acceptance: - end-to-end traceable dossier.

------------------------------------------------------------------------

## Phase 10 --- Hardening

Member 1: - backend integration tests; - security; - RLS; - algorithm
regression; - deployment.

Member 2: - E2E tests; - UI regression; - responsive testing; -
localization; - production smoke testing.

Joint: - 5--8 minute demo; - final regression; - deployment freeze.

------------------------------------------------------------------------

# 3. Critical Dependency Order

``` text
Project Context
      ↓
Schedule Version
      ↓
Stage Model
      ↓
State-aware Matching
      ↓
Progress / Stage Completion
      ↓
Contractor / Quality
      ↓
Compound Impact
      ↓
Memory
      ↓
Agent
      ↓
Dossier
```

Do not reverse this order.

------------------------------------------------------------------------

# 4. Parallelization Rule

Member 2 may build UI shells before backend completion using typed mock
contracts.

Member 1 must publish API contracts before UI integration.

Every API change must update:

1.  schema;
2.  backend model;
3.  endpoint;
4.  API type/interface;
5.  test;
6.  frontend integration.

------------------------------------------------------------------------

# 5. Migration Strategy

Never make a destructive migration in the first V7 migration.

Recommended:

``` text
001_v7_projects
002_v7_schedule_version
003_v7_stages
004_v7_memberships_rls
005_v7_activity_state
006_v7_contractors
007_v7_work_packages
008_v7_quality
009_v7_progress_weights
010_v7_impact_scenarios
011_v7_memory
012_v7_agent
013_v7_dossier
```

Backfill project_id only after project mapping is verified.

------------------------------------------------------------------------

# 6. API Contract Rule

Every operational API must answer:

``` text
Which project?
Which schedule version?
Which authenticated user?
Which role?
```

If a route cannot answer those questions, it needs redesign.

------------------------------------------------------------------------

# 7. Test Gates

No phase is considered complete until:

-   unit tests pass;
-   project isolation tests pass;
-   regression tests pass;
-   API contract tests pass;
-   UI integration passes where applicable.

------------------------------------------------------------------------

# 8. Final Team Deliverables

### Member 1

-   migration set;
-   backend domain services;
-   matching changes;
-   actuals fixes;
-   progress engine;
-   XER importer;
-   contractor/quality engines;
-   impact simulator;
-   memory;
-   agent;
-   dossier;
-   security;
-   tests.

### Member 2

-   AppShell/project context;
-   project dashboard;
-   WBS explorer;
-   review changes;
-   quality UI;
-   contractor UI;
-   impact simulator;
-   memory UI;
-   agent briefing;
-   dossier UI;
-   localization;
-   E2E tests;
-   deployment verification.

### Joint

-   benchmark dataset;
-   V7 demo scenario;
-   final QA;
-   Vercel/Render production configuration;
-   new Supabase project;
-   new Groq/Gemini credentials;
-   release documentation.

------------------------------------------------------------------------

# 9. Release Checklist

-   [ ] V6 frozen.
-   [ ] V7 isolated environment.
-   [ ] Project isolation verified.
-   [ ] RLS verified.
-   [ ] XER import verified.
-   [ ] Completed activity filtering verified.
-   [ ] Reopen verified.
-   [ ] Stage completion verified.
-   [ ] Weighted progress verified.
-   [ ] Contractor attribution verified.
-   [ ] Quality gates verified.
-   [ ] Compound impact verified.
-   [ ] Memory retrieval verified.
-   [ ] Agent restrictions verified.
-   [ ] Audit chain verified.
-   [ ] Dossier generated.
-   [ ] CSV/P6 mock verified.
-   [ ] All old V6 tests pass unless a documented V7 behavior
    intentionally changes an assertion.
-   [ ] New V7 tests pass.
-   [ ] Frontend production build passes.
-   [ ] Backend production smoke test passes.
-   [ ] No old production credentials are used by V7.
-   [ ] No secrets are committed.

------------------------------------------------------------------------

# 10. V7 Success Criterion

The team should be able to demonstrate:

> "This is one project among many. This project has a controlled
> schedule version, stages, activities, contractors and quality gates. A
> field report enters as a claim, is matched only against eligible
> unfinished work, is deterministically checked, is reviewed by a human,
> becomes an authoritative actual only after approval, changes
> stage/project progress, can be tested through impact simulation,
> enriches institutional memory, can trigger a grounded supervisory
> briefing, and remains fully auditable."

That is the V7 product.
