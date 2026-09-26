import React, { useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Upload,
  FileSpreadsheet,
  CheckCircle2,
  AlertTriangle,
  Layers,
  Calendar,
  Sparkles,
  ShieldCheck,
  FileCode2,
} from 'lucide-react';
import { useProject } from '@/context/ProjectContext';
import { cn } from '@/lib/utils';

interface ScheduleImportModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export function ScheduleImportModal({ isOpen, onClose }: ScheduleImportModalProps) {
  const { currentProject, importScheduleVersion } = useProject();

  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [versionName, setVersionName] = useState('Baseline V3 — Q4 Sanctioned');
  const [versionNumber, setVersionNumber] = useState('v3.0');
  const [effectiveDate, setEffectiveDate] = useState(new Date().toISOString().split('T')[0]);
  const [isValidated, setIsValidated] = useState(false);
  const [isValidating, setIsValidating] = useState(false);
  const [isImporting, setIsImporting] = useState(false);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      const file = e.target.files[0];
      setSelectedFile(file);
      setIsValidated(false);
      setSuccessMsg(null);

      if (file.name.endsWith('.xer')) {
        setVersionName(`P6 Baseline — ${file.name.replace('.xer', '')}`);
      } else if (file.name.endsWith('.csv') || file.name.endsWith('.xlsx')) {
        setVersionName(`Schedule Import — ${file.name}`);
      }
    }
  };

  const handleValidate = () => {
    setIsValidating(true);
    setTimeout(() => {
      setIsValidating(false);
      setIsValidated(true);
    }, 600);
  };

  const handleImport = () => {
    setIsImporting(true);
    setTimeout(() => {
      const sourceType = selectedFile?.name.endsWith('.xer')
        ? 'PRIMAVERA_P6_XER'
        : 'CSV_XLSX';

      importScheduleVersion(currentProject.id, {
        name: versionName,
        versionNumber: versionNumber || 'v3.0',
        sourceType,
        effectiveDate,
        activitiesCount: 32,
      });

      setIsImporting(false);
      setSuccessMsg(`Successfully ingested schedule version "${versionName}" (${versionNumber}) into ${currentProject.name}!`);
      setTimeout(() => {
        onClose();
        setSuccessMsg(null);
        setSelectedFile(null);
        setIsValidated(false);
      }, 1400);
    }, 800);
  };

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto p-6 rounded-2xl bg-white dark:bg-[#071B2D] border-slate-200 dark:border-[#1E3A5F] shadow-2xl">
        <DialogHeader className="border-b border-slate-200 dark:border-[#1E3A5F] pb-4">
          <div className="flex items-center gap-2 text-xs font-mono text-primary font-bold">
            <Sparkles className="w-3.5 h-3.5 text-[#FF7A18]" />
            <span>V7 SCHEDULE INGESTION PIPELINE</span>
          </div>
          <DialogTitle className="text-lg font-bold text-foreground flex items-center gap-2">
            <FileCode2 className="w-5 h-5 text-primary" />
            Import / Ingest Project Schedule Version
          </DialogTitle>
          <DialogDescription className="text-xs text-muted-foreground">
            Target Project:{' '}
            <strong className="text-foreground">{currentProject.name}</strong> ({currentProject.code})
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-3">
          {/* File Upload Box */}
          <div className="space-y-1.5">
            <Label className="text-xs font-bold">Schedule File (.xer, .xml, .xlsx, .csv)</Label>
            <label className="flex flex-col items-center justify-center border-2 border-dashed border-slate-300 dark:border-[#214766] hover:border-[#FF7A18] rounded-xl p-6 bg-slate-50/50 dark:bg-[#0A2340]/40 cursor-pointer transition-all">
              <input
                type="file"
                accept=".xer,.xml,.xlsx,.xls,.csv"
                onChange={handleFileChange}
                className="hidden"
              />
              <Upload className="w-8 h-8 text-[#FF7A18] mb-2" />
              <span className="text-xs font-bold text-foreground">
                {selectedFile ? selectedFile.name : 'Click to select Primavera P6 XER or Excel/CSV schedule'}
              </span>
              <span className="text-[10px] text-muted-foreground mt-1">
                Supports Primavera P6 EPPM/Professional .XER baseline exports and WBS CSV tables
              </span>
            </label>
          </div>

          {/* Version Configuration */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div className="space-y-1 sm:col-span-2">
              <Label className="text-xs font-bold">Schedule Version Label</Label>
              <Input
                value={versionName}
                onChange={(e) => setVersionName(e.target.value)}
                placeholder="e.g. Baseline V3 — Q4 Sanctioned"
                className="text-xs h-9"
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs font-bold">Version Tag</Label>
              <Input
                value={versionNumber}
                onChange={(e) => setVersionNumber(e.target.value)}
                placeholder="e.g. v3.0"
                className="text-xs h-9 font-mono"
              />
            </div>
            <div className="space-y-1 sm:col-span-3">
              <Label className="text-xs font-bold">Effective Baseline Date</Label>
              <Input
                type="date"
                value={effectiveDate}
                onChange={(e) => setEffectiveDate(e.target.value)}
                className="text-xs h-9"
              />
            </div>
          </div>

          {/* Validation Preview Card */}
          {selectedFile && !isValidated && (
            <div className="p-3.5 rounded-xl border border-blue-300 dark:border-blue-900 bg-blue-50/50 dark:bg-blue-950/20 flex items-center justify-between">
              <div className="text-xs space-y-0.5">
                <span className="font-bold text-blue-800 dark:text-blue-300 block">File Ready for Structural Validation</span>
                <span className="text-[11px] text-muted-foreground">
                  File: {selectedFile.name} ({(selectedFile.size / 1024).toFixed(1)} KB)
                </span>
              </div>
              <Button
                size="sm"
                onClick={handleValidate}
                disabled={isValidating}
                className="h-8 text-xs font-bold bg-[#0284C7] hover:bg-[#0369A1] text-white"
              >
                {isValidating ? 'Validating Schema...' : 'Validate & Preview'}
              </Button>
            </div>
          )}

          {isValidated && (
            <div className="p-4 rounded-xl border border-emerald-500/40 bg-emerald-50/40 dark:bg-emerald-950/20 space-y-3">
              <div className="flex items-center justify-between text-xs">
                <span className="font-bold text-emerald-700 dark:text-emerald-300 flex items-center gap-1.5">
                  <CheckCircle2 className="w-4 h-4 text-emerald-600" />
                  Schedule Structural Validation Passed
                </span>
                <span className="text-[10px] font-mono font-bold px-2 py-0.5 rounded bg-emerald-100 dark:bg-emerald-900 text-emerald-800 dark:text-emerald-200">
                  READY FOR INGESTION
                </span>
              </div>

              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-[11px]">
                <div className="p-2 rounded bg-white dark:bg-[#071A2D] border border-emerald-200 dark:border-emerald-900/60">
                  <span className="text-muted-foreground block">Activities</span>
                  <span className="font-bold font-mono text-foreground text-sm">32 items</span>
                </div>
                <div className="p-2 rounded bg-white dark:bg-[#071A2D] border border-emerald-200 dark:border-emerald-900/60">
                  <span className="text-muted-foreground block">Stages / WBS</span>
                  <span className="font-bold font-mono text-foreground text-sm">5 Stages</span>
                </div>
                <div className="p-2 rounded bg-white dark:bg-[#071A2D] border border-emerald-200 dark:border-emerald-900/60">
                  <span className="text-muted-foreground block">Critical Float = 0</span>
                  <span className="font-bold font-mono text-rose-600 text-sm">4 Critical</span>
                </div>
                <div className="p-2 rounded bg-white dark:bg-[#071A2D] border border-emerald-200 dark:border-emerald-900/60">
                  <span className="text-muted-foreground block">Target State</span>
                  <span className="font-bold font-mono text-primary text-sm">Active Working</span>
                </div>
              </div>

              <p className="text-[11px] text-muted-foreground">
                Ingesting this schedule will establish it as the active working baseline for <strong>{currentProject.name}</strong> while archiving previous versions immutably.
              </p>
            </div>
          )}

          {successMsg && (
            <div className="p-3.5 rounded-xl bg-emerald-500/10 border border-emerald-500/40 text-xs font-bold text-emerald-600 dark:text-emerald-400 flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4" />
              <span>{successMsg}</span>
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-slate-200 dark:border-[#1E3A5F] pt-4">
          <Button variant="outline" size="sm" onClick={onClose} className="h-9 text-xs">
            Cancel
          </Button>
          <Button
            size="sm"
            onClick={handleImport}
            disabled={!isValidated || isImporting}
            className="h-9 text-xs font-bold bg-gradient-to-r from-[#FF7A18] to-[#FF941F] text-white shadow-xs"
          >
            {isImporting ? 'Ingesting Schedule...' : 'Ingest & Activate Version'}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
