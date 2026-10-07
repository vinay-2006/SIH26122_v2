import React, { useState } from 'react';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { AlertTriangle, CheckCircle2, FileCode2, Loader2, Upload } from 'lucide-react';
import { useProjectState } from '@/context/ProjectContext';
import { scheduleImportApi } from '@/api/projects';
import { ApiError } from '@/api/client';
import { uploadCeilingProblem } from '@/config';

interface ScheduleImportModalProps {
  isOpen: boolean;
  onClose: () => void;
}

/** Imports a real schedule version (CSV or Primavera P6 XER) into the CURRENT project. The backend validates
 *  the file, stores activities + dependencies transactionally and never overwrites an existing version. */
export function ScheduleImportModal({ isOpen, onClose }: ScheduleImportModalProps) {
  const { currentProject, refresh, setCurrentScheduleVersionId } = useProjectState();
  const [file, setFile] = useState<File | null>(null);
  const [versionCode, setVersionCode] = useState('');
  const [dataDate, setDataDate] = useState('');
  const [activate, setActivate] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);

  if (!currentProject) return null;

  const handleImport = async () => {
    if (!file || !versionCode.trim()) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const text = await file.text();
      const common = { version_code: versionCode.trim(), data_date: dataDate || null, activate_immediately: activate };
      const created = file.name.toLowerCase().endsWith('.xer')
        ? await scheduleImportApi.importXer(currentProject.id, { ...common, xer_content: text })
        : await scheduleImportApi.importCsv(currentProject.id, { ...common, csv_content: text });
      setResult(
        `Imported "${created.version_code}": ${created.activity_count} activities, ${created.dependency_count} dependencies.`,
      );
      refresh();
      setCurrentScheduleVersionId(created.schedule_id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Import failed.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-xl p-6 rounded-2xl bg-white dark:bg-[#071B2D] border-slate-200 dark:border-[#1E3A5F] shadow-2xl">
        <DialogHeader className="border-b border-slate-200 dark:border-[#1E3A5F] pb-4">
          <DialogTitle className="text-lg font-bold text-foreground flex items-center gap-2">
            <FileCode2 className="w-5 h-5 text-primary" />
            Import schedule version
          </DialogTitle>
          <DialogDescription className="text-xs text-muted-foreground">
            Target project: <strong className="text-foreground">{currentProject.name}</strong> ({currentProject.code}).
            The file is validated by the backend; nothing is stored if validation fails.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-3">
          <div className="space-y-1.5">
            <Label className="text-xs font-bold">Schedule file (.xer or .csv)</Label>
            <label className="flex flex-col items-center justify-center border-2 border-dashed border-slate-300 dark:border-[#214766] hover:border-[#FF7A18] rounded-xl p-6 cursor-pointer transition-all">
              <input
                type="file"
                accept=".xer,.csv"
                data-testid="schedule-file-input"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0] ?? null;
                  const tooBig = f ? uploadCeilingProblem(f) : null;
                  setFile(tooBig ? null : f);
                  setResult(null);
                  setError(tooBig);
                }}
              />
              <Upload className="w-8 h-8 text-[#FF7A18] mb-2" />
              <span className="text-xs font-bold text-foreground">{file ? file.name : 'Select a schedule file'}</span>
            </label>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label className="text-xs font-bold">Version code</Label>
              <Input value={versionCode} onChange={(e) => setVersionCode(e.target.value)} placeholder="e.g. V3" className="text-xs h-9 font-mono" />
            </div>
            <div className="space-y-1">
              <Label className="text-xs font-bold">Data date</Label>
              <Input type="date" value={dataDate} onChange={(e) => setDataDate(e.target.value)} className="text-xs h-9" />
            </div>
          </div>

          <label className="flex items-center gap-2 text-xs">
            <input type="checkbox" checked={activate} onChange={(e) => setActivate(e.target.checked)} />
            Make this the active version (the previous active version becomes historical, never deleted)
          </label>

          {error && (
            <div className="flex items-start gap-2 text-xs rounded-lg border border-rose-300 bg-rose-50 dark:bg-rose-950/30 p-3 text-rose-800 dark:text-rose-200" role="alert">
              <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
              <span>{error}</span>
            </div>
          )}
          {result && (
            <div className="flex items-start gap-2 text-xs rounded-lg border border-emerald-300 bg-emerald-50 dark:bg-emerald-950/30 p-3 text-emerald-800 dark:text-emerald-200" role="status">
              <CheckCircle2 className="w-4 h-4 shrink-0 mt-0.5" />
              <span>{result}</span>
            </div>
          )}
        </div>

        <div className="flex justify-end gap-2 pt-2 border-t border-slate-200 dark:border-[#1E3A5F]">
          <Button variant="outline" size="sm" onClick={onClose}>
            Close
          </Button>
          <Button size="sm" onClick={handleImport} disabled={!file || !versionCode.trim() || busy} data-testid="schedule-import-submit">
            {busy && <Loader2 className="w-3.5 h-3.5 animate-spin mr-1" />}
            Import
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
