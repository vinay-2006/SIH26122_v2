import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ClaimForm } from '@/v2/claims/ClaimForm';
import { V2Error } from '@/v2/api/http';
import type { CatalogActivity } from '@/v2/api/types';

const m = vi.hoisted(() => ({ submit: vi.fn(), correction: vi.fn(), activities: vi.fn(), upload: vi.fn() }));
vi.mock('@/v2/api/endpoints', () => ({ claimsApi: { submit: m.submit, correction: m.correction }, catalogApi: { activities: m.activities }, documentsApi: { upload: m.upload } }));

const act: CatalogActivity = {
  activity_uid: 'act-1', external_activity_id: 'OSD-2320', activity_name: 'W3 - drill 26 in hole section', wbs_path: 'W3', discipline_code: 'DRILLING', activity_type: 'TASK', baseline_start: '2026-01-01', baseline_finish: '2026-02-01',
  baseline_duration: 10, physical_pct: 0, execution_state: 'NOT_STARTED', actual_start: null, actual_finish: null, any_overrun: false, progress_basis: 'QUANTITY', claim_types: ['QUANTITY', 'PERCENT'],
  measured_assignments: [
    { assignment_uid: 'a-m', resource_code: 'W3_DRILLED_26_M', resource_name: 'W3: 26 in section drilled', unit_of_measure: 'M', baseline_qty: 446.4, approved_cumulative_qty: null },
    { assignment_uid: 'a-v', resource_code: 'W3_MUD_26_M3', resource_name: 'W3: mud volume used', unit_of_measure: 'M3', baseline_qty: 401.76, approved_cumulative_qty: 10 },
  ],
};
const ok = { claim_id: 'c1', status: 'MATCHED', activity_uid: 'act-1', quantities: [], validations: [{ rule: 'PERCENT_ONLY_NEEDS_METHOD', severity: 'INFO', message: 'needs a method' }], priority_score: 0 };
const renderForm = (props: Partial<React.ComponentProps<typeof ClaimForm>> = {}) => render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ClaimForm projectId="p1" initialActivity={act} {...props} /></QueryClientProvider>);
const qty = (name: string) => screen.getByLabelText(`Quantity of ${name}`);

beforeEach(() => { vi.clearAllMocks(); m.activities.mockResolvedValue({ items: [act], next_offset: null, limit: 30, offset: 0, version: {} }); });

describe('claim form', () => {
  it('offers one row per measured resource, in the schedule’s own unit, and blocks an empty claim', () => {
    renderForm();
    expect(screen.getByText(/Baseline 446\.4 M/)).toBeInTheDocument();
    expect(screen.getByText(/Baseline 401\.76 M3/)).toBeInTheDocument();
    expect(screen.getByText(/never converted or capped/i)).toBeInTheDocument();
    expect(screen.getByTestId('submit-claim')).toBeDisabled();
  });

  it('submits exactly what was typed: unit codes from the schedule, basis, resource hint, no conversion', async () => {
    m.submit.mockResolvedValue(ok);
    renderForm();
    await userEvent.type(qty('W3: 26 in section drilled'), '200');
    await userEvent.selectOptions(screen.getByLabelText('Basis for W3: 26 in section drilled'), 'INCREMENTAL');
    await userEvent.type(screen.getByLabelText('Remarks'), 'Drilled 200 m this shift');
    await userEvent.click(screen.getByTestId('submit-claim'));
    await waitFor(() => expect(m.submit).toHaveBeenCalledTimes(1));
    const [pid, body, key] = m.submit.mock.calls[0];
    expect(pid).toBe('p1');
    expect(body).toMatchObject({ activity_uid: 'act-1', raw_text: 'Drilled 200 m this shift', quantities: [{ qty: '200', uom: 'M', basis: 'INCREMENTAL', resource_hint: 'W3_DRILLED_26_M' }] });
    expect(body.quantities).toHaveLength(1);                      // the resource left blank is not sent as zero
    expect(body.claimed_pct).toBeUndefined();
    expect(key).toMatch(/^claim-/);
    expect(await screen.findByTestId('claim-success')).toHaveTextContent(/does not change project progress/);
    expect(screen.getByTestId('claim-validations')).toHaveTextContent('PERCENT_ONLY_NEEDS_METHOD');
  });

  it('percent-only is stored as a percentage, with a clear note that it is not converted', async () => {
    m.submit.mockResolvedValue(ok);
    renderForm();
    await userEvent.click(screen.getByRole('button', { name: 'Percent only' }));
    expect(screen.getByTestId('pct-note')).toHaveTextContent(/not converted/i);
    await userEvent.type(screen.getByLabelText('Percent complete'), '40');
    await userEvent.type(screen.getByLabelText('Remarks'), 'about 40 percent');
    await userEvent.click(screen.getByTestId('submit-claim'));
    await waitFor(() => expect(m.submit).toHaveBeenCalled());
    const body = m.submit.mock.calls[0][1];
    expect(body.claimed_pct).toBe('40'); expect(body.quantities).toBeUndefined();
  });

  it('refuses future dates, impossible percentages and negative quantities before sending anything', async () => {
    renderForm();
    await userEvent.type(qty('W3: 26 in section drilled'), '-5');
    await userEvent.type(screen.getByLabelText('Remarks'), 'x y z');
    expect(screen.getByTestId('submit-claim')).toBeDisabled();
    expect(screen.getByText('Quantities must be zero or more.')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Report date'), { target: { value: '2999-01-01' } });
    expect(screen.getByText('A claim cannot be dated in the future.')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Percent only' }));
    await userEvent.type(screen.getByLabelText('Percent complete'), '120');
    expect(screen.getByText('Enter a percentage between 0 and 100.')).toBeInTheDocument();
    expect(m.submit).not.toHaveBeenCalled();
  });

  it('shows the server’s reason when a claim is refused and keeps what was typed', async () => {
    m.submit.mockRejectedValue(new V2Error(422, 'FUTURE_DATE', 'A claim cannot be dated in the future'));
    renderForm();
    await userEvent.type(qty('W3: mud volume used'), '5'); await userEvent.type(screen.getByLabelText('Remarks'), 'mud used');
    await userEvent.click(screen.getByTestId('submit-claim'));
    expect(await screen.findByTestId('claim-error')).toHaveTextContent('A claim cannot be dated in the future');
    expect(qty('W3: mud volume used')).toHaveValue(5);
    expect(screen.queryByTestId('claim-success')).toBeNull();
  });

  it('retrying the SAME submission reuses its idempotency key; changing it, or a success, makes a new key', async () => {
    m.submit.mockRejectedValueOnce(new V2Error(0, 'NETWORK_ERROR', 'down')).mockRejectedValueOnce(new V2Error(0, 'NETWORK_ERROR', 'down')).mockResolvedValue(ok);
    renderForm();
    await userEvent.type(qty('W3: mud volume used'), '5'); await userEvent.type(screen.getByLabelText('Remarks'), 'mud used');
    await userEvent.click(screen.getByTestId('submit-claim')); await screen.findByTestId('claim-error');
    await userEvent.click(screen.getByTestId('submit-claim')); await waitFor(() => expect(m.submit).toHaveBeenCalledTimes(2));
    expect(m.submit.mock.calls[1][2]).toBe(m.submit.mock.calls[0][2]);                         // same content -> same key: a retry can never double-file
    await userEvent.type(qty('W3: mud volume used'), '0');                                     // 50 now: different content
    await userEvent.click(screen.getByTestId('submit-claim')); await waitFor(() => expect(m.submit).toHaveBeenCalledTimes(3));
    expect(m.submit.mock.calls[2][2]).not.toBe(m.submit.mock.calls[0][2]);
    await screen.findByTestId('claim-success');
    await userEvent.type(qty('W3: mud volume used'), '50'); await userEvent.type(screen.getByLabelText('Remarks'), ' again');
    await userEvent.click(screen.getByTestId('submit-claim')); await waitFor(() => expect(m.submit).toHaveBeenCalledTimes(4));
    expect(m.submit.mock.calls[3][2]).not.toBe(m.submit.mock.calls[2][2]);
  });

  it('a correction is filed against the rejected claim and says it is a new, linked claim', async () => {
    m.correction.mockResolvedValue(ok);
    renderForm({ correctionOf: 'rej-1' });
    expect(screen.getByText(/filed as a new claim linked to the rejected one/i)).toBeInTheDocument();
    await userEvent.type(qty('W3: mud volume used'), '7'); await userEvent.type(screen.getByLabelText('Remarks'), 'corrected');
    await userEvent.click(screen.getByTestId('submit-claim'));
    await waitFor(() => expect(m.correction).toHaveBeenCalled());
    expect(m.correction.mock.calls[0][1]).toBe('rej-1'); expect(m.correction.mock.calls[0][3]).toMatch(/^fix-/); expect(m.submit).not.toHaveBeenCalled();
  });

  it('does not allow submitting while evidence is still uploading', async () => {
    let finish: (v: any) => void = () => {};
    m.upload.mockImplementation(() => new Promise((r) => { finish = r; }));
    renderForm();
    await userEvent.type(qty('W3: mud volume used'), '5'); await userEvent.type(screen.getByLabelText('Remarks'), 'mud used');
    await userEvent.upload(screen.getByTestId('evidence-input'), new File(['x'], 'log.txt', { type: 'text/plain' }));
    await waitFor(() => expect(screen.getByTestId('submit-claim')).toBeDisabled());
    finish({ document_id: 'd1', file_name: 'log.txt', kind: 'EVIDENCE' });
    await waitFor(() => expect(screen.getByTestId('submit-claim')).toBeEnabled());
    expect(within(screen.getByTestId('evidence-row')).getByText('uploaded')).toBeInTheDocument();
  });
});
