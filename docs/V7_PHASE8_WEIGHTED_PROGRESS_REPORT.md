# SETUAI V7 — PHASE 8 COMPLETION REPORT
## Weighted Progress & Canonical Rollup Engine

**Module / Phase:** Member 1 — Phase 8: Weighted Progress & Rollups  
**Repository:** SETUAI V7 / SIH26122_v2  
**Timestamp:** 2026-09-28T23:41:00+05:30  
**Status:** **PHASE 8 COMPLETE — VERIFIED & FROZEN**

---

### 1. AUDIT FINDINGS

Prior to implementation, a thorough audit was performed across `backend/routers`, `backend/services`, `backend/repositories`, `backend/schemas`, and `backend/models` regarding weighting, actuals, rollups, and revisions:

1. **`schedule_activities.weight_factor`**:
   - Represents relative activity weight within its enclosing stage or schedule version.
   - Numeric / float, defaulting to `1.0`.
2. **`stages.weight_pct`**:
   - Represents the percentage weight (0.0 to 100.0) of a stage towards total schedule completion.
   - Distinct and complementary to `weight_factor`; there is no namespace or semantic collision.
3. **Quantity-Based Progress**:
   - `schedule_activities.planned_quantity` and `approved_actuals.actual_quantity` exist.
   - When explicit `actual_pct_complete` is absent, quantity progress is derived via `(actual_quantity / planned_quantity) * 100.0`.
4. **Historical Revision Double-Counting Risk**:
   - `approved_actuals` has a unique constraint `UNIQUE (schedule_id, activity_id)`.
   - Historical snapshots are immutably archived into `audit_logs` during Phase 7 rework revisions.
   - Querying `approved_actuals` directly guarantees authoritative state without double-counting past revisions.

---

### 2. EXISTING WEIGHTING MODEL

- **Activity Weight:** `weight_factor` (default `1.0`). If negative, clamped to `0.0`. If `NULL`, safely defaults to `1.0`.
- **Stage Weight:** `weight_pct` (0.0 to 100.0%). If stages have non-zero `weight_pct`, the schedule rollup operates on a stage-weighted basis.
- **Zero Weight Behavior:** If $\sum \text{weights} == 0$, the calculation safely returns `0.0%` with zero division or `NaN` errors.

---

### 3. EXISTING PROGRESS MODEL & DECOUPLING

- **Numerical Progress $\neq$ Lifecycle State:**
  - Progress percentage reflects raw or weighted completion of work.
  - Stage state remains strictly governed by Phase 5 rules (gating predecessor completion, quality gates, mandatory activity completion). High progress (e.g. 95%) does not prematurely set `StageState.COMPLETED`.

---

### 4. CANONICAL FORMULAS

#### A. Activity Progress
$$\text{progress\_pct} = 
\begin{cases}
\text{clamp}(\text{actual\_pct\_complete}, 0, 100) & \text{if } \text{actual\_pct\_complete} \text{ IS NOT NULL} \\
\text{clamp}\left(\frac{\text{actual\_quantity}}{\text{planned\_quantity}} \times 100, 0, 100\right) & \text{if quantity available and } \text{planned\_quantity} > 0 \\
100.0 & \text{if canonical state is } \text{COMPLETED} \\
0.0 & \text{otherwise}
\end{cases}$$

#### B. Stage Progress
$$\text{Stage Progress} = \frac{\sum_{i=1}^{n} (w_i \times p_i)}{\sum_{i=1}^{n} w_i} \quad \text{where } w_i = \text{activity.weight\_factor}, \, p_i = \text{activity.progress\_pct}$$
$$\text{Activity Contribution to Stage} = \frac{w_i \times p_i}{\sum_{j=1}^{n} w_j}$$

#### C. Schedule Progress
- **Stage-Weighted Mode (Preferred when stages have assigned weights):**
  $$\text{Schedule Progress} = \frac{\sum_{k=1}^{m} (\text{stage}_k.\text{weight\_pct} \times \text{stage}_k.\text{progress\_pct})}{\sum_{k=1}^{m} \text{stage}_k.\text{weight\_pct}}$$
- **Activity-Weighted Mode (Fallback when stages are unweighted or absent):**
  $$\text{Schedule Progress} = \frac{\sum_{i=1}^{N} (w_i \times p_i)}{\sum_{i=1}^{N} w_i}$$

#### D. Project Progress
- Progress is evaluated per specific schedule version (`target_schedule_id`) or across active versions.
- **Version Isolation Invariant:** Schedule V1 and Schedule V2 are calculated independently and are **NEVER** merged, summed, or averaged together.

---

### 5. REOPEN / REWORK INTERACTION & HISTORICAL ACTUALS

- When an activity is challenged, approved for rework, and subsequently re-evaluated with a corrected actual (e.g. from 100% down to 60%):
  - `approved_actuals` holds the single authoritative current state (`60.0%`).
  - Prior approved states exist solely in `audit_logs` as historical audit records.
  - The progress engine calculates strictly against `approved_actuals.actual_pct_complete`, producing `60.0%`, **never `160.0%` and never stale `100.0%`**.

---

### 6. NULL AND ZERO-WEIGHT EDGE CASES

- `actual_pct_complete = NULL`: Evaluates quantity derivation, execution state completion, or safely defaults to `0.0%`.
- `weight_factor = NULL`: Safely defaults to `1.0`.
- $\sum \text{weights} == 0$: Progress returns `0.0%`. Division-by-zero, `NaN`, or `Infinity` is strictly prohibited.

---

### 7. PERFORMANCE & ZERO N+1 QUERIES

- In `ProjectProgressRepository`:
  - `get_schedule_activities_with_actuals`: Executes a single bulk `LEFT JOIN` between `schedule_activities` and `approved_actuals` with project and schedule filters.
  - `get_schedule_stages`: Loads all schedule stages in a single query.
  - Rollup trees and explainable contribution breakdowns are computed in-memory in $O(N)$ time.
  - Zero per-activity SQL roundtrips.

---

### 8. API CONTRACTS IMPLEMENTED

| Method | Endpoint | Response Model | Description |
|---|---|---|---|
| `GET` | `/api/v1/projects/{project_id}/schedules/{schedule_id}/progress` | `ScheduleProgressResponse` | Authoritative schedule progress rollup and stage breakdown |
| `GET` | `/api/v1/projects/{project_id}/schedules/{schedule_id}/stages/{stage_id}/progress` | `StageProgressBreakdown` | Stage progress with explainable activity contributions |
| `GET` | `/api/v1/projects/{project_id}/schedules/{schedule_id}/activities/{activity_id}/progress` | `ActivityProgressResponse` | Authoritative activity progress and calculation basis |
| `GET` | `/api/v1/projects/{project_id}/progress` | `ProjectProgressResponse` | Multi-schedule project rollup maintaining version isolation |
| `GET` | `/api/v1/projects/{project_id}/schedules/{schedule_id}/progress/breakdown` | `ProgressBreakdownResponse` | Full hierarchical tree: Schedule -> Stage -> Activity |

---

### 9. VERIFICATION & TEST SUITE

The dedicated Phase 8 test suite (`tests/test_phase8_progress_engine.py`) covers all 16 mandatory test scenarios:
1. `test_activity_progress_percentages`: 0%, 25%, 50%, 100% canonical percentage completion.
2. `test_quantity_derived_and_null_progress`: `NULL` actual_pct_complete fallback to `actual_quantity / planned_quantity`.
3. `test_weighted_stage_rollups_and_contributions`: Weight factor ratios (1.0 vs 3.0) and explainable percentage contributions.
4. `test_schedule_stage_weighted_rollup`: Stage percentage weighting (60% / 40%) into overall schedule progress.
5. `test_reopened_activity_uses_current_revision_no_double_count`: Reopened revision (60%) correctly used; historical 100% not double-counted (resulting in 60%, not 160%).
6. `test_null_and_zero_weight_behavior`: Zero-weight stage returns 0.0% without division-by-zero or `NaN`.
7. `test_schedule_version_and_project_isolation`: Independent V1 vs V2 calculation; 403 Forbidden for cross-project access.
8. `test_stage_lifecycle_decoupled_from_progress`: High numerical progress does not override Phase 5 stage lifecycle state.
9. `test_hierarchical_progress_breakdown_endpoint`: Verification of the full RESTful hierarchical tree breakdown.

**Suite Results:**
- `tests/test_phase8_progress_engine.py`: **9/9 PASSED (100%)**
- Combined Regression Suite (Phases 5, 6, 7, 8): **55/55 PASSED (100%)**
- Full Schema Verification: **ALL 30 TABLES, 56 FKS, 97 INDEXES, 54 POLICIES VERIFIED GREEN**.
- Schema Migrations: **NO SCHEMA MIGRATION REQUIRED**.

---

### 10. DOWNSTREAM CONTRACTS FOR PHASE 10

Phase 10 (Compound Impact Intelligence) can safely consume:
- `ProgressService.get_activity_progress(context, activity_id)`
- `ProgressService.get_stage_progress(context, stage_id)`
- `ProgressService.get_schedule_progress(context)`
- `ProgressService.get_project_progress(context, target_schedule_id)`
- `ProgressService.get_progress_breakdown(context)`
