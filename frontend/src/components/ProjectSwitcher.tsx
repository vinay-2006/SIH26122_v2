import React, { useState, useRef, useEffect } from 'react';
import {
  Building2,
  Calendar,
  ChevronDown,
  Upload,
  Check,
  Lock,
} from 'lucide-react';
import { useProject } from '@/context/ProjectContext';
import { useAuth } from '@/auth/AuthProvider';
import { Button } from '@/components/ui/button';
import { ScheduleImportModal } from '@/components/ScheduleImportModal';
import { cn } from '@/lib/utils';

export function ProjectSwitcher() {
  const {
    projects,
    currentProject,
    setCurrentProjectId,
    currentScheduleVersion,
    setCurrentScheduleVersionId,
  } = useProject();

  const { user } = useAuth();
  const isSupervisor = user?.role === 'SUPERVISOR';

  const [isProjectOpen, setIsProjectOpen] = useState(false);
  const [isVersionOpen, setIsVersionOpen] = useState(false);
  const [isImportModalOpen, setIsImportModalOpen] = useState(false);

  const projectRef = useRef<HTMLDivElement>(null);
  const versionRef = useRef<HTMLDivElement>(null);

  // Close dropdowns on click outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (projectRef.current && !projectRef.current.contains(event.target as Node)) {
        setIsProjectOpen(false);
      }
      if (versionRef.current && !versionRef.current.contains(event.target as Node)) {
        setIsVersionOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  return (
    <>
      <div className="flex items-center gap-2 max-w-full">
        {/* Project Selector Dropdown */}
        <div className="relative" ref={projectRef}>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setIsProjectOpen(!isProjectOpen);
              setIsVersionOpen(false);
            }}
            className="h-9 px-2.5 sm:px-3 text-xs rounded-xl border-slate-300 dark:border-[#1E3A5F] bg-white/95 dark:bg-[#0A2340] hover:bg-[#EEF5FC] dark:hover:bg-[#0B2D4A] text-foreground shadow-2xs font-semibold gap-2 max-w-[200px] sm:max-w-[280px] truncate cursor-pointer"
          >
            <Building2 className="w-3.5 h-3.5 text-[#FF7A18] shrink-0" />
            <div className="flex flex-col items-start min-w-0 text-left truncate">
              <span className="truncate text-[11px] font-bold leading-tight text-[#071A2D] dark:text-[#F5F7FA]">
                {currentProject.name}
              </span>
              <span className="text-[9px] font-mono text-muted-foreground truncate leading-none">
                {currentProject.code} · {currentProject.region}
              </span>
            </div>
            <ChevronDown className="w-3.5 h-3.5 text-muted-foreground shrink-0 ml-auto" />
          </Button>

          {isProjectOpen && (
            <div className="absolute left-0 top-11 w-80 p-2 rounded-2xl bg-white dark:bg-[#071B2D] border border-slate-200 dark:border-[#1E3A5F] shadow-2xl space-y-1 z-50 animate-in fade-in zoom-in-95 duration-100">
              <div className="text-[10px] font-mono text-muted-foreground uppercase tracking-wider px-2 py-1 flex items-center justify-between">
                <span>Select Active Enterprise Project</span>
                <span className="font-bold text-[#FF7A18]">{projects.length} Total</span>
              </div>
              <div className="h-px bg-slate-200 dark:bg-[#1E3A5F] my-1" />

              <div className="max-h-72 overflow-y-auto space-y-1">
                {projects.map((proj) => {
                  const isSelected = proj.id === currentProject.id;
                  return (
                    <div
                      key={proj.id}
                      onClick={() => {
                        setCurrentProjectId(proj.id);
                        setIsProjectOpen(false);
                      }}
                      className={cn(
                        'p-2.5 rounded-xl cursor-pointer transition-all flex flex-col items-start gap-1',
                        isSelected
                          ? 'bg-orange-50 dark:bg-orange-950/40 border border-orange-300 dark:border-orange-800/80 text-foreground'
                          : 'hover:bg-slate-100 dark:hover:bg-[#0B2D4A] text-foreground'
                      )}
                    >
                      <div className="flex items-center justify-between w-full">
                        <span className="font-bold text-xs flex items-center gap-1.5 text-[#071A2D] dark:text-[#F5F7FA]">
                          <Building2 className="w-3.5 h-3.5 text-[#FF7A18]" />
                          {proj.name}
                        </span>
                        {isSelected && (
                          <Check className="w-4 h-4 text-[#FF7A18] shrink-0" />
                        )}
                      </div>

                      <div className="flex items-center justify-between w-full text-[10px] text-muted-foreground">
                        <span className="font-mono">{proj.code} · {proj.region}</span>
                        <span className="font-mono font-bold text-emerald-600 dark:text-emerald-400">
                          {proj.overallActual}% / {proj.overallPlanned}%
                        </span>
                      </div>

                      <div className="w-full flex items-center justify-between text-[10px] pt-1 border-t border-slate-200/60 dark:border-[#1E3A5F]/60">
                        <span className="text-muted-foreground truncate">{proj.activeStage}</span>
                        <span className="font-mono">{proj.scheduleVersions.length} Sched Versions</span>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        {/* Schedule Version Selector Dropdown */}
        <div className="relative hidden md:block" ref={versionRef}>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setIsVersionOpen(!isVersionOpen);
              setIsProjectOpen(false);
            }}
            className="h-9 px-2.5 sm:px-3 text-xs rounded-xl border-slate-300 dark:border-[#1E3A5F] bg-white/95 dark:bg-[#0A2340] hover:bg-[#EEF5FC] dark:hover:bg-[#0B2D4A] text-foreground shadow-2xs font-semibold gap-1.5 flex items-center max-w-[260px] truncate cursor-pointer"
          >
            <Calendar className="w-3.5 h-3.5 text-[#0284C7] shrink-0" />
            <div className="flex flex-col items-start min-w-0 text-left truncate">
              <div className="flex items-center gap-1 truncate">
                <span className="truncate text-[11px] font-bold text-[#071A2D] dark:text-[#F5F7FA]">
                  {currentScheduleVersion.versionNumber}
                </span>
                {currentScheduleVersion.isCurrent ? (
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 shrink-0" />
                ) : (
                  <Lock className="w-2.5 h-2.5 text-muted-foreground shrink-0" />
                )}
              </div>
              <span className="text-[9px] font-mono text-muted-foreground truncate leading-none">
                {currentScheduleVersion.name || currentScheduleVersion.status}
              </span>
            </div>
            <ChevronDown className="w-3.5 h-3.5 text-muted-foreground shrink-0 ml-auto" />
          </Button>

          {isVersionOpen && (
            <div className="absolute left-0 top-11 w-80 p-2 rounded-2xl bg-white dark:bg-[#071B2D] border border-slate-200 dark:border-[#1E3A5F] shadow-2xl space-y-1 z-50 animate-in fade-in zoom-in-95 duration-100">
              <div className="text-[10px] font-mono text-muted-foreground uppercase tracking-wider px-2 py-1 flex items-center justify-between">
                <span>Schedule Versions</span>
                <span className="text-primary font-bold">{currentProject.code}</span>
              </div>
              <div className="h-px bg-slate-200 dark:bg-[#1E3A5F] my-1" />

              <div className="max-h-64 overflow-y-auto space-y-1">
                {currentProject.scheduleVersions.map((ver) => {
                  const isSelected = ver.id === currentScheduleVersion.id;
                  return (
                    <div
                      key={ver.id}
                      onClick={() => {
                        setCurrentScheduleVersionId(ver.id);
                        setIsVersionOpen(false);
                      }}
                      className={cn(
                        'p-2.5 rounded-xl cursor-pointer transition-all flex flex-col items-start gap-1',
                        isSelected
                          ? 'bg-blue-50 dark:bg-blue-950/40 border border-blue-300 dark:border-blue-800 text-foreground'
                          : 'hover:bg-slate-100 dark:hover:bg-[#0B2D4A] text-foreground'
                      )}
                    >
                      <div className="flex items-center justify-between w-full">
                        <span className="font-bold text-xs flex items-center gap-1.5 text-[#071A2D] dark:text-[#F5F7FA]">
                          {ver.name}
                        </span>
                        {ver.isCurrent && (
                          <span className="text-[9px] font-mono font-bold px-1.5 py-0.5 rounded bg-emerald-100 dark:bg-emerald-900 text-emerald-800 dark:text-emerald-200">
                            ACTIVE
                          </span>
                        )}
                        {ver.isImmutable && !ver.isCurrent && (
                          <span className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-slate-200 dark:bg-slate-800 text-muted-foreground flex items-center gap-1">
                            <Lock className="w-2.5 h-2.5" />
                            READ-ONLY
                          </span>
                        )}
                      </div>

                      <div className="flex items-center justify-between w-full text-[10px] text-muted-foreground font-mono">
                        <span>Tag: {ver.versionNumber} · {ver.sourceType}</span>
                        <span>Eff: {ver.effectiveDate}</span>
                      </div>
                    </div>
                  );
                })}
              </div>

              {isSupervisor && (
                <>
                  <div className="h-px bg-slate-200 dark:bg-[#1E3A5F] my-1" />
                  <button
                    type="button"
                    onClick={() => {
                      setIsVersionOpen(false);
                      setIsImportModalOpen(true);
                    }}
                    className="w-full p-2 rounded-xl text-xs font-bold text-primary hover:bg-primary/10 cursor-pointer flex items-center gap-2 transition-colors"
                  >
                    <Upload className="w-3.5 h-3.5" />
                    <span>Import New Baseline / XER...</span>
                  </button>
                </>
              )}
            </div>
          )}
        </div>

        {/* Quick Schedule Import Action Button - SUPERVISOR ONLY */}
        {isSupervisor && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => setIsImportModalOpen(true)}
            className="h-9 px-2.5 text-xs rounded-xl border-slate-300 dark:border-[#1E3A5F] bg-white/95 dark:bg-[#0A2340] hover:bg-orange-50 dark:hover:bg-orange-950/40 text-foreground shadow-2xs font-semibold gap-1.5 hidden xl:flex items-center cursor-pointer"
            title="Import Primavera P6 XER or CSV Schedule Version"
          >
            <Upload className="w-3.5 h-3.5 text-[#FF7A18]" />
            <span>Import Schedule</span>
          </Button>
        )}
      </div>

      {/* Embedded Ingestion Modal */}
      {isSupervisor && (
        <ScheduleImportModal
          isOpen={isImportModalOpen}
          onClose={() => setIsImportModalOpen(false)}
        />
      )}
    </>
  );
}

