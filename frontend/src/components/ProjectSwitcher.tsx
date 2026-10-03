import React, { useEffect, useRef, useState } from 'react';
import { Building2, Calendar, Check, ChevronDown, Lock, Upload } from 'lucide-react';
import { useProjectState } from '@/context/ProjectContext';
import { Button } from '@/components/ui/button';
import { ScheduleImportModal } from '@/components/ScheduleImportModal';
import { cn } from '@/lib/utils';
import { IS_V2 } from '@/config';

/** Project + schedule-version selectors. Both choices are explicit and are what every API call carries. */
export function ProjectSwitcher() {
  const {
    projects,
    currentProject,
    setCurrentProjectId,
    currentScheduleVersion,
    scheduleVersions,
    setCurrentScheduleVersionId,
    can,
  } = useProjectState();

  const [isProjectOpen, setIsProjectOpen] = useState(false);
  const [isVersionOpen, setIsVersionOpen] = useState(false);
  const [isImportModalOpen, setIsImportModalOpen] = useState(false);
  const projectRef = useRef<HTMLDivElement>(null);
  const versionRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (projectRef.current && !projectRef.current.contains(event.target as Node)) setIsProjectOpen(false);
      if (versionRef.current && !versionRef.current.contains(event.target as Node)) setIsVersionOpen(false);
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  if (!projects.length) return null;

  const canManageSchedule = !IS_V2 && can('MANAGE_SCHEDULE'); // v2 schedule management has its own page (Schedule)
  const selectedProjectId = currentProject?.id ?? null;
  const menu =
    'absolute left-0 top-11 w-80 p-2 rounded-2xl bg-white dark:bg-[#071B2D] border border-slate-200 dark:border-[#1E3A5F] shadow-2xl space-y-1 z-50';
  const trigger =
    'h-9 px-2.5 sm:px-3 text-xs rounded-xl border-slate-300 dark:border-[#1E3A5F] bg-white/95 dark:bg-[#0A2340] hover:bg-[#EEF5FC] dark:hover:bg-[#0B2D4A] text-foreground shadow-2xs font-semibold gap-2 cursor-pointer';

  return (
    <>
      <div className="flex items-center gap-2 max-w-full">
        <div className="relative" ref={projectRef}>
          <Button
            variant="outline"
            size="sm"
            data-testid="project-switcher"
            onClick={() => {
              setIsProjectOpen(!isProjectOpen);
              setIsVersionOpen(false);
            }}
            className={cn(trigger, 'max-w-[200px] sm:max-w-[280px] truncate')}
          >
            <Building2 className="w-3.5 h-3.5 text-[#FF7A18] shrink-0" />
            <div className="flex flex-col items-start min-w-0 text-left truncate">
              <span className="truncate text-[11px] font-bold leading-tight text-[#071A2D] dark:text-[#F5F7FA]">
                {currentProject?.name ?? 'Select a project'}
              </span>
              <span className="text-[9px] font-mono text-muted-foreground truncate leading-none">
                {currentProject ? `${currentProject.code} · ${currentProject.role}` : `${projects.length} available`}
              </span>
            </div>
            <ChevronDown className="w-3.5 h-3.5 text-muted-foreground shrink-0 ml-auto" />
          </Button>

          {isProjectOpen && (
            <div className={menu} role="listbox" aria-label="Projects">
              <div className="text-[10px] font-mono text-muted-foreground uppercase tracking-wider px-2 py-1 flex items-center justify-between">
                <span>Your projects</span>
                <span className="font-bold text-[#FF7A18]">{projects.length}</span>
              </div>
              <div className="h-px bg-slate-200 dark:bg-[#1E3A5F] my-1" />
              <div className="max-h-72 overflow-y-auto space-y-1">
                {projects.map((proj) => {
                  const isSelected = proj.project_id === selectedProjectId;
                  return (
                    <div
                      key={proj.project_id}
                      role="option"
                      aria-selected={isSelected}
                      data-testid={`project-option-${proj.project_code}`}
                      onClick={() => {
                        setCurrentProjectId(proj.project_id);
                        setIsProjectOpen(false);
                      }}
                      className={cn(
                        'p-2.5 rounded-xl cursor-pointer transition-all flex flex-col items-start gap-1',
                        isSelected
                          ? 'bg-orange-50 dark:bg-orange-950/40 border border-orange-300 dark:border-orange-800/80'
                          : 'hover:bg-slate-100 dark:hover:bg-[#0B2D4A]',
                      )}
                    >
                      <div className="flex items-center justify-between w-full">
                        <span className="font-bold text-xs flex items-center gap-1.5 text-[#071A2D] dark:text-[#F5F7FA]">
                          <Building2 className="w-3.5 h-3.5 text-[#FF7A18]" />
                          {proj.project_name}
                        </span>
                        {isSelected && <Check className="w-4 h-4 text-[#FF7A18] shrink-0" />}
                      </div>
                      <div className="flex items-center justify-between w-full text-[10px] text-muted-foreground font-mono">
                        <span>{proj.project_code}</span>
                        <span>{proj.assigned_role}</span>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        {currentProject && (
          <div className="relative hidden md:block" ref={versionRef}>
            <Button
              variant="outline"
              size="sm"
              data-testid="version-switcher"
              onClick={() => {
                setIsVersionOpen(!isVersionOpen);
                setIsProjectOpen(false);
              }}
              className={cn(trigger, 'flex items-center max-w-[260px] truncate')}
            >
              <Calendar className="w-3.5 h-3.5 text-[#0284C7] shrink-0" />
              <div className="flex flex-col items-start min-w-0 text-left truncate">
                <div className="flex items-center gap-1 truncate">
                  <span className="truncate text-[11px] font-bold text-[#071A2D] dark:text-[#F5F7FA]">
                    {currentScheduleVersion?.versionNumber ?? 'Select schedule version'}
                  </span>
                  {currentScheduleVersion &&
                    (currentScheduleVersion.isCurrent ? (
                      <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 shrink-0" />
                    ) : (
                      <Lock className="w-2.5 h-2.5 text-muted-foreground shrink-0" />
                    ))}
                </div>
                <span className="text-[9px] font-mono text-muted-foreground truncate leading-none">
                  {currentScheduleVersion
                    ? `${currentScheduleVersion.activitiesCount} activities`
                    : `${scheduleVersions.length} versions`}
                </span>
              </div>
              <ChevronDown className="w-3.5 h-3.5 text-muted-foreground shrink-0 ml-auto" />
            </Button>

            {isVersionOpen && (
              <div className={menu} role="listbox" aria-label="Schedule versions">
                <div className="text-[10px] font-mono text-muted-foreground uppercase tracking-wider px-2 py-1 flex items-center justify-between">
                  <span>Schedule versions</span>
                  <span className="text-primary font-bold">{currentProject.code}</span>
                </div>
                <div className="h-px bg-slate-200 dark:bg-[#1E3A5F] my-1" />
                <div className="max-h-64 overflow-y-auto space-y-1">
                  {scheduleVersions.map((ver) => {
                    const isSelected = ver.id === currentScheduleVersion?.id;
                    return (
                      <div
                        key={ver.id}
                        role="option"
                        aria-selected={isSelected}
                        data-testid={`version-option-${ver.versionNumber}`}
                        onClick={() => {
                          setCurrentScheduleVersionId(ver.id);
                          setIsVersionOpen(false);
                        }}
                        className={cn(
                          'p-2.5 rounded-xl cursor-pointer transition-all flex flex-col items-start gap-1',
                          isSelected
                            ? 'bg-blue-50 dark:bg-blue-950/40 border border-blue-300 dark:border-blue-800'
                            : 'hover:bg-slate-100 dark:hover:bg-[#0B2D4A]',
                        )}
                      >
                        <div className="flex items-center justify-between w-full">
                          <span className="font-bold text-xs text-[#071A2D] dark:text-[#F5F7FA]">{ver.versionNumber}</span>
                          {ver.isCurrent ? (
                            <span className="text-[9px] font-mono font-bold px-1.5 py-0.5 rounded bg-emerald-100 dark:bg-emerald-900 text-emerald-800 dark:text-emerald-200">
                              ACTIVE
                            </span>
                          ) : (
                            <span className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-slate-200 dark:bg-slate-800 text-muted-foreground flex items-center gap-1">
                              <Lock className="w-2.5 h-2.5" />
                              HISTORICAL
                            </span>
                          )}
                        </div>
                        <div className="flex items-center justify-between w-full text-[10px] text-muted-foreground font-mono">
                          <span>
                            {ver.activitiesCount} act{ver.dependenciesCount >= 0 ? ` · ${ver.dependenciesCount} deps` : ''} · {ver.sourceType}
                          </span>
                          <span>{ver.effectiveDate}</span>
                        </div>
                      </div>
                    );
                  })}
                </div>

                {canManageSchedule && (
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
                      <span>Import schedule version (CSV / XER)…</span>
                    </button>
                  </>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      {currentProject && canManageSchedule && (
        <ScheduleImportModal isOpen={isImportModalOpen} onClose={() => setIsImportModalOpen(false)} />
      )}
    </>
  );
}
