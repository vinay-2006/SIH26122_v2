import React, { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Archive, Loader2, Mail, Save, Settings2, UserPlus, Users } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { useProjectState } from '@/context/ProjectContext';
import { useAuth } from '@/auth/AuthProvider';
import { useV2Project } from '@/v2/ProjectProviderV2';
import { projectsApi } from '@/v2/api/endpoints';
import type { Lifecycle, Member } from '@/v2/api/types';
import { ROLE_LABEL } from '@/v2/permissions';
import { FIELD, Label, Loading, Notice, PageHeader, Panel, QueryError, day, errText, F } from '@/v2/ui';

function DetailsTab() {
  const { projectId, detail, isArchived } = useV2Project();
  const qc = useQueryClient();
  const [f, setF] = useState({ project_name: '', description: '', client_name: '', project_type: '', location: '', planned_start: '', planned_finish: '', contract_finish: '', lifecycle_status: 'UPCOMING' as Lifecycle });
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => { setF({ project_name: detail.project_name, description: detail.description ?? '', client_name: detail.client_name ?? '', project_type: detail.project_type ?? '', location: detail.location ?? '',
    planned_start: detail.planned_start ?? '', planned_finish: detail.planned_finish ?? '', contract_finish: detail.contract_finish ?? '', lifecycle_status: detail.lifecycle_status }); }, [detail]);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<any>) => setF((p) => ({ ...p, [k]: e.target.value }));
  const save = useMutation({
    mutationFn: () => { const body: Record<string, string> = {}; (Object.keys(f) as (keyof typeof f)[]).forEach((k) => { if (String(f[k]).trim() !== '' && f[k] !== ((detail as any)[k] ?? '')) body[k] = String(f[k]).trim(); }); return projectsApi.patch(projectId, body); },
    onSuccess: () => { setMsg({ ok: true, text: 'Saved.' }); qc.invalidateQueries({ queryKey: ['v2'] }); }, onError: (e) => setMsg({ ok: false, text: errText(e) }),
  });
  const arch = useMutation({
    mutationFn: () => (isArchived ? projectsApi.restore(projectId) : projectsApi.archive(projectId)),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['v2'] }), onError: (e) => setMsg({ ok: false, text: errText(e) }),
  });
  return (
    <div className="space-y-4">
      <Panel icon={Settings2} title="Project details" description={`Code ${detail.project_code} (fixed). Dates are planned dates; the schedule baseline has its own data date.`}>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div><Label>Name</Label><Input aria-label="Project name" value={f.project_name} onChange={set('project_name')} disabled={isArchived} /></div>
          <div><Label>Status</Label><select className={FIELD} aria-label="Lifecycle status" value={f.lifecycle_status} onChange={set('lifecycle_status')} disabled={isArchived}><option value="UPCOMING">Upcoming</option><option value="ONGOING">Ongoing</option><option value="COMPLETED">Completed</option></select></div>
          <div><Label>Client</Label><Input aria-label="Client" value={f.client_name} onChange={set('client_name')} disabled={isArchived} /></div>
          <div><Label>Project type</Label><Input aria-label="Project type" value={f.project_type} onChange={set('project_type')} disabled={isArchived} /></div>
          <div><Label>Location</Label><Input aria-label="Location" value={f.location} onChange={set('location')} disabled={isArchived} /></div>
          <div><Label>Planned start</Label><Input type="date" aria-label="Planned start" value={f.planned_start} onChange={set('planned_start')} disabled={isArchived} /></div>
          <div><Label>Planned finish</Label><Input type="date" aria-label="Planned finish" value={f.planned_finish} onChange={set('planned_finish')} disabled={isArchived} /></div>
          <div><Label>Contract finish</Label><Input type="date" aria-label="Contract finish" value={f.contract_finish} onChange={set('contract_finish')} disabled={isArchived} /></div>
          <div className="sm:col-span-2"><Label>Description</Label><Textarea rows={2} aria-label="Description" value={f.description} onChange={set('description')} disabled={isArchived} /></div>
        </div>
        {msg && <Notice tone={msg.ok ? 'ok' : 'bad'}>{msg.text}</Notice>}
        <div className="flex gap-2">
          <Button onClick={() => { setMsg(null); save.mutate(); }} disabled={save.isPending || isArchived} className="gap-1.5 cursor-pointer" data-testid="save-details">{save.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />} Save changes</Button>
          <Button variant="outline" className="gap-1.5 cursor-pointer" disabled={arch.isPending} data-testid="archive-toggle"
            onClick={() => { if (isArchived || window.confirm('Archive this project? It becomes read-only for everyone (claims, decisions and uploads stop) until restored.')) arch.mutate(); }}><Archive className="w-4 h-4" />{isArchived ? 'Restore project' : 'Archive project'}</Button>
        </div>
      </Panel>
    </div>
  );
}

function SettingsTab() {
  const { projectId, isArchived } = useV2Project();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['v2', 'settings', projectId], queryFn: () => projectsApi.settings(projectId), retry: false });
  const [f, setF] = useState({ over: '', complete: '', days: '', photo: false });
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => { if (q.data) setF({ over: String(Number(q.data.over_baseline_tolerance_pct)), complete: String(Number(q.data.completion_threshold_pct)), days: String(q.data.working_days_per_week), photo: q.data.require_photo_evidence }); }, [q.data]);
  const save = useMutation({
    mutationFn: () => projectsApi.patchSettings(projectId, { over_baseline_tolerance_pct: Number(f.over), completion_threshold_pct: Number(f.complete), working_days_per_week: Number(f.days), require_photo_evidence: f.photo }),
    onSuccess: () => { setMsg({ ok: true, text: 'Settings saved.' }); qc.invalidateQueries({ queryKey: ['v2', 'settings'] }); }, onError: (e) => setMsg({ ok: false, text: errText(e) }),
  });
  if (q.isPending) return <Loading />;
  if (q.error) return <QueryError error={q.error} onRetry={() => q.refetch()} />;
  const bad = [f.over, f.complete].some((v) => v === '' || Number(v) < 0 || Number(v) > 100) || !(Number(f.days) >= 1 && Number(f.days) <= 7);
  return (
    <Panel icon={Settings2} title="Progress rules" description="Applied by the server to every claim decision on this project.">
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div><Label hint="(%)">Over-baseline tolerance</Label><Input type="number" min={0} max={100} step="0.5" aria-label="Over-baseline tolerance" value={f.over} onChange={(e) => setF({ ...f, over: e.target.value })} disabled={isArchived} />
          <div className="text-[10px] text-muted-foreground mt-0.5">Beyond this, a Supervisor must acknowledge the overrun with a note. Quantities are never capped.</div></div>
        <div><Label hint="(%)">Completion threshold</Label><Input type="number" min={0} max={100} step="0.5" aria-label="Completion threshold" value={f.complete} onChange={(e) => setF({ ...f, complete: e.target.value })} disabled={isArchived} />
          <div className="text-[10px] text-muted-foreground mt-0.5">Finishing below this needs a stated short-close note.</div></div>
        <div><Label>Working days per week</Label><Input type="number" min={1} max={7} aria-label="Working days per week" value={f.days} onChange={(e) => setF({ ...f, days: e.target.value })} disabled={isArchived} /></div>
      </div>
      <label className="flex items-center gap-2 text-xs cursor-pointer"><input type="checkbox" className="accent-[#FF7A18]" checked={f.photo} onChange={(e) => setF({ ...f, photo: e.target.checked })} disabled={isArchived} /> Require photo evidence with claims</label>
      {msg && <Notice tone={msg.ok ? 'ok' : 'bad'}>{msg.text}</Notice>}
      <Button onClick={() => { setMsg(null); save.mutate(); }} disabled={bad || save.isPending || isArchived} className="gap-1.5 cursor-pointer" data-testid="save-settings">{save.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />} Save settings</Button>
    </Panel>
  );
}

function MembersTab() {
  const { projectId, isArchived } = useV2Project();
  const { user } = useAuth();
  const qc = useQueryClient();
  const members = useQuery({ queryKey: ['v2', 'members', projectId], queryFn: () => projectsApi.members(projectId), retry: false });
  const invites = useQuery({ queryKey: ['v2', 'invitations', projectId], queryFn: () => projectsApi.invitations(projectId), retry: false });
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<'SUPERVISOR' | 'SITE_ENGINEER'>('SITE_ENGINEER');
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [link, setLink] = useState<string | null>(null);
  const done = (text?: string) => { if (text) setMsg({ ok: true, text }); qc.invalidateQueries({ queryKey: ['v2', 'members'] }); qc.invalidateQueries({ queryKey: ['v2', 'invitations'] }); };
  const fail = (e: unknown) => setMsg({ ok: false, text: errText(e) });
  const add = useMutation({ mutationFn: () => projectsApi.addMember(projectId, email.trim(), role), onSuccess: (m) => { setEmail(''); setLink(null); done(`${m.full_name || m.email} added as ${ROLE_LABEL[m.role]}.`); }, onError: fail });
  const invite = useMutation({ mutationFn: () => projectsApi.invite(projectId, email.trim(), role), onSuccess: (i) => { setEmail(''); setLink(`${window.location.origin}${i.accept_path}`); done('Invitation created. Share the link below with the invitee — it is shown only once.'); }, onError: fail });
  const patch = useMutation({ mutationFn: (v: { id: string; body: { role?: 'SUPERVISOR' | 'SITE_ENGINEER'; status?: 'ACTIVE' | 'SUSPENDED' | 'REMOVED' } }) => projectsApi.patchMember(projectId, v.id, v.body), onSuccess: () => done('Member updated.'), onError: fail });
  const revoke = useMutation({ mutationFn: (id: string) => projectsApi.revokeInvitation(projectId, id), onSuccess: () => done('Invitation revoked.'), onError: fail });
  const valid = /^\S+@\S+\.\S+$/.test(email.trim());
  return (
    <div className="space-y-4">
      <Panel icon={UserPlus} title="Add a person" description="Add a registered user directly, or create an invitation link for someone who has not signed in yet. Project Managers are created only by the platform.">
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex-1 min-w-56"><Label>Email</Label><Input type="email" aria-label="Member email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@company.com" disabled={isArchived} /></div>
          <div><Label>Role</Label><select className={F('w-44')} aria-label="Member role" value={role} onChange={(e) => setRole(e.target.value as any)}><option value="SITE_ENGINEER">Site Engineer</option><option value="SUPERVISOR">Supervisor</option></select></div>
          <Button onClick={() => { setMsg(null); add.mutate(); }} disabled={!valid || add.isPending || isArchived} className="gap-1.5 cursor-pointer" data-testid="add-member"><UserPlus className="w-4 h-4" />Add</Button>
          <Button variant="outline" onClick={() => { setMsg(null); invite.mutate(); }} disabled={!valid || invite.isPending || isArchived} className="gap-1.5 cursor-pointer" data-testid="invite-member"><Mail className="w-4 h-4" />Invite by link</Button>
        </div>
        {msg && <Notice tone={msg.ok ? 'ok' : 'bad'}>{msg.text}</Notice>}
        {link && <input readOnly aria-label="Invitation link" value={link} onFocus={(e) => e.currentTarget.select()} className={F('font-mono text-xs')} data-testid="invite-link" />}
      </Panel>
      <Panel icon={Users} title="Members">
        {members.isPending && <Loading />}
        {members.error && <QueryError error={members.error} />}
        {members.data && (
          <div className="rounded-xl border border-border overflow-x-auto bg-card"><Table data-testid="members-table">
            <TableHeader><TableRow><TableHead>Name</TableHead><TableHead>Email</TableHead><TableHead>Role</TableHead><TableHead>Status</TableHead><TableHead className="text-right">Actions</TableHead></TableRow></TableHeader>
            <TableBody>{members.data.map((m: Member) => {
              const self = m.user_id === user?.id, pm = m.role === 'PROJECT_MANAGER';
              return (
                <TableRow key={m.user_id} data-testid="member-row" data-email={m.email}>
                  <TableCell className="font-semibold">{m.full_name}{self && <span className="ml-1 text-[10px] text-muted-foreground">(you)</span>}</TableCell><TableCell className="text-[11px]">{m.email}</TableCell>
                  <TableCell>{pm ? <span className="text-xs font-bold">{ROLE_LABEL[m.role]}</span> : <select className={F('h-7 w-36 text-xs')} aria-label={`Role of ${m.full_name}`} value={m.role} disabled={isArchived || m.status !== 'ACTIVE'} onChange={(e) => patch.mutate({ id: m.user_id, body: { role: e.target.value as any } })}><option value="SITE_ENGINEER">Site Engineer</option><option value="SUPERVISOR">Supervisor</option></select>}</TableCell>
                  <TableCell className="text-[11px]">{m.status}</TableCell>
                  <TableCell className="text-right whitespace-nowrap space-x-1">{!pm && m.status === 'ACTIVE' && <><Button size="sm" variant="outline" className="cursor-pointer" disabled={isArchived} onClick={() => patch.mutate({ id: m.user_id, body: { status: 'SUSPENDED' } })}>Suspend</Button>
                    <Button size="sm" variant="ghost" className="text-rose-600 cursor-pointer" disabled={isArchived} onClick={() => { if (window.confirm(`Remove ${m.full_name} from this project?`)) patch.mutate({ id: m.user_id, body: { status: 'REMOVED' } }); }}>Remove</Button></>}
                    {!pm && m.status === 'SUSPENDED' && <Button size="sm" variant="outline" className="cursor-pointer" disabled={isArchived} onClick={() => patch.mutate({ id: m.user_id, body: { status: 'ACTIVE' } })}>Reactivate</Button>}</TableCell>
                </TableRow>);
            })}</TableBody></Table></div>
        )}
      </Panel>
      <Panel icon={Mail} title="Invitations">
        {invites.data?.length === 0 && <div className="text-xs text-muted-foreground">No invitations.</div>}
        {invites.data && invites.data.length > 0 && (
          <Table><TableHeader><TableRow><TableHead>Email</TableHead><TableHead>Role</TableHead><TableHead>State</TableHead><TableHead>Expires</TableHead><TableHead /></TableRow></TableHeader>
            <TableBody>{invites.data.map((i) => <TableRow key={i.invitation_id}><TableCell>{i.email}</TableCell><TableCell>{ROLE_LABEL[i.role]}</TableCell><TableCell className="text-[11px]">{i.state}</TableCell><TableCell className="text-[11px]">{day(i.expires_at)}</TableCell>
              <TableCell className="text-right">{i.state === 'PENDING' && <Button size="sm" variant="ghost" className="text-rose-600 cursor-pointer" onClick={() => revoke.mutate(i.invitation_id)}>Revoke</Button>}</TableCell></TableRow>)}</TableBody></Table>
        )}
      </Panel>
    </div>
  );
}

export default function SettingsPage() {
  const { currentProject } = useProjectState();
  const { isArchived } = useV2Project();
  return (
    <div className="space-y-6" data-testid="settings-page">
      <PageHeader project={`${currentProject?.name} (${currentProject?.code})`} title="Project Settings" subtitle="Project details, the rules the server applies to claim decisions, and who is on the project." />
      {isArchived && <Notice tone="warn">This project is archived and read-only. Restore it to make changes.</Notice>}
      <Tabs defaultValue="details">
        <TabsList><TabsTrigger value="details">Details</TabsTrigger><TabsTrigger value="rules">Progress rules</TabsTrigger><TabsTrigger value="members">Members</TabsTrigger></TabsList>
        <TabsContent value="details"><DetailsTab /></TabsContent>
        <TabsContent value="rules"><SettingsTab /></TabsContent>
        <TabsContent value="members"><MembersTab /></TabsContent>
      </Tabs>
    </div>
  );
}
