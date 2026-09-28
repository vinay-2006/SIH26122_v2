# SIH26122 / SETUAI --- PRD v7

## Project Execution Intelligence, Verification & Governance Platform

**Problem Statement:** SIH26122 --- Oil India Limited --- Smart
Automation\
**Version:** 7.0\
**Status:** Master Product Requirements Document\
**Release intent:** Major architectural/product evolution from v6 into a
project-centric, multi-project, state-aware, enterprise-grade execution
intelligence platform.

------------------------------------------------------------------------

# 0. V7 Executive Definition

SETUAI v7 is a **project-centric, human-governed execution intelligence
platform** that converts heterogeneous field evidence into verified,
explainable, auditable project actuals and project-level intelligence.

The platform sits between:

**Project Baseline → Schedule/WBS → Field Reality → AI Extraction →
State-Aware Matching → Deterministic Verification → Human Governance →
Approved Actuals → Progress/Impact/Quality Intelligence →
Audit/Institutional Memory**

The governing principle remains:

> **AI reads the mess. Rules check it. A human approves it. Everything
> is logged.**

V7 does **not** replace Primavera P6, MS Project, project controls
teams, QC teams, or human approval. It provides the intelligence,
reconciliation, governance, evidence, and workflow layer around them.

### V7's central transformation

V6 is primarily an **activity/claim validation system**.

V7 becomes a **project execution operating layer**.

The domain model becomes:

``` text
PROJECT
  └── SCHEDULE VERSION
        └── STAGE / MILESTONE
              └── WBS
                    └── DISCIPLINE
                          └── ACTIVITY
                                ├── BASELINE
                                ├── DEPENDENCIES
                                ├── WEIGHT
                                ├── QUALITY GATES
                                ├── CONTRACTOR / WORK PACKAGE
                                └── EXECUTION STATE
                                      └── FIELD CLAIMS
                                            └── EVIDENCE
                                                  └── MATCHING
                                                        └── VALIDATION
                                                              └── HUMAN DECISION
                                                                    └── APPROVED ACTUAL
```

V7 must support multiple projects simultaneously while ensuring strict
project isolation.

------------------------------------------------------------------------

# 1. Product Goals

## 1.1 Primary goals

1.  Convert field evidence into structured execution claims.
2.  Keep project/schedule context explicit at every stage.
3.  Prevent cross-project contamination.
4.  Prevent completed work from being repeatedly matched.
5.  Represent stage and project completion from authoritative activity
    state.
6.  Provide physically meaningful project progress using explicit
    weighting.
7.  Support native Primavera P6 `.xer` baseline ingestion.
8.  Attribute execution to contractors and work packages.
9.  Introduce quality gates / ITP / hold-point verification.
10. Preserve and strengthen deterministic validation.
11. Expand Schedule Impact Preview from single-activity analysis to
    compound delay simulation.
12. Create structured institutional memory.
13. Add a human-governed Supervising Agent.
14. Provide project-centric dashboards and WBS exploration.
15. Provide enterprise audit/compliance dossier generation.
16. Preserve all v6 capabilities unless explicitly superseded.
17. Maintain deterministic fallback behavior when external AI services
    fail.
18. Keep PostgreSQL/Supabase authoritative and FAISS/retrieval
    structures disposable.

## 1.2 Non-goals

V7 will NOT:

-   replace Primavera P6;
-   build a complete resource-leveling engine;
-   build a complete enterprise cost-accounting system;
-   autonomously approve or reject field claims;
-   autonomously modify approved actuals;
-   autonomously write to live P6;
-   replace deterministic validation with an LLM;
-   create one database per project;
-   introduce Kafka/Kubernetes/microservices merely for architecture
    aesthetics;
-   require paid AI APIs for the core demonstration path;
-   create unrestricted full-project CPM optimization;
-   claim legal/statutory compliance merely because a PDF dossier
    exists.

------------------------------------------------------------------------

# 2. V7 Product Principles

1.  A field report is a **claim**, never automatically a fact.
2.  AI assists extraction, clarification, summarization and
    investigation.
3.  Deterministic rules remain authoritative for physical/logical
    validation.
4.  Human approval remains mandatory before authoritative actuals
    change.
5.  Project context is mandatory; there is no implicit "latest
    schedule".
6.  Schedule version is distinct from project.
7.  Historical schedules remain immutable records.
8.  Completed activities are excluded from normal candidate generation.
9.  Completed stages are excluded from normal candidate generation.
10. Reopening completed work requires an explicit exception/rework path.
11. Stage status is derived from authoritative activity state and stage
    gates.
12. Project status is derived from stage state.
13. Approved cumulative percentage is replaced, not blindly accumulated.
14. Incremental quantity is recomputed from approved source decisions.
15. Start and finish updates are independently reconciled.
16. A WBS split and normal single-activity match are mutually exclusive.
17. Completed siblings receive zero new WBS allocation.
18. Quality gates can prevent completion when required evidence is
    absent.
19. Contractor attribution must be auditable.
20. Every approved value must remain traceable to its source evidence
    and decision.
21. Audit logs remain tamper-evident.
22. PostgreSQL/Supabase is the source of truth.
23. FAISS/vector indexes are retrieval accelerators only.
24. LLMs never invent schedule facts.
25. Translation never overwrites canonical source data.
26. External adapters cannot roll back committed approvals.
27. Intelligence must be explainable.
28. Every agent recommendation must expose evidence and rationale.
29. The Supervising Agent is advisory and human-governed.
30. V7 must retain a deterministic demo path.
31. Existing sound v6 algorithms are preserved unless the V7 context
    requires scoped modification.
32. V7 complexity must be justified by operational value.

------------------------------------------------------------------------

# 3. V6 Baseline --- What Already Exists

The following capabilities are considered the **existing baseline** and
are not to be rewritten unnecessarily.

## 3.1 Intake

Existing:

-   PDF intake
-   XLSX intake
-   CSV intake
-   TXT intake
-   typed text
-   browser Web Speech voice-to-text
-   scanned diary/image intake
-   evidence photos
-   audio-upload UI where present
-   P6/MSP progress export intake
-   batch/multi-claim extraction support
-   source document metadata
-   SHA-256 file hashes

## 3.2 Extraction

Existing:

-   shared LLM client
-   Groq primary
-   Gemini fallback
-   deterministic fallback path
-   Pydantic structured extraction
-   language detection
-   field-level provenance
-   adaptive one-question clarification
-   English/Hindi/Telugu support
-   runtime translation

## 3.3 Matching

Existing:

-   EXACT_ID
-   EXACT_ASSET
-   HYBRID_FALLBACK
-   HARD_MISMATCH
-   semantic similarity
-   RapidFuzz
-   contextual location/discipline signals
-   top-three candidates
-   unmatched handling
-   WBS granularity bridge
-   split persistence

## 3.4 Validation

Existing:

-   physical validation
-   percentage bounds
-   quantity bounds
-   UOM checks
-   dependency checks
-   sequence checks
-   evidence metadata checks
-   same-date disagreement
-   progress regression
-   incremental quantity anomaly detection
-   evidence fusion
-   corroboration/contradiction
-   explainable review priority

## 3.5 Human governance

Existing:

-   Supervisor review
-   APPROVE
-   EDIT
-   REJECT
-   HOLD
-   mandatory decision justification
-   provenance display
-   evidence display
-   candidate visibility
-   WBS split editing
-   priority queue
-   Daily Digest

## 3.6 Actuals

Existing:

-   one authoritative row per `(schedule_id, activity_id)`
-   start/finish reconciliation
-   cumulative percentage replacement
-   source-decision quantity resummation
-   CSV export
-   P6-shaped adapter
-   local P6 mock
-   adapter failure isolation

## 3.7 Intelligence

Existing:

-   delay dashboard
-   institutional-memory baseline/variance view
-   activity history
-   silent activity alerts
-   lightweight forecasting
-   Schedule Impact Preview
-   execution knowledge graph
-   Ask Why
-   AI Execution Summary
-   SHA-256 audit chain

## 3.8 Frontend

Existing:

-   Login
-   Claim Intake
-   Daily Digest
-   Review Workspace
-   Dashboard
-   Activity History
-   Impact Preview
-   WBS Explorer
-   AI Execution Summary
-   industrial dark/light theme
-   English/Hindi/Telugu UI

------------------------------------------------------------------------

# 4. V7 Critical Defects To Fix Before Feature Expansion

These are not optional enhancements. They are architectural defects
identified by the deep audit.

## 4.1 Eliminate implicit latest-schedule resolution

Current unsafe behavior:

``` text
resolve_schedule_id()
    -> latest created schedule
```

V7 requirement:

``` text
Authenticated user
    -> active project
        -> active schedule version
            -> scoped operation
```

Every claim, matching operation, validation query, impact query,
dashboard query and schedule operation must carry or derive an
authenticated project context.

There must be no fallback to "most recently created schedule".

## 4.2 Fix reopened activity actual-finish corruption

If:

``` text
activity = COMPLETED
actual_pct = 100
actual_finish = date
```

and an approved rework/reopen event moves the activity back below
completion, the authoritative state must not retain an invalid finish
date.

V7 must define explicit reopen semantics:

``` text
COMPLETED
   ↓
REOPEN_REQUESTED
   ↓
SUPERVISOR_APPROVAL
   ↓
IN_PROGRESS
```

The finish date is cleared or replaced according to the approved rework
event semantics and must be auditable.

## 4.3 Remove completed activities from normal matching

Completed activities must not be loaded into the ordinary candidate
search space.

Candidate eligibility must be evaluated before expensive semantic/fuzzy
scoring.

## 4.4 Remove completed stages from normal matching

If every eligible activity in a stage is completed and required stage
gates are satisfied:

``` text
stage.status = COMPLETED
```

Normal matching must not search that stage.

## 4.5 Preserve an exception path

Completed work is not deleted.

If a new report claims:

-   rework,
-   damage,
-   replacement,
-   reversal,
-   correction,
-   reopen,

the system must create an explicit exception/reopen workflow instead of
silently treating the completed activity as an ordinary candidate.

------------------------------------------------------------------------

# 5. V7 Domain Model

## 5.1 Core hierarchy

``` text
Project
  └── Schedule Version
       └── Stage
            └── WBS
                 └── Activity
                      ├── Contractor
                      ├── Work Package
                      ├── Quality Gates
                      ├── Dependencies
                      └── Approved Actual
```

## 5.2 Project

A project is the long-lived business container.

Fields:

-   project_id
-   project_code
-   project_name
-   client_name
-   project_type
-   location
-   latitude
-   longitude
-   geofence_radius
-   planned_start
-   planned_finish
-   contract_finish
-   status
-   current_stage_id
-   current_schedule_version_id
-   created_at
-   updated_at

Project status examples:

``` text
PLANNED
ACTIVE
ON_HOLD
COMPLETED
CLOSED
```

## 5.3 Schedule Version

A project may contain multiple schedule versions.

Fields:

-   schedule_id
-   project_id
-   version_code
-   version_name
-   source_type
-   source_file_hash
-   imported_at
-   baseline_start
-   baseline_finish
-   is_active
-   supersedes_schedule_id
-   import_metadata

Rules:

-   only one active baseline/version per project unless explicitly
    supported;
-   old versions remain queryable;
-   historical claims remain attached to their original schedule;
-   no automatic historical rematching after replacement.

## 5.4 Stage

A stage is a meaningful project milestone/work package grouping.

Fields:

-   stage_id
-   project_id
-   schedule_id
-   stage_name
-   sequence_order
-   weight_pct
-   status
-   planned_start
-   planned_finish
-   contract_milestone_date
-   gating_predecessor_stage_id
-   completion_rule
-   created_at

## 5.5 Activity

Existing activity fields remain, with V7 additions:

-   project_id
-   schedule_id
-   stage_id
-   activity_id
-   wbs_code
-   activity_name
-   discipline
-   uom
-   planned_quantity
-   planned_start
-   planned_finish
-   total_float
-   is_critical
-   weight_factor
-   weight_basis
-   execution_state
-   quality_gate_required
-   contractor/work-package association

## 5.6 Execution state

Canonical:

``` text
NOT_STARTED
IN_PROGRESS
COMPLETED
```

Additional workflow state may exist separately:

``` text
REOPEN_REQUESTED
REWORK_IN_PROGRESS
QUALITY_HOLD
BLOCKED
```

Do not overload the canonical execution state with workflow conditions.

------------------------------------------------------------------------

# 6. V7 Feature Catalog

# DOMAIN A --- PROJECT FOUNDATION

## Feature A1 --- Multi-Project Management

Users with appropriate permissions can:

-   create projects;
-   view assigned projects;
-   archive projects;
-   activate/deactivate projects;
-   view project metadata;
-   switch active project.

Every project is isolated.

Acceptance:

-   Project A data never appears in Project B queries.
-   Project A claims never match Project B activities.
-   Project A audit records cannot be queried by unauthorized Project B
    users.

## Feature A2 --- Schedule Version Management

Support:

-   initial baseline;
-   revised schedule;
-   active schedule;
-   historical schedule;
-   schedule replacement;
-   version comparison metadata.

The product stores schedule history but does not automatically rematch
historical claims.

## Feature A3 --- Project Context

The frontend must always expose:

``` text
Current Project
Current Schedule Version
Current Stage where applicable
```

All pages become project-scoped.

## Feature A4 --- Project Membership

Membership fields:

-   user_id
-   project_id
-   role
-   contractor_id nullable
-   created_at
-   active

V7 roles:

``` text
SITE_ENGINEER
SUPERVISOR
PROJECT_MANAGER
AUDITOR
```

The original two roles remain fully supported.

------------------------------------------------------------------------

# DOMAIN B --- SCHEDULE INGESTION

## Feature B1 --- Native P6 XER Baseline Import

The existing native XER parser must be promoted from claim-intake
utility to baseline schedule importer.

Support:

-   `.xer` upload;
-   project association;
-   schedule version creation;
-   activities;
-   WBS;
-   dependencies;
-   lag/lead;
-   baseline dates;
-   float where available;
-   criticality where available.

Import must be transactional.

## Feature B2 --- CSV/XLSX Schedule Import

Preserve existing import.

Add:

-   explicit project_id;
-   schedule version;
-   validation report;
-   duplicate protection;
-   import preview.

## Feature B3 --- Schedule Import Validation

Reject:

-   duplicate activity IDs within version;
-   empty IDs;
-   invalid dates;
-   negative quantities;
-   invalid percentages;
-   invalid dependency references;
-   self-dependencies;
-   malformed relationships.

Do not fabricate missing schedule facts.

------------------------------------------------------------------------

# DOMAIN C --- STATE-AWARE EXECUTION

## Feature C1 --- Canonical Activity State

Derive:

``` text
actual_pct_complete >= 100 -> COMPLETED
else actual_start != NULL -> IN_PROGRESS
else -> NOT_STARTED
```

This remains the canonical state model.

## Feature C2 --- Eligibility Engine

Before matching, calculate:

``` text
PROJECT ELIGIBILITY
STAGE ELIGIBILITY
ACTIVITY ELIGIBILITY
QUALITY ELIGIBILITY
TEMPORAL ELIGIBILITY
DEPENDENCY ELIGIBILITY
```

A candidate is eligible only if it passes the applicable gates.

## Feature C3 --- Completed Activity Exclusion

Completed activities:

-   remain in database;
-   remain in history;
-   remain in audit;
-   remain in impact analysis;
-   remain visible in Activity History;
-   are excluded from normal matching.

## Feature C4 --- Completed Stage Exclusion

A completed stage is excluded from normal matching.

## Feature C5 --- Reopen / Rework Workflow

If a claim conflicts with completed state:

``` text
NORMAL MATCH
    ↓
COMPLETED ACTIVITY DETECTED
    ↓
CLASSIFY
    ├── normal duplicate -> block
    └── rework/reopen -> exception workflow
```

Supervisor must explicitly approve reopening.

------------------------------------------------------------------------

# DOMAIN D --- MATCHING V7

## Feature D1 --- Preserve Four-Tier Cascade

``` text
1. EXACT_ID
2. EXACT_ASSET
3. HYBRID_FALLBACK
4. HARD_MISMATCH
```

Existing semantic/fuzzy/context weights remain tunable and
benchmark-calibrated.

## Feature D2 --- Pre-Ranking Eligibility Filter

Eligibility happens before FAISS/RapidFuzz ranking.

Pseudo-flow:

``` text
project scope
→ active schedule
→ non-completed stage
→ non-completed activity
→ quality/state eligibility
→ temporal eligibility
→ dependency eligibility
→ semantic/fuzzy scoring
```

## Feature D3 --- Project-Partitioned Retrieval

FAISS indexes must be isolated by project/schedule version.

No global cross-project index.

## Feature D4 --- State-Aware WBS Splitting

Preserve V6 WBS bridge, but:

-   completed siblings = 0 allocation;
-   completed stages excluded;
-   future/ineligible siblings excluded;
-   active siblings receive headroom-aware allocation;
-   dependency order respected;
-   UOM compatibility required;
-   shares sum to `1.0000 ± 0.0001`.

## Feature D5 --- Candidate Explanation

Every candidate exposes:

-   confidence;
-   semantic contribution;
-   fuzzy contribution;
-   location contribution;
-   discipline contribution;
-   eligibility gates passed;
-   disqualifying signals.

------------------------------------------------------------------------

# DOMAIN E --- PROGRESS & EARNED VALUE

## Feature E1 --- Activity Weighting

Each activity may have:

``` text
weight_factor
weight_basis
```

Supported basis:

-   planned value/cost where available;
-   planned quantity/duration where appropriate;
-   explicit planner-provided weight.

Do not silently invent economic value.

## Feature E2 --- Multi-tier Progress Rollup

``` text
Activity
   ↓ weighted
Stage
   ↓ weighted
Project
```

A generic weighted progress model:

``` text
weighted_progress =
Σ(activity_progress × activity_weight)
/
Σ(activity_weight)
```

Weights must be explicitly defined and normalized.

## Feature E3 --- Stage Progress

Display:

-   physical progress;
-   planned progress where available;
-   actual progress;
-   variance;
-   stage status;
-   completed activity count;
-   remaining activity count;
-   quality holds;
-   blocked activities.

## Feature E4 --- Project Progress

Display:

-   project physical progress;
-   schedule progress;
-   variance;
-   active stage;
-   completed stages;
-   critical path exposure;
-   quality holds;
-   contractor progress.

## Feature E5 --- EVM Boundary

V7 supports project progress weighting.

It does not become a full P6 cost/EVM accounting replacement.

Where cost/budget data is unavailable, the system must clearly label the
selected progress basis.

------------------------------------------------------------------------

# DOMAIN F --- STAGE COMPLETION & MILESTONES

## Feature F1 --- Stage Completion Engine

Stage becomes COMPLETED only when:

1.  all required activities are complete;
2.  required quality gates are cleared;
3.  required predecessor stages are satisfied;
4.  no unresolved blocking condition exists.

## Feature F2 --- Stage Gating

Example:

``` text
Stage B cannot complete
until Stage A is complete.
```

Gating must be configurable.

## Feature F3 --- Milestone Visibility

Expose:

-   planned milestone;
-   actual completion;
-   variance;
-   blocking activities;
-   controlling dependencies;
-   quality status.

------------------------------------------------------------------------

# DOMAIN G --- CONTRACTOR & WORK PACKAGE

## Feature G1 --- Contractor Directory

Fields:

-   contractor_id
-   company_name
-   contract_reference
-   contact_email
-   active_status
-   created_at

## Feature G2 --- Work Packages

Fields:

-   work_package_id
-   project_id
-   package_code
-   package_name
-   contractor_id
-   discipline
-   stage_id
-   planned_start
-   planned_finish
-   status

## Feature G3 --- Claim Attribution

Execution events may record:

-   contractor_id;
-   work_package_id;
-   source of attribution;
-   attribution confidence.

## Feature G4 --- Contractor Performance

Dashboard metrics:

-   claims submitted;
-   approved claims;
-   rejected claims;
-   conflict rate;
-   validation rate;
-   quality hold count;
-   average review time;
-   delay association;
-   discrepancy rate.

Metrics are descriptive, not automated contractual judgments.

------------------------------------------------------------------------

# DOMAIN H --- QUALITY / ITP / HOLD POINTS

## Feature H1 --- Quality Gate Definitions

A quality gate may be attached to:

-   activity;
-   stage;
-   work package.

Types:

``` text
INSPECTION
TEST
POUR_CARD
WELD_INSPECTION
NDT
MATERIAL_CERTIFICATE
NCR_CLEARANCE
CLIENT_APPROVAL
```

## Feature H2 --- Quality Gate State

``` text
NOT_REQUIRED
PENDING
SUBMITTED
PASSED
FAILED
WAIVED
```

## Feature H3 --- Quality Hold Rule

If an activity requires a quality gate:

``` text
claim = 100%
+
quality gate != PASSED
=
VAL_QUALITY_HOLD_UNCLEARED
```

The claim can remain reviewable, but the authoritative completion state
cannot be silently finalized.

## Feature H4 --- Quality Evidence

Store:

-   document reference;
-   inspection date;
-   inspector;
-   result;
-   remarks;
-   evidence hash;
-   linked activity;
-   linked claim.

------------------------------------------------------------------------

# DOMAIN I --- VALIDATION

Preserve all V6 deterministic rules.

Add:

## I1 --- Completed Stage Mutation

`VAL_COMPLETED_STAGE_MUTATION`

Triggered when a normal claim attempts to modify a completed stage.

## I2 --- Quality Hold

`VAL_QUALITY_HOLD_UNCLEARED`

## I3 --- Contractor Attribution

Where contractor attribution is required by project configuration:

`VAL_CONTRACTOR_UNATTRIBUTED`

## I4 --- Project Scope

Reject or quarantine claims whose project/schedule context is invalid.

## I5 --- Reopen Validation

Reopen claims must satisfy explicit rework/reopen rules.

------------------------------------------------------------------------

# DOMAIN J --- EVIDENCE & PROVENANCE

Preserve:

-   EXIF timestamp;
-   GPS;
-   Haversine distance;
-   source hash;
-   source document metadata;
-   field-level provenance;
-   evidence fusion;
-   corroboration;
-   contradiction.

V7 additionally associates evidence with:

-   project;
-   schedule version;
-   stage;
-   contractor;
-   work package;
-   quality gate;
-   decision.

------------------------------------------------------------------------

# DOMAIN K --- COMPOUND IMPACT PREVIEW

## Feature K1 --- Existing Single-Activity Impact

Preserve V6 deterministic logic:

-   FS;
-   SS;
-   FF;
-   SF;
-   lag/lead;
-   controlling predecessor;
-   float absorption;
-   execution state;
-   bounded propagation.

## Feature K2 --- Compound Delay Simulation

Input:

``` json
[
  {"activity_id": "A100", "delay_days": 5},
  {"activity_id": "B200", "delay_days": 8},
  {"activity_id": "C300", "delay_days": 3}
]
```

The engine performs a multi-source forward propagation over the
dependency graph.

Output:

-   impacted activities;
-   controlling constraints;
-   absorbed delay;
-   net delay;
-   stage milestone slip;
-   project completion impact where calculable;
-   critical path exposure;
-   explanation for each impacted activity.

## Feature K3 --- Stage Delay Scenario

User can select multiple activities and simulate:

``` text
What happens if these activities are delayed simultaneously?
```

No schedule actuals are modified.

## Feature K4 --- Impact Explainability

Every result includes:

``` text
CAUSE
→ RELATIONSHIP
→ CONTROLLING PREDECESSOR
→ FLOAT
→ NET DELAY
→ DOWNSTREAM EFFECT
```

------------------------------------------------------------------------

# DOMAIN L --- CONFLICT & EVIDENCE INTELLIGENCE

Preserve:

-   same-date disagreement;
-   normal chronological progress;
-   progress regression;
-   quantity anomaly;
-   cross-channel corroboration;
-   cross-channel contradiction.

V7 adds contractor-aware context:

``` text
same activity
+
different contractors
+
contradictory claims
=
escalated investigation context
```

The system must not assume fraud or wrongdoing.

------------------------------------------------------------------------

# DOMAIN M --- INSTITUTIONAL MEMORY

## Feature M1 --- Incident Repository

Table concept:

``` text
institutional_incidents
```

Fields:

-   incident_id
-   project_id
-   stage_id
-   activity_id nullable
-   discipline
-   incident_type
-   title
-   narrative
-   root_cause
-   delay_days
-   cost_impact nullable
-   corrective_action
-   lessons_learned
-   recorded_by
-   recorded_at
-   embedding
-   status

## Feature M2 --- Incident Taxonomy

Examples:

-   GEOTECHNICAL_SURPRISE
-   WEATHER_SEVERITY
-   RIGHT_OF_WAY_BLOCKED
-   DESIGN_REVISION
-   VENDOR_SUPPLY_DELAY
-   LABOUR_SHORTAGE
-   CONTRACTOR_DELAY
-   REWORK
-   QUALITY_FAILURE
-   PERMIT_DELAY

## Feature M3 --- Hybrid Search

Search combines:

-   project metadata;
-   discipline;
-   contractor;
-   incident type;
-   semantic similarity.

## Feature M4 --- Closed-loop memory

When a supervisor decision identifies a reusable cause such as:

-   rework;
-   site blockage;
-   recurring delay;
-   design issue;

the UI may offer:

``` text
Record as institutional incident?
```

Human chooses whether to save it.

------------------------------------------------------------------------

# DOMAIN N --- SUPERVISING AGENT

## N1 --- Agent Purpose

The agent is a **decision-support supervisor**, not an autonomous
supervisor.

It watches project events and prepares:

-   risk briefings;
-   investigation context;
-   impact analysis;
-   historical analogues;
-   quality status;
-   contractor context;
-   recommended next investigation/action.

## N2 --- Agent Triggers

1.  New claim.
2.  New contradiction.
3.  Critical-path drift.
4.  Near-critical activity delay.
5.  Silent activity threshold.
6.  Quality hold.
7.  Stage milestone risk.
8.  Daily digest cutoff.
9.  Significant contractor discrepancy.
10. New institutional incident.

## N3 --- Agent Tools

The agent may call deterministic/read-only tools:

``` text
get_project_status()
get_stage_status()
get_activity_state()
get_impact_preview()
search_institutional_memory()
get_quality_gates()
get_contractor_context()
get_recent_conflicts()
get_review_queue()
```

Optional external weather verification may be provided as a read-only
evidence source when a reliable configured provider exists.

## N4 --- Agent Outputs

-   supervisor briefing;
-   recommended review priority;
-   causal explanation;
-   suggested clarification question;
-   historical context;
-   escalation suggestion;
-   draft approval dossier.

## N5 --- Agent Hard Restrictions

The agent must NEVER:

-   approve;
-   reject;
-   edit authoritative actuals;
-   change claimed quantities;
-   change claimed percentages;
-   clear quality gates;
-   modify schedule baseline;
-   write directly to P6;
-   bypass project permissions;
-   invent missing evidence.

------------------------------------------------------------------------

# DOMAIN O --- WEATHER / ENVIRONMENTAL VERIFICATION

This is an optional V7 intelligence module, not a hard dependency.

If enabled:

1.  project coordinates are used;
2.  weather observation/forecast source is queried;
3.  claimed weather delay is compared with recorded conditions;
4.  evidence is shown to the Supervisor;
5.  result is classified as:
    -   corroborated;
    -   inconsistent;
    -   unavailable.

The system must never automatically conclude that a weather claim is
false solely because an external source is unavailable.

No paid weather service is required for the core demo.

------------------------------------------------------------------------

# DOMAIN P --- DASHBOARD

The Dashboard becomes project-centric.

## P1 --- Executive project view

Display:

-   project progress;
-   planned vs actual;
-   current stage;
-   stage completion;
-   milestone variance;
-   critical/near-critical exposure;
-   quality holds;
-   active contractor work;
-   unresolved review items;
-   delay reasons;
-   silent activities.

## P2 --- Stage view

For each stage:

-   progress;
-   status;
-   weight;
-   planned/actual dates;
-   blocking activities;
-   quality gates;
-   contractor breakdown.

## P3 --- Contractor view

Descriptive performance indicators only.

## P4 --- Progress visualization

Support:

-   stage progress bars;
-   project S-curve where data supports it;
-   milestone timeline;
-   weighted progress;
-   planned/actual comparison.

------------------------------------------------------------------------

# DOMAIN Q --- PROJECT-CENTRIC UI

## Q1 --- Global Project Switcher

Top bar:

``` text
Project
Schedule Version
Current Progress
Current Stage
```

Changing project reloads all project-scoped queries.

## Q2 --- WBS Explorer

Hierarchy:

``` text
Project
→ Stage
→ WBS
→ Discipline
→ Activity
```

Activity status indicators:

-   Not Started
-   In Progress
-   Completed
-   Blocked
-   Quality Hold
-   Rework

## Q3 --- Review Workspace

Add:

-   project;
-   stage;
-   contractor;
-   work package;
-   quality gate status;
-   activity execution state;
-   matching eligibility explanation;
-   impact preview;
-   memory suggestions.

## Q4 --- Impact Preview

Add:

-   multi-select activity simulation;
-   delay vector;
-   stage impact;
-   project impact;
-   causal chain.

## Q5 --- Activity History

Tree navigation by:

``` text
Project → Stage → Discipline → Activity
```

Timeline remains chronological.

------------------------------------------------------------------------

# DOMAIN R --- AUDIT & GOVERNANCE

## R1 --- SHA-256 Audit Chain

Preserve existing chain.

Add:

-   project_id;
-   schedule_id;
-   actor role;
-   entity context.

## R2 --- Project-scoped Audit

Auditors and authorized project managers can inspect:

-   original evidence;
-   extracted claim;
-   candidate matches;
-   validations;
-   human decisions;
-   before/after values;
-   approved actual;
-   export/write-back events.

## R3 --- Statutory / Compliance Dossier

One-click dossier generation containing:

1.  project metadata;
2.  schedule version metadata;
3.  relevant WBS/activity records;
4.  claim source;
5.  source document hash;
6.  extraction fields;
7.  field provenance;
8.  matching candidates;
9.  validation results;
10. evidence metadata;
11. quality-gate status;
12. contractor/work-package attribution;
13. Supervisor decision;
14. decision justification;
15. approved actual;
16. downstream export status;
17. audit-chain entries;
18. cryptographic verification summary.

The dossier is an evidence package. It must not claim certification by
CAG/CVC or any regulator.

------------------------------------------------------------------------

# DOMAIN S --- REPORTING

Preserve:

-   delay reasons;
-   forecast;
-   history;
-   execution summary;
-   CSV export;
-   P6 mock write-back.

Add:

-   project-scoped reports;
-   stage reports;
-   contractor reports;
-   quality reports;
-   audit dossier;
-   project progress report;
-   impact scenario report.

------------------------------------------------------------------------

# DOMAIN T --- SECURITY / RBAC / RLS

## T1 --- Authentication

Preserve Supabase Auth/JWT.

## T2 --- Backend authorization

Every protected endpoint validates:

``` text
authenticated user
→ role
→ project membership
→ requested project
→ requested schedule
```

## T3 --- Supabase RLS

Project-scoped rows must enforce project isolation.

## T4 --- Role matrix

### SITE_ENGINEER

Can:

-   submit claims;
-   upload evidence;
-   answer clarification;
-   view permitted project execution context.

Cannot:

-   approve;
-   reject;
-   edit authoritative actuals;
-   access audit administration.

### SUPERVISOR

Can:

-   review;
-   approve;
-   edit;
-   reject;
-   hold;
-   inspect evidence;
-   resolve WBS splits;
-   request reopen;
-   inspect quality.

### PROJECT_MANAGER

Can:

-   view project dashboards;
-   manage project/schedule context;
-   manage contractors/work packages;
-   inspect reports;
-   generate dossiers;
-   view project-level analytics.

### AUDITOR

Can:

-   read project-scoped audit records;
-   inspect evidence;
-   generate dossiers;
-   verify hash chain.

Auditor cannot modify operational state.

------------------------------------------------------------------------

# DOMAIN U --- PERFORMANCE & SCALABILITY

V7 should remain a modular monolith.

## U1 --- Database

Optimize:

-   project_id indexes;
-   schedule_id indexes;
-   stage_id indexes;
-   activity state indexes;
-   candidate query indexes;
-   audit indexes.

## U2 --- Matching

Use:

``` text
SQL eligibility prefilter
→ candidate retrieval
→ semantic/fuzzy scoring
```

Do not load every activity into memory for every claim.

## U3 --- Vector retrieval

Partition by:

``` text
project_id + schedule_id
```

## U4 --- API

All list endpoints:

-   paginate;
-   filter by project;
-   filter by stage/status;
-   avoid unbounded queries.

## U5 --- Agent

Use event-driven/background execution where supported, but do not
introduce distributed infrastructure unnecessarily.

------------------------------------------------------------------------

# 7. V7 Database Model

The existing tables are preserved where possible.

## 7.1 Existing core tables

-   profiles
-   schedules
-   schedule_activities
-   schedule_dependencies
-   source_documents
-   execution_events
-   source_references
-   candidate_matches
-   conflict_records
-   validation_issues
-   planner_decisions
-   approved_actuals
-   audit_logs
-   claim_activity_splits
-   evidence_links
-   execution_summaries

## 7.2 New tables

### projects

``` sql
projects(
  project_id UUID PRIMARY KEY,
  project_code TEXT UNIQUE NOT NULL,
  project_name TEXT NOT NULL,
  client_name TEXT,
  project_type TEXT,
  location TEXT,
  latitude DOUBLE PRECISION,
  longitude DOUBLE PRECISION,
  geofence_radius_m REAL,
  planned_start DATE,
  planned_finish DATE,
  contract_finish DATE,
  status TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
)
```

### stages

``` sql
stages(
  stage_id UUID PRIMARY KEY,
  project_id UUID NOT NULL REFERENCES projects,
  schedule_id UUID NOT NULL REFERENCES schedules,
  stage_name TEXT NOT NULL,
  sequence_order INTEGER,
  weight_pct REAL,
  status TEXT NOT NULL,
  planned_start DATE,
  planned_finish DATE,
  contract_milestone_date DATE,
  gating_predecessor_stage_id UUID NULL,
  completion_rule JSONB,
  created_at TIMESTAMPTZ DEFAULT now()
)
```

### project_memberships

``` sql
project_memberships(
  membership_id UUID PRIMARY KEY,
  user_id UUID NOT NULL,
  project_id UUID NOT NULL REFERENCES projects,
  assigned_role TEXT NOT NULL,
  contractor_id UUID NULL,
  active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT now()
)
```

### contractors

``` sql
contractors(
  contractor_id UUID PRIMARY KEY,
  company_name TEXT NOT NULL,
  contract_reference TEXT,
  contact_email TEXT,
  active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT now()
)
```

### work_packages

``` sql
work_packages(
  work_package_id UUID PRIMARY KEY,
  project_id UUID NOT NULL REFERENCES projects,
  contractor_id UUID REFERENCES contractors,
  stage_id UUID REFERENCES stages,
  package_code TEXT,
  package_name TEXT NOT NULL,
  discipline TEXT,
  planned_start DATE,
  planned_finish DATE,
  status TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
)
```

### quality_gates

``` sql
quality_gates(
  quality_gate_id UUID PRIMARY KEY,
  project_id UUID NOT NULL REFERENCES projects,
  stage_id UUID NULL REFERENCES stages,
  activity_id TEXT NULL,
  gate_type TEXT NOT NULL,
  gate_name TEXT NOT NULL,
  required BOOLEAN DEFAULT TRUE,
  status TEXT NOT NULL,
  due_date DATE,
  passed_at TIMESTAMPTZ NULL,
  passed_by UUID NULL,
  remarks TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
)
```

### quality_evidence

``` sql
quality_evidence(
  quality_evidence_id UUID PRIMARY KEY,
  quality_gate_id UUID NOT NULL REFERENCES quality_gates,
  source_document_id UUID NULL,
  evidence_type TEXT,
  result TEXT,
  inspector_name TEXT,
  inspection_date DATE,
  evidence_hash TEXT,
  metadata JSONB,
  created_at TIMESTAMPTZ DEFAULT now()
)
```

### institutional_incidents

``` sql
institutional_incidents(
  incident_id UUID PRIMARY KEY,
  project_id UUID NOT NULL REFERENCES projects,
  stage_id UUID NULL REFERENCES stages,
  activity_id TEXT NULL,
  discipline TEXT,
  contractor_id UUID NULL REFERENCES contractors,
  incident_type TEXT,
  title TEXT NOT NULL,
  narrative TEXT,
  root_cause TEXT,
  delay_days REAL,
  cost_impact REAL NULL,
  corrective_action TEXT,
  lessons_learned TEXT,
  embedding VECTOR NULL,
  recorded_by UUID,
  recorded_at TIMESTAMPTZ DEFAULT now(),
  status TEXT
)
```

### incident_evidence

``` sql
incident_evidence(
  incident_evidence_id UUID PRIMARY KEY,
  incident_id UUID NOT NULL REFERENCES institutional_incidents,
  source_document_id UUID NULL,
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
)
```

### contractor_disputes

``` sql
contractor_disputes(
  dispute_id UUID PRIMARY KEY,
  project_id UUID NOT NULL REFERENCES projects,
  contractor_id UUID NOT NULL REFERENCES contractors,
  incident_id UUID NULL REFERENCES institutional_incidents,
  claimed_extension_days REAL,
  granted_extension_days REAL,
  settlement_terms TEXT,
  status TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
)
```

### impact_scenarios

``` sql
impact_scenarios(
  scenario_id UUID PRIMARY KEY,
  project_id UUID NOT NULL REFERENCES projects,
  schedule_id UUID NOT NULL REFERENCES schedules,
  name TEXT,
  created_by UUID,
  created_at TIMESTAMPTZ DEFAULT now(),
  inputs JSONB NOT NULL,
  results JSONB NOT NULL
)
```

### agent_briefings

``` sql
agent_briefings(
  briefing_id UUID PRIMARY KEY,
  project_id UUID NOT NULL REFERENCES projects,
  trigger_type TEXT,
  severity TEXT,
  title TEXT,
  briefing_text TEXT,
  evidence_refs JSONB,
  recommended_action TEXT,
  generated_at TIMESTAMPTZ DEFAULT now(),
  status TEXT
)
```

## 7.3 Existing table modifications

### schedules

Add:

-   project_id
-   version_code
-   version metadata
-   source hash
-   active flag
-   supersedes_schedule_id

### schedule_activities

Add:

-   project_id
-   stage_id
-   weight_factor
-   weight_basis
-   quality_gate_required
-   work_package_id
-   contractor_id where appropriate

Retain:

-   total_float
-   is_critical

### schedule_dependencies

Retain/add:

-   lag_days

### execution_events

Add:

-   project_id
-   stage_id
-   contractor_id
-   work_package_id
-   reopen workflow fields
-   quality context
-   project-scoped provenance

### approved_actuals

Add:

-   project_id
-   stage_id
-   reopen/rework metadata where required

### audit_logs

Add:

-   project_id
-   schedule_id
-   role
-   entity context

------------------------------------------------------------------------

# 8. API V7

## Project

``` http
POST /api/v1/projects
GET /api/v1/projects
GET /api/v1/projects/{project_id}
PATCH /api/v1/projects/{project_id}
GET /api/v1/projects/{project_id}/progress
GET /api/v1/projects/{project_id}/stages
GET /api/v1/projects/{project_id}/contractors
GET /api/v1/projects/{project_id}/work-packages
GET /api/v1/projects/{project_id}/briefing
```

## Schedule

``` http
POST /api/v1/projects/{project_id}/schedules
GET /api/v1/projects/{project_id}/schedules
GET /api/v1/schedules/{schedule_id}
GET /api/v1/schedules/{schedule_id}/activities
GET /api/v1/schedules/{schedule_id}/dependencies
GET /api/v1/schedules/{schedule_id}/wbs-tree
```

POST schedule must accept:

-   CSV
-   XLSX
-   XER

## Claims

Preserve:

``` http
POST /claims/file
POST /claims/text
POST /claims/schedule-export
GET /claims/{event_id}
GET /claims
POST /claims/{event_id}/clarify
POST /claims/{event_id}/match
GET /claims/{event_id}/candidates
POST /claims/{event_id}/rematch
GET /claims/{event_id}/splits
PATCH /claims/{event_id}/splits
POST /claims/{event_id}/check
GET /claims/{event_id}/conflicts
GET /claims/{event_id}/validation
GET /claims/{event_id}/evidence
```

All are project-scoped.

## State/reopen

``` http
POST /api/v1/claims/{event_id}/reopen-request
GET /api/v1/activities/{activity_id}/state
GET /api/v1/stages/{stage_id}/state
```

## Quality

``` http
GET /api/v1/activities/{activity_id}/quality-gates
POST /api/v1/quality-gates
POST /api/v1/quality-gates/{id}/evidence
POST /api/v1/quality-gates/{id}/pass
POST /api/v1/quality-gates/{id}/fail
```

## Contractors

``` http
POST /api/v1/projects/{project_id}/contractors
GET /api/v1/projects/{project_id}/contractors
POST /api/v1/projects/{project_id}/work-packages
GET /api/v1/projects/{project_id}/work-packages
```

## Impact

Preserve:

``` http
GET /api/v1/schedule/{activity_id}/impact-preview?delay_days=N
```

Add:

``` http
POST /api/v1/projects/{project_id}/impact-simulations
GET /api/v1/impact-simulations/{scenario_id}
```

## Memory

``` http
POST /api/v1/memory/incidents
GET /api/v1/memory/incidents
GET /api/v1/memory/search
```

## Agent

``` http
GET /api/v1/agent/briefing
POST /api/v1/agent/analyze/{event_id}
```

Agent endpoints are read/recommendation oriented.

## Dossier

``` http
POST /api/v1/projects/{project_id}/dossier
GET /api/v1/dossiers/{dossier_id}
```

## Existing reporting

Preserve and project-scope:

``` http
GET /dashboard/delay-reasons
GET /dashboard/forecast
GET /activities/{activity_id}/history
GET /graph/activity/{activity_id}
GET /reports/execution-summary
GET /export/csv
POST /mock-p6/activities/{activity_id}
```

------------------------------------------------------------------------

# 9. Frontend V7 Information Architecture

``` text
Login
  ↓
Project Selector / Project Context
  ↓
Project Dashboard
  ├── Daily Digest
  ├── Review Workspace
  ├── WBS Explorer
  ├── Activities
  ├── Impact Simulator
  ├── Quality
  ├── Contractors
  ├── History
  ├── Institutional Memory
  ├── Reports
  └── Audit / Dossier
```

## Site Engineer

Primary:

-   Project
-   Claim Intake
-   My submitted claims
-   Evidence
-   clarification

## Supervisor

Primary:

-   Project Dashboard
-   Daily Digest
-   Review Workspace
-   Impact
-   Quality
-   History
-   Ask Why
-   Memory
-   reports

## Project Manager

Primary:

-   Dashboard
-   Progress
-   Stages
-   Contractors
-   Work packages
-   Impact
-   reports
-   dossier

## Auditor

Primary:

-   Audit
-   Dossier
-   Evidence
-   History
-   read-only project intelligence

------------------------------------------------------------------------

# 10. V7 Supervising Agent Architecture

``` text
PROJECT EVENTS
    ↓
EVENT FILTER
    ↓
AGENT ORCHESTRATOR
    ↓
┌───────────────────────────────┐
│ Deterministic read-only tools │
├───────────────────────────────┤
│ project status                │
│ stage status                  │
│ matching evidence             │
│ impact preview                │
│ quality gates                 │
│ contractor context            │
│ institutional memory          │
│ audit history                 │
└───────────────────────────────┘
    ↓
LLM REASONING / NARRATIVE
    ↓
STRUCTURED BRIEFING
    ↓
SUPERVISOR
```

LLM output must be grounded in tool results.

Required structured output:

``` json
{
  "severity": "LOW|MEDIUM|HIGH|CRITICAL",
  "summary": "...",
  "evidence": [],
  "reasoning": [],
  "recommended_action": "...",
  "requires_human_review": true
}
```

------------------------------------------------------------------------

# 11. AI Architecture

All LLM calls continue through:

``` text
backend/shared/llm_client.py
```

Configuration:

``` text
LLM_PROVIDER
LLM_API_KEY
LLM_MODEL
```

V7 deployment must use a **new isolated Supabase project and new AI
credentials** for the V7 environment so the old deployed environment
remains untouched.

Recommended:

``` text
V6 / deployed
    → frozen
    → old Supabase
    → old Groq/Gemini credentials

V7
    → new Supabase
    → new Groq key
    → new Gemini key if used
    → new Vercel project/environment
    → new Render service/environment
```

Secrets must never be committed.

------------------------------------------------------------------------

# 12. Deployment Architecture

``` text
                    ┌───────────────┐
                    │    Vercel     │
                    │ React/Vite    │
                    └───────┬───────┘
                            │ HTTPS
                    ┌───────▼───────┐
                    │    Render     │
                    │ FastAPI       │
                    └───────┬───────┘
                            │
          ┌─────────────────┼──────────────────┐
          │                 │                  │
     ┌────▼────┐      ┌─────▼────┐      ┌─────▼─────┐
     │ Supabase│      │ Groq     │      │ Gemini    │
     │ Postgres│      │ LLM      │      │ fallback  │
     │ Auth    │      └──────────┘      └───────────┘
     └─────────┘
```

Optional:

-   object storage for evidence;
-   weather provider;
-   vector storage using pgvector or isolated in-memory project/schedule
    indexes.

------------------------------------------------------------------------

# 13. Build Strategy

V7 is an additive evolution, not a rewrite.

## Keep

-   v6 extraction schemas;
-   shared LLM client;
-   four-tier matching;
-   semantic/fuzzy scoring;
-   WBS bridge;
-   deterministic validation;
-   evidence fusion;
-   priority scoring;
-   approved actuals contract;
-   SHA-256 audit chain;
-   impact math;
-   forecast;
-   knowledge graph;
-   Ask Why;
-   P6 adapter;
-   CSV export;
-   existing UI design system.

## Modify

-   project/schedule context;
-   schedule schema;
-   activity schema;
-   dependency schema;
-   execution event schema;
-   approved actual schema;
-   audit schema;
-   matching eligibility;
-   WBS allocation;
-   actuals reopen logic;
-   project progress;
-   impact API;
-   dashboards;
-   review workspace;
-   AppShell;
-   authorization;
-   RLS;
-   FAISS indexing.

## Build new

-   project domain;
-   schedule version management;
-   stages;
-   project memberships;
-   contractors;
-   work packages;
-   quality gates;
-   quality evidence;
-   institutional incidents;
-   contractor disputes;
-   compound impact scenarios;
-   supervising agent;
-   agent briefings;
-   dossier generator;
-   project switcher;
-   project-centric dashboard;
-   quality UI;
-   contractor UI;
-   memory UI.

## Deprecate

-   implicit latest schedule fallback;
-   global unscoped matching;
-   legacy WBS split table if duplicated by `claim_activity_splits`;
-   frontend-only authorization assumptions.

------------------------------------------------------------------------

# 14. Two-Member Build Plan

Team:

-   **Member 1 --- Backend / Data / Intelligence**
-   **Member 2 --- Frontend / UX / Integration / QA**

Both members must work from the same API contracts and migration plan.

------------------------------------------------------------------------

## PHASE 0 --- BASELINE FREEZE

### Member 1

1.  Freeze current repository.
2.  Create `v6-baseline` tag.
3.  Create V7 branch.
4.  Export/document current schema.
5.  Record current test suite.
6.  Record current API inventory.
7.  Verify current actuals, matching, impact and audit tests.
8.  Create V7 migration strategy.

### Member 2

1.  Deploy untouched V6 frontend.
2.  Record current routes.
3.  Capture screenshots of working flows.
4.  Document current UI contracts.
5.  Create frontend V7 branch.
6.  Verify current build.

### Exit

-   V6 deployable.
-   V6 rollback available.
-   V7 branch created.
-   No V7 work has damaged V6.

------------------------------------------------------------------------

# PHASE 1 --- PROJECT FOUNDATION

### Member 1 --- Backend/Data

Build:

-   projects;
-   project memberships;
-   schedule project FK;
-   schedule version;
-   RLS;
-   project-scoped repository functions;
-   auth project membership checks.

Modify:

-   `schedule_context.py`;
-   `schedule_repository.py`;
-   all routers using implicit schedule resolution.

Remove:

``` text
latest schedule fallback
```

### Member 2 --- Frontend

Build:

-   Project Switcher;
-   Project Context Provider;
-   project-aware API client;
-   project-aware route loading;
-   project dashboard shell.

Modify:

-   AppShell;
-   Login routing;
-   all page queries.

### Gate

A user can switch between two projects and see completely different
data.

------------------------------------------------------------------------

# PHASE 2 --- STATE-AWARE MATCHING & STAGE MODEL

### Member 1

Build:

-   stages;
-   stage status engine;
-   eligibility engine;
-   completed activity filter;
-   completed stage filter;
-   reopen/rework workflow.

Modify:

-   matching;
-   WBS allocation;
-   actuals.

Fix:

-   reopened `actual_finish` bug.

### Member 2

Build:

-   WBS stage hierarchy UI;
-   activity state badges;
-   stage status display;
-   reopen request UI;
-   candidate eligibility explanations.

### Gate

Completed activities never appear in ordinary matching.

Completed stages never appear in ordinary matching.

Reopen requires explicit workflow.

------------------------------------------------------------------------

# PHASE 3 --- PROGRESS / EVM / MILESTONES

### Member 1

Build:

-   activity weight;
-   weighted progress;
-   stage rollup;
-   project rollup;
-   milestone evaluator;
-   stage gating.

### Member 2

Build:

-   project progress dashboard;
-   stage cards;
-   progress visualization;
-   milestone timeline;
-   planned vs actual views.

### Gate

Progress is mathematically reproducible from activity weights and
approved actuals.

------------------------------------------------------------------------

# PHASE 4 --- XER + CONTRACTORS + WORK PACKAGES

### Member 1

Build:

-   native XER schedule ingestion;
-   contractors;
-   work packages;
-   claim attribution;
-   contractor metrics.

### Member 2

Build:

-   XER upload UI;
-   contractor directory;
-   work package UI;
-   contractor filter;
-   contractor dashboard.

### Gate

A real XER creates a project schedule version without CSV conversion.

A claim can be traced to contractor/work package.

------------------------------------------------------------------------

# PHASE 5 --- QUALITY GATES / ITP

### Member 1

Build:

-   quality schema;
-   quality gate evaluator;
-   quality evidence;
-   validation rules;
-   completion blocking.

### Member 2

Build:

-   quality status panels;
-   inspection evidence upload;
-   hold-point workflow;
-   review integration.

### Gate

A quality-gated activity cannot silently become authoritative 100%
complete without required quality clearance.

------------------------------------------------------------------------

# PHASE 6 --- COMPOUND IMPACT

### Member 1

Build:

-   multi-source delay propagation;
-   scenario engine;
-   stage/project impact calculations;
-   scenario persistence.

Preserve existing single-activity math.

### Member 2

Build:

-   multi-select impact UI;
-   delay vector editor;
-   impact graph;
-   stage milestone impact;
-   explanation panels.

### Gate

Five simultaneous delays produce deterministic, explainable results.

------------------------------------------------------------------------

# PHASE 7 --- INSTITUTIONAL MEMORY

### Member 1

Build:

-   incidents;
-   taxonomy;
-   embeddings;
-   hybrid retrieval;
-   incident/activity/contractor linkage.

### Member 2

Build:

-   Memory Explorer;
-   incident creation;
-   related incident panel;
-   review-time memory suggestions.

### Gate

A current delay can retrieve relevant historical incidents without
hallucinated facts.

------------------------------------------------------------------------

# PHASE 8 --- SUPERVISING AGENT

### Member 1

Build:

-   agent orchestrator;
-   read-only tools;
-   briefing generator;
-   trigger engine;
-   structured outputs;
-   safety restrictions.

### Member 2

Build:

-   Agent briefing UI;
-   severity cards;
-   evidence drawer;
-   recommendation panel;
-   "why" / evidence drill-down;
-   human action controls.

### Gate

Agent recommends; human decides.

No agent write path to approved actuals.

------------------------------------------------------------------------

# PHASE 9 --- AUDIT DOSSIER / ENTERPRISE REPORTING

### Member 1

Build:

-   dossier backend;
-   evidence collector;
-   audit verification;
-   report generation;
-   project-scoped audit endpoints.

### Member 2

Build:

-   dossier generation UI;
-   report preview;
-   audit timeline;
-   export/download workflow;
-   auditor read-only workspace.

### Gate

One action generates a traceable evidence package for a selected
project/activity/claim.

------------------------------------------------------------------------

# PHASE 10 --- FULL SYSTEM HARDENING

### Member 1

Test:

-   multi-project isolation;
-   RLS;
-   matching;
-   state transitions;
-   actuals;
-   quality;
-   impact;
-   audit;
-   agent grounding;
-   adapter failures.

### Member 2

Test:

-   all role flows;
-   responsive behavior;
-   project switching;
-   review workspace stability;
-   scroll/jump behavior;
-   loading/error states;
-   localization;
-   dossier UX.

### Joint

Run:

-   unit tests;
-   integration tests;
-   regression suite;
-   benchmark;
-   end-to-end demo;
-   production deployment smoke tests.

------------------------------------------------------------------------

# 15. Parallel Work Ownership Matrix

  Area                Member 1        Member 2
  ------------------- --------------- -------------------
  DB migrations       Primary         Review
  FastAPI             Primary         API integration
  Auth/RLS            Primary         UI role behavior
  Projects            Primary         UI
  Schedule versions   Primary         UI
  Stages              Primary         UI
  Matching            Primary         explanation UI
  Actuals             Primary         display
  EVM                 Primary         charts
  XER                 Primary         upload UI
  Contractors         Primary         UI
  Work packages       Primary         UI
  Quality             Primary         UI
  Impact engine       Primary         simulator UI
  Memory              Primary         explorer UI
  Agent               Primary         briefing UI
  Dossier             Primary         preview/export UI
  Audit               Primary         audit UI
  Testing             Backend tests   E2E/UI tests
  Deployment          Backend         Frontend

------------------------------------------------------------------------

# 16. Branch Strategy

``` text
main
│
├── v6-baseline
│
└── v7-development
    │
    ├── v7/project-foundation
    ├── v7/state-aware-matching
    ├── v7/progress-evm
    ├── v7/xer-contractors
    ├── v7/quality
    ├── v7/compound-impact
    ├── v7/institutional-memory
    ├── v7/supervising-agent
    └── v7/audit-dossier
```

Prefer small feature branches and frequent merges.

------------------------------------------------------------------------

# 17. Testing Strategy

## 17.1 Existing V6 benchmark must remain

Retain the 45-activity benchmark and all existing intelligence
scenarios.

## 17.2 New V7 benchmark scenarios

At minimum:

1.  Project A vs Project B isolation.
2.  Project A claim cannot match Project B.
3.  Active schedule selection.
4.  Historical schedule remains immutable.
5.  Completed activity excluded.
6.  Completed stage excluded.
7.  Reopen request.
8.  Reopen approval.
9.  Reopened finish date reconciliation.
10. Stage auto-completion.
11. Stage gate blocking.
12. Project weighted progress.
13. XER import.
14. Contractor attribution.
15. Work-package attribution.
16. Quality gate pending.
17. Quality gate pass.
18. Quality gate failure.
19. Quality completion block.
20. Compound delay.
21. Multiple controlling predecessors.
22. Float absorption.
23. Institutional memory retrieval.
24. Agent grounding.
25. Agent cannot mutate actuals.
26. Dossier generation.
27. Auditor read-only access.
28. Cross-project RLS denial.
29. Adapter failure after approval.
30. New V7 Supabase deployment.

## 17.3 Security tests

Every protected endpoint must test:

``` text
No JWT
Wrong role
Wrong project
Wrong schedule
Valid role + valid project
```

------------------------------------------------------------------------

# 18. Definition of Done

V7 is complete only when:

## Domain

-   [ ] project is explicit;
-   [ ] schedule version is explicit;
-   [ ] stage is explicit;
-   [ ] activity is state-aware;
-   [ ] contractor/work package exists;
-   [ ] quality gate exists;
-   [ ] project isolation works.

## Matching

-   [ ] completed activities excluded;
-   [ ] completed stages excluded;
-   [ ] reopen workflow exists;
-   [ ] WBS split respects state;
-   [ ] candidate explanation exists.

## Progress

-   [ ] activity weighting exists;
-   [ ] stage rollup works;
-   [ ] project rollup works;
-   [ ] stage completion works;
-   [ ] milestone gating works.

## Ingestion

-   [ ] CSV works;
-   [ ] XLSX works;
-   [ ] XER works;
-   [ ] field claims work;
-   [ ] OCR works;
-   [ ] voice works;
-   [ ] multilingual flow works.

## Quality

-   [ ] gates can be defined;
-   [ ] evidence can be attached;
-   [ ] gate status is visible;
-   [ ] blocked completion is enforced.

## Intelligence

-   [ ] single impact works;
-   [ ] compound impact works;
-   [ ] memory retrieval works;
-   [ ] agent briefing works;
-   [ ] deterministic explanations work.

## Governance

-   [ ] Supervisor approval required;
-   [ ] audit chain intact;
-   [ ] project-scoped audit;
-   [ ] dossier generation works;
-   [ ] Auditor read-only access works.

## Deployment

-   [ ] V7 has isolated Supabase;
-   [ ] V7 has isolated LLM keys;
-   [ ] V7 frontend deployed;
-   [ ] V7 backend deployed;
-   [ ] production environment variables configured;
-   [ ] no secrets committed;
-   [ ] deterministic fallback tested.

------------------------------------------------------------------------

# 19. Primary V7 Demonstration

Target: 5--8 minutes.

1.  Login.
2.  Select Project A.
3.  Show project progress.
4.  Show active stage.
5.  Upload XER baseline.
6.  Show WBS tree.
7.  Submit field report.
8.  Show extraction.
9.  Show contractor/work package attribution.
10. Show state-aware candidate filtering.
11. Show top candidates.
12. Show WBS split if applicable.
13. Show validation.
14. Show quality gate status.
15. Switch Supervisor.
16. Show prioritized review queue.
17. Show provenance/evidence.
18. Show Ask Why.
19. Show institutional-memory match.
20. Show agent briefing.
21. Approve/edit/reject with justification.
22. Show approved actual.
23. Show stage progress update.
24. Show project progress update.
25. Open compound impact simulator.
26. Simulate multiple delays.
27. Show downstream impact.
28. Open contractor dashboard.
29. Open activity history.
30. Generate audit dossier.
31. Show SHA-256 audit verification.
32. Show CSV/P6 mock output.

------------------------------------------------------------------------

# 20. V7 Release Tiers

## V7 Core --- Mandatory

-   project context;
-   schedule versioning;
-   RLS/RBAC;
-   stage model;
-   state-aware matching;
-   completed activity/stage exclusion;
-   reopen workflow;
-   progress weighting;
-   stage completion;
-   XER import;
-   contractor/work package;
-   quality gates;
-   compound impact;
-   project dashboard.

## V7 Intelligence --- Mandatory for full V7

-   institutional memory;
-   supervising agent;
-   agent briefings;
-   project intelligence;
-   contractor analytics;
-   impact scenarios.

## V7 Enterprise --- Mandatory for enterprise-oriented demonstration

-   audit dossier;
-   Auditor role;
-   project-scoped audit;
-   deployment isolation;
-   security hardening;
-   regression suite;
-   operational monitoring.

------------------------------------------------------------------------

# 21. V7 Feature Coverage Cross-Check

## V6 → V7 preservation check

  V6 capability          V7 treatment
  ---------------------- -----------------------------------------------
  Schedule ingestion     MODIFY --- add project/version + XER baseline
  PDF/XLSX/CSV/TXT       KEEP + project scope
  Typed intake           KEEP
  Voice                  KEEP
  OCR                    KEEP
  Multilingual UI        KEEP
  Runtime translation    KEEP
  LLM extraction         KEEP
  Clarification          KEEP
  Four-tier matching     MODIFY --- eligibility first
  Top-3 candidates       KEEP
  Unmatched claims       KEEP
  WBS bridge             MODIFY --- state-aware
  Conflict detection     KEEP + contractor context
  Physical validation    KEEP
  Sequence validation    KEEP
  Evidence checks        KEEP
  Evidence fusion        KEEP
  Review workspace       MODIFY
  Priority scoring       KEEP + project context
  Human decisions        KEEP
  Approved actuals       MODIFY
  CSV export             KEEP
  P6 adapter             KEEP
  Delay dashboard        MODIFY
  Institutional memory   MAJOR EXPANSION
  Daily Digest           MODIFY
  Activity History       MODIFY
  Silent activity        KEEP + agent trigger
  Impact Preview         MODIFY --- compound
  Knowledge graph        KEEP
  Ask Why                KEEP
  Forecast               KEEP
  AI Execution Summary   MODIFY --- project/stage scope
  SHA-256 audit          MODIFY --- project scope
  P6 mock                KEEP
  Dark/light theme       KEEP
  Fallback mocks         KEEP

## Audit → V7 coverage check

  -----------------------------------------------------------------------
  Audit discovery                     V7 response
  ----------------------------------- -----------------------------------
  Implicit singleton contamination    Removed

  Reopen finish corruption            Fixed

  XER parser underuse                 Promoted to baseline ingestion

  Completed activity pollution        Eliminated

  Project hierarchy                   Added

  Schedule versioning                 Added

  State-aware matching                Added

  Stage completion                    Added

  Weighted progress/EVM               Added

  Contractor ledger                   Added

  Work packages                       Added

  Quality gates                       Added

  Compound impact                     Added

  Institutional memory                Added

  Supervising Agent                   Added

  Project-centric UI                  Added

  Project membership                  Added

  RLS                                 Added

  Partitioned retrieval               Added

  Audit dossier                       Added

  Optional weather verification       Added as optional

  Offline-first field capture         Reserved as V7.1 unless
                                      implementation
                                      evidence/requirements justify V7

  Full P6 replacement                 Explicitly rejected

  Autonomous approval                 Explicitly rejected

  Database-per-project                Explicitly rejected

  LLM validation replacement          Explicitly rejected
  -----------------------------------------------------------------------

------------------------------------------------------------------------

# 22. V7.1 / Future Backlog

Not required to block V7:

1.  Offline-first PWA synchronization.
2.  Advanced local Whisper pipeline.
3.  richer geospatial evidence.
4.  external weather provider integrations.
5.  advanced predictive forecasting.
6.  full resource/cost EVM.
7.  broader CPM analysis.
8.  richer contractor dispute workflow.
9.  enterprise SSO.
10. advanced document classification.
11. automated schedule-version comparison.
12. persistent pgvector retrieval if scale requires it.
13. configurable organization-level policy engine.

------------------------------------------------------------------------

# 23. Final Architecture

``` text
                         SETUAI V7
                             │
              ┌──────────────┴──────────────┐
              │      PROJECT CONTROL        │
              │ Project / Version / RBAC    │
              └──────────────┬──────────────┘
                             │
                    ┌────────▼────────┐
                    │ Schedule / WBS  │
                    │ Stage / Activity│
                    └────────┬────────┘
                             │
                ┌────────────▼────────────┐
                │   FIELD REALITY INTAKE  │
                │ Text / Voice / Docs /   │
                │ Images / P6 / XLSX/XER  │
                └────────────┬────────────┘
                             │
                    ┌────────▼────────┐
                    │ AI EXTRACTION   │
                    │ + PROVENANCE    │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │ STATE-AWARE     │
                    │ MATCHING        │
                    └────────┬────────┘
                             │
              ┌──────────────▼──────────────┐
              │ DETERMINISTIC VERIFICATION │
              │ Physical / Sequence /      │
              │ Conflict / Evidence /      │
              │ Quality / Reopen           │
              └──────────────┬──────────────┘
                             │
                    ┌────────▼────────┐
                    │ HUMAN SUPERVISOR│
                    │ APPROVE / EDIT  │
                    │ REJECT / HOLD   │
                    └────────┬────────┘
                             │
                  ┌──────────▼───────────┐
                  │ AUTHORITATIVE        │
                  │ APPROVED ACTUALS     │
                  └──────────┬───────────┘
                             │
       ┌─────────────────────┼─────────────────────────┐
       │                     │                         │
┌──────▼──────┐      ┌───────▼────────┐       ┌───────▼────────┐
│ PROGRESS    │      │ IMPACT         │       │ QUALITY /      │
│ EVM / Stage │      │ SINGLE +       │       │ CONTRACTOR     │
│ / Project   │      │ COMPOUND       │       │ INTELLIGENCE   │
└──────┬──────┘      └───────┬────────┘       └───────┬────────┘
       │                     │                         │
       └─────────────────────┼─────────────────────────┘
                             │
                 ┌───────────▼───────────┐
                 │ SUPERVISING AGENT     │
                 │ MEMORY / BRIEFINGS /  │
                 │ INVESTIGATION         │
                 └───────────┬───────────┘
                             │
                 ┌───────────▼───────────┐
                 │ AUDIT / DOSSIER /     │
                 │ EXPORT / P6 ADAPTER   │
                 └───────────────────────┘
```

------------------------------------------------------------------------

# 24. Final V7 Product Statement

SETUAI v7 is not merely a better claim matcher.

It is a **project execution intelligence and governance layer** that
creates a continuous chain:

``` text
FIELD REALITY
   ↓
EVIDENCE
   ↓
CLAIM
   ↓
PROJECT CONTEXT
   ↓
STATE-AWARE MATCH
   ↓
DETERMINISTIC VERIFICATION
   ↓
QUALITY / CONTRACTOR / DEPENDENCY CONTEXT
   ↓
HUMAN DECISION
   ↓
APPROVED ACTUAL
   ↓
PROJECT PROGRESS
   ↓
IMPACT INTELLIGENCE
   ↓
INSTITUTIONAL MEMORY
   ↓
SUPERVISING INTELLIGENCE
   ↓
AUDITABLE PROJECT RECORD
```

The central product promise remains:

> **AI reads the mess. Rules check it. A human approves it. Everything
> is logged.**

V7 extends that promise from an activity-level reconciliation prototype
into a project-level execution intelligence platform without turning
SETUAI into an autonomous scheduler or autonomous approval system.
