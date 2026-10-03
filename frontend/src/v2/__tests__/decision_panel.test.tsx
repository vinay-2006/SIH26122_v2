import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { DecisionPanel } from '@/v2/claims/DecisionPanel';
import { V2Error } from '@/v2/api/http';
import type { CatalogActivity, ClaimDetail } from '@/v2/api/types';

const m = vi.hoisted(() => ({ preview: vi.fn(), decide: vi.fn(), ask: vi.fn() }));
vi.mock('@/v2/api/endpoints', () => ({ claimsApi: { preview: m.preview, decide: m.decide, ask: m.ask, rematch: vi.fn(), bind: vi.fn() }, catalogApi: { activities: vi.fn() }, documentsApi: {} }));

const activity: CatalogActivity = {
  activity_uid: 'act-1', external_activity_id: 'A2010', activity_name: 'Welding', wbs_path: 'X', discipline_code: 'PIPING', activity_type: 'TASK', baseline_start: '2026-01-01', baseline_finish: '2026-02-01', baseline_duration: 10,
  physical_pct: 0, execution_state: 'NOT_STARTED', actual_start: null, actual_finish: null, any_overrun: false, progress_basis: 'QUANTITY', claim_types: ['QUANTITY'],
  measured_assignments: [{ assignment_uid: 'asg-1', resource_code: 'WELD_JOINTS', resource_name: 'Weld joints', unit_of_measure: 'JOINT', baseline_qty: 2000, approved_cumulative_qty: null }],
};
const base: ClaimDetail = {
  event_id: 'c1', event_date: '2026-09-30', status: 'MATCHED', matched_activity_uid: 'act-1', claimed_pct: null, clarification_status: null, created_at: '2026-09-30T00:00:00Z', raw_claim_text: 'welded',
  claimed_start: null, claimed_finish: null, location: null, input_channel: 'TYPED', document_id: null, clarification_answer: null, withdrawn_reason: null, field_provenance: null,
  quantities: [{ claim_quantity_id: 'q1', assignment_uid: 'asg-1', resource_code: 'WELD_JOINTS', reported_qty: 500, reported_uom: 'joints', qty_basis: 'CUMULATIVE', normalized_qty: 500, normalized_uom: 'JOINT' }],
  evidence: [], validations: [], candidates: [], decisions: [],
};
const applied = (cum: number, over = 0) => ({ assignment_uid: 'asg-1', resource: 'WELD_JOINTS', unit: 'JOINT', baseline_qty: '2000', head_cumulative: '0', approved_cumulative: String(cum), incremental: String(cum), source: 'CLAIM', claim_quantity_id: 'q1', overrun_pct: String(over), beyond_tolerance: over > 10 });
const pv = (cum: number, extra: any = {}) => ({ ok: true, action: 'APPROVE', method: 'QUANTITIES_AS_CLAIMED', applied: [applied(cum, extra.over ?? 0)], result: { activity_pct_before: '0', activity_pct_after: String((cum / 2000) * 100), start_inferred: true, short_close_acknowledged: false, overruns: [], tolerance_pct: '10', completion_threshold_pct: '95' }, requires_overrun_acknowledgement: !!extra.ack });
const done = (cum: number) => ({ decision_id: 'd1', claim_id: 'c1', action: 'APPROVE', status: 'APPROVED', method: 'QUANTITIES_AS_CLAIMED', applied: [applied(cum)], result: { activity_pct_before: '0', activity_pct_after: String((cum / 2000) * 100) } });
const mount = (claim = base, act: CatalogActivity | null = activity) => render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><DecisionPanel projectId="p1" claim={claim} activity={act} onDone={() => {}} /></QueryClientProvider>);

beforeEach(() => { vi.clearAllMocks(); m.preview.mockResolvedValue(pv(500)); });

describe('supervisor decision panel', () => {
  it('previews the effect (nothing is saved) and approves only after an explicit confirmation', async () => {
    m.decide.mockResolvedValue(done(500));
    mount();
    expect(await screen.findByTestId('pct-after')).toHaveTextContent('25%');
    expect(m.preview).toHaveBeenCalledWith('p1', 'c1', expect.objectContaining({ action: 'APPROVE' }));
    expect(m.decide).not.toHaveBeenCalled();
    await userEvent.click(screen.getByTestId('decide-btn'));
    expect(m.decide).not.toHaveBeenCalled();                                   // the confirmation dialog comes first
    await userEvent.click(await screen.findByTestId('confirm-decide'));
    await waitFor(() => expect(m.decide).toHaveBeenCalledTimes(1));
    expect(m.decide.mock.calls[0][2]).toEqual({ action: 'APPROVE' });          // as reported: no quantities are sent, the server uses what was filed
    expect(await screen.findByTestId('decision-done')).toHaveTextContent(/Decision recorded.*25%/);
  });

  it('shows reported next to approved in the preview', async () => {
    mount();
    const t = await screen.findByTestId('applied-table');
    expect(t).toHaveTextContent('500 joints'); expect(t).toHaveTextContent('500 JOINT'); expect(t).toHaveTextContent('2,000');
  });

  it('a percentage-only claim on a measured activity cannot be approved until the method is chosen explicitly', async () => {
    m.preview.mockResolvedValue({ ...pv(800), method: 'APPLY_PCT_TO_ASSIGNMENTS', applied: [{ ...applied(800), source: 'PCT' }] });
    m.decide.mockResolvedValue({ ...done(800), method: 'APPLY_PCT_TO_ASSIGNMENTS' });
    mount({ ...base, claimed_pct: 40, quantities: [] });
    expect(screen.getByTestId('pct-method')).toHaveTextContent(/Nothing is converted automatically/);
    expect(screen.getByTestId('decide-btn')).toBeDisabled();
    expect(m.preview).not.toHaveBeenCalled();                                   // there is nothing to preview before a method is chosen
    await userEvent.click(screen.getByRole('radio'));
    expect(await screen.findByTestId('applied-table')).toHaveTextContent('PCT');   // the resulting quantities are shown before anything is approved
    expect(m.preview.mock.calls[0][2]).toMatchObject({ action: 'APPROVE', method: 'APPLY_PCT_TO_ASSIGNMENTS' });
    await userEvent.click(screen.getByTestId('decide-btn')); await userEvent.click(await screen.findByTestId('confirm-decide'));
    await waitFor(() => expect(m.decide).toHaveBeenCalled());
    expect(m.decide.mock.calls[0][2]).toMatchObject({ action: 'APPROVE', method: 'APPLY_PCT_TO_ASSIGNMENTS' });
  });

  it('beyond the tolerance an acknowledgement is required; it is sent with the decision; quantities are not capped', async () => {
    m.preview.mockResolvedValue(pv(2600, { over: 30, ack: true }));
    m.decide.mockResolvedValue(done(2600));
    mount({ ...base, quantities: [{ ...base.quantities[0], reported_qty: 2600, normalized_qty: 2600 }] });
    const box = await screen.findByLabelText('Over-baseline acknowledgement');
    expect(screen.getByTestId('overrun-ack')).toHaveTextContent(/tolerance \(10%\)/);
    expect(screen.getByTestId('applied-table')).toHaveTextContent('2,600');         // shown as approved, not clamped to the 2,000 baseline
    expect(screen.getByTestId('decide-btn')).toBeDisabled();
    await userEvent.type(box, 'Re-welded joints counted twice');
    await waitFor(() => expect(screen.getByTestId('decide-btn')).toBeEnabled());
    await userEvent.click(screen.getByTestId('decide-btn')); await userEvent.click(await screen.findByTestId('confirm-decide'));
    await waitFor(() => expect(m.decide).toHaveBeenCalled());
    expect(m.decide.mock.calls[0][2]).toMatchObject({ action: 'APPROVE', overrun_ack_note: 'Re-welded joints counted twice' });
  });

  it('a failed decision shows the reason, records nothing and leaves the panel usable', async () => {
    m.decide.mockRejectedValue(new V2Error(409, 'CLAIM_NOT_DECIDABLE', 'This claim can no longer be decided.'));
    mount();
    await screen.findByTestId('applied-table');
    await userEvent.click(screen.getByTestId('decide-btn')); await userEvent.click(await screen.findByTestId('confirm-decide'));
    expect(await screen.findByTestId('decision-error')).toHaveTextContent('This claim can no longer be decided.');
    expect(screen.queryByTestId('decision-done')).toBeNull();                         // no optimistic success
    expect(screen.getByTestId('decide-btn')).toBeEnabled();
  });

  it('an edit sends the entered quantities as MANUAL_QUANTITIES and needs a justification', async () => {
    m.preview.mockResolvedValue({ ...pv(450), action: 'EDIT', method: 'MANUAL_QUANTITIES', applied: [{ ...applied(450), source: 'MANUAL' }] });
    m.decide.mockResolvedValue({ ...done(450), action: 'EDIT' });
    mount();
    await userEvent.click(screen.getByRole('button', { name: 'Approve with changes' }));
    const q = await screen.findByLabelText('Approved quantity of Weld joints');
    expect(q).toHaveValue(500);                                                  // prefilled with what was reported
    await userEvent.clear(q); await userEvent.type(q, '450');
    await screen.findByTestId('applied-table');
    expect(screen.getByTestId('decide-btn')).toBeDisabled();                     // no justification yet
    await userEvent.type(screen.getByLabelText('Justification'), 'register shows 450');
    await waitFor(() => expect(screen.getByTestId('decide-btn')).toBeEnabled());
    await userEvent.click(screen.getByTestId('decide-btn')); await userEvent.click(await screen.findByTestId('confirm-decide'));
    await waitFor(() => expect(m.decide).toHaveBeenCalled());
    expect(m.decide.mock.calls[0][2]).toMatchObject({ action: 'EDIT', method: 'MANUAL_QUANTITIES', approved_quantities: { 'asg-1': { cumulative: '450' } }, justification: 'register shows 450' });
  });

  it('rejection needs a reason; a question goes through the clarification endpoint', async () => {
    m.decide.mockResolvedValue({ ...done(0), action: 'REJECT', status: 'REJECTED', applied: [] });
    m.ask.mockResolvedValue({ ...done(0), action: 'HOLD', status: 'DISPUTED', applied: [] });
    const { unmount } = mount();
    await userEvent.click(screen.getByRole('button', { name: 'Reject' }));
    expect(screen.getByTestId('decide-btn')).toBeDisabled();
    await userEvent.type(screen.getByLabelText('Rejection reason'), 'does not match the register');
    await userEvent.click(screen.getByTestId('decide-btn'));
    await waitFor(() => expect(m.decide).toHaveBeenCalledWith('p1', 'c1', { action: 'REJECT', justification: 'does not match the register' }));
    unmount();
    mount();
    await userEvent.click(screen.getByRole('button', { name: 'Ask a question' }));
    await userEvent.type(screen.getByLabelText('Clarification question'), 'which section?');
    await userEvent.click(screen.getByTestId('decide-btn'));
    await waitFor(() => expect(m.ask).toHaveBeenCalledWith('p1', 'c1', 'which section?'));
  });

  it('an already-decided claim offers no decision at all', () => {
    mount({ ...base, status: 'APPROVED' });
    expect(screen.getByTestId('not-decidable')).toBeInTheDocument(); expect(screen.queryByTestId('decide-btn')).toBeNull();
  });

  it('a preview refusal (e.g. below the completion threshold) is shown and offers the short-close note', async () => {
    m.preview.mockResolvedValue({ ok: false, error: { code: 'FINISH_BELOW_THRESHOLD', message: 'Finishing at 25% is below the project’s completion threshold (95%): add a short-close note to proceed' } });
    mount({ ...base, claimed_finish: '2026-09-30' });
    expect(await screen.findByTestId('preview-error')).toHaveTextContent('FINISH_BELOW_THRESHOLD');
    expect(screen.getByTestId('short-close')).toBeInTheDocument(); expect(screen.getByTestId('decide-btn')).toBeDisabled();
  });
});
