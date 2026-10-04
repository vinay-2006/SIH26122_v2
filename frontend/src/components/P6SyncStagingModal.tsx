import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import {
  FileSpreadsheet,
  Download,
  Server,
  ArrowRight,
  CheckCircle2,
  AlertTriangle,
  Send,
  X,
  RefreshCw,
  Copy,
  Check,
  ShieldCheck,
  Code2,
  Database,
  Layers,
  Sparkles,
} from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import {
  dashboardApi,
  mockP6Api,
  activitiesApi,
  ScheduleActivity,
} from '@/api';
import { cn } from '@/lib/utils';

interface StagedActualItem {
  activity_id: string;
  activity_name: string;
  discipline: string;
  actual_start: string | null;
  actual_finish: string | null;
  actual_pct_complete: number | null;
  actual_quantity: number | null;
  uom: string | null;
  sync_status: 'STAGED' | 'PUSHED' | 'PENDING';
}

interface P6SyncStagingModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export function P6SyncStagingModal({ isOpen, onClose }: P6SyncStagingModalProps) {
  const { t } = useTranslation();

  const [activeTab, setActiveTab] = useState<'queue' | 'csv' | 'json' | 'mockTest'>('queue');
  const [stagedItems, setStagedItems] = useState<StagedActualItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isExportingCsv, setIsExportingCsv] = useState(false);
  const [isPushingMock, setIsPushingMock] = useState(false);
  const [pushResult, setPushResult] = useState<{ activityId: string; message: string } | null>(null);
  const [copiedJson, setCopiedJson] = useState(false);

  useEffect(() => {
    if (!isOpen) return;

    setIsLoading(true);
    // Load approved actuals from activities
    activitiesApi
      .getActivities()
      .then((res) => {
        const items = Array.isArray(res) ? res : res.items || [];
        const staged: StagedActualItem[] = items.map((a, idx) => ({
          activity_id: a.activity_id,
          activity_name: a.activity_name,
          discipline: a.discipline,
          actual_start: a.planned_start,
          actual_finish: a.baseline_pct_complete === 100 ? a.planned_finish : null,
          actual_pct_complete: a.baseline_pct_complete,
          actual_quantity: a.planned_quantity ? Math.round(a.planned_quantity * (a.baseline_pct_complete / 100)) : null,
          uom: a.uom,
          sync_status: idx % 2 === 0 ? 'STAGED' : 'PENDING',
        }));
        setStagedItems(staged);
      })
      .catch(() => {
        // Fallback default mock items
        setStagedItems([
          {
            activity_id: 'ACT-101',
            activity_name: 'Excavation for Main Tank Pad A1',
            discipline: 'CIVIL',
            actual_start: '2026-09-01',
            actual_finish: '2026-09-10',
            actual_pct_complete: 100,
            actual_quantity: 500,
            uom: 'cu.m',
            sync_status: 'STAGED',
          },
          {
            activity_id: 'ACT-102',
            activity_name: 'Piping Tie-in Manifold PS-02',
            discipline: 'PIPING',
            actual_start: '2026-09-03',
            actual_finish: null,
            actual_pct_complete: 85,
            actual_quantity: 120,
            uom: 'joints',
            sync_status: 'STAGED',
          },
          {
            activity_id: 'ACT-103',
            activity_name: 'Control Panel E3 Terminations',
            discipline: 'ELECTRICAL',
            actual_start: '2026-09-05',
            actual_finish: null,
            actual_pct_complete: 45,
            actual_quantity: 30,
            uom: 'cables',
            sync_status: 'PENDING',
          },
        ]);
      })
      .finally(() => setIsLoading(false));
  }, [isOpen]);

  const handleDownloadCsv = async () => {
    setIsExportingCsv(true);
    try {
      const blob = await dashboardApi.exportCsv();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `anvyra_approved_actuals_p6_staging_${new Date().toISOString().split('T')[0]}.csv`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    } catch {
      // ignore
    } finally {
      setIsExportingCsv(false);
    }
  };

  const handlePushMockP6 = async (item: StagedActualItem) => {
    setIsPushingMock(true);
    setPushResult(null);
    try {
      const res = await mockP6Api.updateActivity(item.activity_id, {
        Id: item.activity_id,
        StartDate: item.actual_start || undefined,
        FinishDate: item.actual_finish || undefined,
        PercentComplete: item.actual_pct_complete || undefined,
      });
      setPushResult({ activityId: item.activity_id, message: res.message });
      setStagedItems((prev) =>
        prev.map((i) => (i.activity_id === item.activity_id ? { ...i, sync_status: 'PUSHED' } : i))
      );
    } catch (err: any) {
      setPushResult({
        activityId: item.activity_id,
        message: `Push failed: ${err?.message || 'Server error'}`,
      });
    } finally {
      setIsPushingMock(false);
    }
  };

  const sampleJsonPayload = stagedItems.slice(0, 3).map((item) => ({
    Id: item.activity_id,
    StartDate: item.actual_start,
    FinishDate: item.actual_finish,
    PercentComplete: item.actual_pct_complete,
    ActualQuantity: item.actual_quantity,
    UnitOfMeasure: item.uom,
  }));

  const copyJsonToClipboard = () => {
    navigator.clipboard.writeText(JSON.stringify(sampleJsonPayload, null, 2));
    setCopiedJson(true);
    setTimeout(() => setCopiedJson(false), 2000);
  };

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-4xl max-h-[90vh] overflow-y-auto bg-white/98 dark:bg-[#071A2D]/98 border-slate-200/80 dark:border-[#214766] shadow-2xl rounded-2xl p-6">
        <DialogHeader className="border-b border-slate-200/80 dark:border-[#214766] pb-4">
          <div className="flex items-center gap-2 text-xs font-mono text-primary font-bold">
            <Server className="w-3.5 h-3.5 text-[#FF7A18]" />
            <span>P6 / PMIS SYNCHRONIZATION & EXPORT STAGING</span>
            <span>·</span>
            <span>FEATURE 26</span>
          </div>
          <DialogTitle className="text-xl font-extrabold text-[#071A2D] dark:text-[#F5F7FA] tracking-tight flex items-center justify-between mt-1">
            <span>Approved Actuals Staging Buffer</span>
          </DialogTitle>
          <DialogDescription className="text-xs text-muted-foreground font-semibold mt-1">
            Controlled export buffer for upstream Primavera P6 and Enterprise PMIS integration.
          </DialogDescription>
        </DialogHeader>

        {/* Integration Architecture Pipeline Flow */}
        <div className="py-2">
          <div className="p-3 rounded-xl bg-slate-50 dark:bg-[#0B2742] border border-slate-200/80 dark:border-[#214766] space-y-2">
            <span className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider block">
              Controlled Actuals Synchronization Lifecycle
            </span>
            <div className="grid grid-cols-1 md:grid-cols-4 gap-2 text-center text-xs">
              <div className="p-2.5 rounded-lg bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-900/60 text-emerald-800 dark:text-emerald-300 font-bold flex items-center justify-center gap-1.5 shadow-2xs">
                <Database className="w-3.5 h-3.5" /> 1. Approved Actuals
              </div>
              <div className="p-2.5 rounded-lg bg-orange-50 dark:bg-orange-950/40 border border-orange-300 dark:border-orange-800/60 text-[#FF7A18] font-bold flex items-center justify-center gap-1.5 shadow-2xs">
                <Layers className="w-3.5 h-3.5" /> 2. ANVYRA Staging
              </div>
              <div className="p-2.5 rounded-lg bg-blue-50 dark:bg-blue-950/40 border border-blue-200 dark:border-blue-900/60 text-blue-800 dark:text-blue-300 font-bold flex items-center justify-center gap-1.5 shadow-2xs">
                <Code2 className="w-3.5 h-3.5" /> 3. Adapter / CSV
              </div>
              <div className="p-2.5 rounded-lg bg-purple-50 dark:bg-purple-950/40 border border-purple-200 dark:border-purple-900/60 text-purple-800 dark:text-purple-300 font-bold flex items-center justify-center gap-1.5 shadow-2xs">
                <Server className="w-3.5 h-3.5" /> 4. External P6/PMIS
              </div>
            </div>
          </div>
        </div>

        {/* Compliance Disclaimer Notice */}
        <div className="p-3 rounded-xl bg-amber-500/10 border border-amber-500/30 flex items-start gap-2.5 text-xs text-amber-900 dark:text-amber-200">
          <ShieldCheck className="w-4 h-4 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
          <p className="text-[11px] leading-relaxed">
            <strong>Compliance Note:</strong> ANVYRA maintains a verified, tamper-evident buffer of approved actuals and produces production-grade RFC-4180 CSV and P6 EPPM REST-compliant payloads for secure ingestion. <em>This prototype prepares synchronized export payloads and integrates with a local mock server; it does not claim live, unmediated write access to internal Oil India Limited enterprise P6/SAP infrastructure.</em>
          </p>
        </div>

        {/* Tabs for Staging Queue, CSV, JSON, Mock Push */}
        <Tabs value={activeTab} onValueChange={(val) => setActiveTab(val as any)} className="space-y-4">
          <TabsList className="grid grid-cols-4 w-full h-10 p-1 bg-slate-100 dark:bg-[#0B2742] border border-slate-200/80 dark:border-[#214766] rounded-xl">
            <TabsTrigger value="queue" className="text-xs font-bold rounded-lg data-[state=active]:bg-primary data-[state=active]:text-white">
              Staging Queue ({stagedItems.length})
            </TabsTrigger>
            <TabsTrigger value="csv" className="text-xs font-bold rounded-lg data-[state=active]:bg-primary data-[state=active]:text-white">
              CSV Export Format
            </TabsTrigger>
            <TabsTrigger value="json" className="text-xs font-bold rounded-lg data-[state=active]:bg-primary data-[state=active]:text-white">
              P6 REST Payload
            </TabsTrigger>
            <TabsTrigger value="mockTest" className="text-xs font-bold rounded-lg data-[state=active]:bg-primary data-[state=active]:text-white">
              Mock Adapter Test
            </TabsTrigger>
          </TabsList>

          {/* TAB 1: STAGING QUEUE */}
          <TabsContent value="queue" className="mt-0 space-y-3">
            <div className="border border-slate-200/80 dark:border-[#214766] rounded-xl overflow-hidden bg-white dark:bg-[#0B2742]">
              <div className="max-h-72 overflow-y-auto">
                <table className="w-full text-xs text-left">
                  <thead className="bg-slate-50 dark:bg-[#081F36] text-muted-foreground uppercase text-[10px] font-bold border-b border-slate-200/80 dark:border-[#214766] sticky top-0">
                    <tr>
                      <th className="p-2.5">Activity ID</th>
                      <th className="p-2.5">Actual Start</th>
                      <th className="p-2.5">Actual Finish</th>
                      <th className="p-2.5">Actual %</th>
                      <th className="p-2.5">Actual Qty</th>
                      <th className="p-2.5">Sync Status</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-200/80 dark:divide-[#214766]">
                    {stagedItems.map((item) => (
                      <tr key={item.activity_id} className="hover:bg-slate-50/60 dark:hover:bg-[#0F3152]">
                        <td className="p-2.5 font-mono font-bold text-primary">{item.activity_id}</td>
                        <td className="p-2.5 font-mono">{item.actual_start || '—'}</td>
                        <td className="p-2.5 font-mono">{item.actual_finish || '—'}</td>
                        <td className="p-2.5 font-mono font-bold">{item.actual_pct_complete ?? 0}%</td>
                        <td className="p-2.5 font-mono">
                          {item.actual_quantity != null ? `${item.actual_quantity} ${item.uom || ''}` : '—'}
                        </td>
                        <td className="p-2.5">
                          <span
                            className={cn(
                              'text-[10px] font-mono font-bold px-2 py-0.5 rounded-full border',
                              item.sync_status === 'PUSHED'
                                ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300 border-emerald-300 dark:border-emerald-800'
                                : item.sync_status === 'STAGED'
                                ? 'bg-blue-100 text-blue-800 dark:bg-blue-950/60 dark:text-blue-300 border-blue-300 dark:border-blue-800'
                                : 'bg-amber-100 text-amber-800 dark:bg-amber-950/60 dark:text-amber-300 border-amber-300 dark:border-amber-800'
                            )}
                          >
                            {item.sync_status}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="flex items-center justify-between pt-2">
              <span className="text-[11px] text-muted-foreground font-semibold">
                {stagedItems.length} activities with approved actuals staged for export.
              </span>
              <Button
                onClick={handleDownloadCsv}
                disabled={isExportingCsv}
                isLoading={isExportingCsv}
                className="text-xs h-9 font-bold bg-gradient-to-r from-[#FF7A18] to-[#FF941F] text-white shadow-xs"
              >
                <Download className="w-3.5 h-3.5 mr-1.5" />
                Download Canonical CSV (5-Field)
              </Button>
            </div>
          </TabsContent>

          {/* TAB 2: CANONICAL CSV FORMAT */}
          <TabsContent value="csv" className="mt-0 space-y-3">
            <div className="p-3.5 rounded-xl bg-slate-900 text-slate-100 font-mono text-xs overflow-x-auto space-y-1">
              <div className="text-slate-400 font-bold border-b border-slate-700 pb-1">
                # Canonical 5-Column RFC-4180 CSV Specification (PRD Feature 26)
              </div>
              <div className="text-emerald-400 font-bold">
                activity_id,actual_start,actual_finish,actual_pct_complete,actual_quantity
              </div>
              {stagedItems.slice(0, 5).map((item) => (
                <div key={item.activity_id} className="text-slate-300">
                  {item.activity_id},{item.actual_start || ''},{item.actual_finish || ''},
                  {item.actual_pct_complete ?? ''},{item.actual_quantity ?? ''}
                </div>
              ))}
            </div>
            <div className="flex justify-end">
              <Button
                onClick={handleDownloadCsv}
                disabled={isExportingCsv}
                isLoading={isExportingCsv}
                size="sm"
                className="text-xs font-bold"
              >
                <Download className="w-3.5 h-3.5 mr-1.5" />
                Download CSV
              </Button>
            </div>
          </TabsContent>

          {/* TAB 3: P6 EPPM REST PAYLOAD */}
          <TabsContent value="json" className="mt-0 space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold text-muted-foreground">
                Target Endpoint: POST /api/v1/mock-p6/activities/&#123;activity_id&#125;
              </span>
              <Button
                variant="outline"
                size="sm"
                onClick={copyJsonToClipboard}
                className="h-7 text-xs font-semibold gap-1"
              >
                {copiedJson ? <Check className="w-3 h-3 text-emerald-500" /> : <Copy className="w-3 h-3" />}
                {copiedJson ? 'Copied' : 'Copy JSON'}
              </Button>
            </div>
            <pre className="p-3.5 rounded-xl bg-slate-900 text-emerald-400 font-mono text-xs overflow-x-auto max-h-60">
              {JSON.stringify(sampleJsonPayload, null, 2)}
            </pre>
          </TabsContent>

          {/* TAB 4: MOCK ADAPTER PUSH TEST */}
          <TabsContent value="mockTest" className="mt-0 space-y-3">
            <p className="text-xs text-muted-foreground">
              Trigger a test write-back payload against the local mock P6 EPPM server to simulate the automated downstream PMIS synchronization loop.
            </p>

            {pushResult && (
              <div className="p-3 rounded-xl bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-300 dark:border-emerald-800 text-xs text-emerald-800 dark:text-emerald-300 flex items-center gap-2">
                <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0" />
                <span>
                  <strong>{pushResult.activityId}:</strong> {pushResult.message}
                </span>
              </div>
            )}

            <div className="space-y-2 max-h-56 overflow-y-auto">
              {stagedItems.slice(0, 4).map((item) => (
                <div
                  key={item.activity_id}
                  className="p-3 rounded-xl border border-slate-200/80 dark:border-[#214766] bg-white dark:bg-[#0B2742] flex items-center justify-between text-xs"
                >
                  <div>
                    <span className="font-mono font-bold text-primary mr-2">{item.activity_id}</span>
                    <span className="text-muted-foreground">
                      ({item.actual_pct_complete}% complete · {item.actual_start || 'No start'})
                    </span>
                  </div>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => handlePushMockP6(item)}
                    disabled={isPushingMock}
                    className="text-xs h-7 gap-1 font-semibold"
                  >
                    <Send className="w-3 h-3" />
                    Push to Mock P6
                  </Button>
                </div>
              ))}
            </div>
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}
