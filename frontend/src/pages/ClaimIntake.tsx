import React, { useState, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Mic,
  MicOff,
  FileText,
  Files,
  Upload,
  CheckCircle2,
  AlertTriangle,
  FileAudio,
  PlusCircle,
  RotateCcw,
  Sparkles,
  Send,
  Paperclip,
  X,
  Building2,
  Lock,
  Unlock,
  ShieldAlert,
  GitFork,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { Label } from '@/components/ui/label';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { BatchUploadPanel } from '@/components/BatchUploadPanel';
import { StatusBadge } from '@/components/StatusBadge';
import { ErrorState } from '@/components/ui/error-state';
import {
  claimsApi,
  schedulesApi,
  qualityGatesApi,
  impactApi,
  ExecutionEvent,
  CandidateMatch,
  ScheduleActivity,
  ExecutionState,
  QualityGate,
  CompoundImpact,
} from '@/api';
import { cn } from '@/lib/utils';
import { useProject } from '@/context/ProjectContext';
import { ExecutionStateBadge } from '@/components/ExecutionStateBadge';
import { ReopenRequestModal } from '@/components/ReopenRequestModal';

type InputTab = 'batch' | 'text' | 'voice' | 'file';

const MAX_TEXT_LENGTH = 2000;
const MIN_TEXT_LENGTH = 10;
const MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024; // 25 MB
const ACCEPTED_FILE_EXTS = ['.pdf', '.xlsx', '.xls', '.csv', '.txt', '.xer', '.jpg', '.jpeg', '.png'];

export default function ClaimIntake() {
  const { t } = useTranslation();
  const { currentProject, currentScheduleVersion } = useProject();

  const [activeTab, setActiveTab] = useState<InputTab>('batch');

  // Text Tab State
  const [textValue, setTextValue] = useState('');
  const [textError, setTextError] = useState<string | null>(null);

  // Evidence Attachment State for Typed Claims (P0-1)
  const [evidenceFile, setEvidenceFile] = useState<File | null>(null);
  const [evidenceError, setEvidenceError] = useState<string | null>(null);
  const [evidencePreviewUrl, setEvidencePreviewUrl] = useState<string | null>(null);
  const evidenceInputRef = useRef<HTMLInputElement>(null);

  // Voice Tab — Web Speech API
  const [isRecording, setIsRecording] = useState(false);
  const [voiceTranscript, setVoiceTranscript] = useState('');
  const [speechNotice, setSpeechNotice] = useState<string | null>(null);
  const recognitionRef = useRef<any>(null);

  // Voice Audio File Upload State
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const audioInputRef = useRef<HTMLInputElement>(null);

  // File Tab State
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Pipeline Stepper State (0: Idle, 1: Submitting, 2: Matching, 3: Checking, 4: Complete)
  const [pipelineStep, setPipelineStep] = useState<number>(0);
  const [isProcessing, setIsProcessing] = useState(false);
  const isSubmittingRef = useRef(false);
  const [createdEvents, setCreatedEvents] = useState<ExecutionEvent[]>([]);
  const [pipelineError, setPipelineError] = useState<string | null>(null);

  // Feature 29: Adaptive Field Copilot (Clarification State)
  const [clarificationAnswers, setClarificationAnswers] = useState<Record<string, string>>({});
  const [clarifyingEventId, setClarifyingEventId] = useState<string | null>(null);
  const [clarifyError, setClarifyError] = useState<Record<string, string | null>>({});

  // Reopen Request & Completed Activity Protection State
  const [candidateMatches, setCandidateMatches] = useState<Record<string, CandidateMatch[]>>({});
  const [activitiesMap, setActivitiesMap] = useState<Record<string, ScheduleActivity>>({});
  const [scheduleQualityGates, setScheduleQualityGates] = useState<QualityGate[]>([]);
  const [scheduleImpacts, setScheduleImpacts] = useState<CompoundImpact[]>([]);
  const [reopenModalActivity, setReopenModalActivity] = useState<ScheduleActivity | null>(null);
  const [reopenModalEventId, setReopenModalEventId] = useState<string | undefined>(undefined);
  const [reopenedActivityIds, setReopenedActivityIds] = useState<Set<string>>(new Set());

  // Web Speech API Initialization
  useEffect(() => {
    const SpeechRecognition =
      (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;

    if (SpeechRecognition) {
      const recognition = new SpeechRecognition();
      recognition.continuous = true;
      recognition.interimResults = true;
      recognition.lang = 'en-US';

      recognition.onresult = (event: any) => {
        let currentTranscript = '';
        for (let i = 0; i < event.results.length; i++) {
          currentTranscript += event.results[i][0].transcript;
        }
        setVoiceTranscript(currentTranscript);
      };

      recognition.onerror = (err: any) => {
        setSpeechNotice(err.error === 'not-allowed'
          ? 'Microphone permission denied. Please allow microphone access or upload an audio file.'
          : `Microphone error (${err.error}). Please type or upload an audio file.`);
        setIsRecording(false);
      };

      recognition.onend = () => {
        setIsRecording(false);
      };

      recognitionRef.current = recognition;
    } else {
      setSpeechNotice(t('intake.speechUnsupported'));
    }

    return () => {
      if (recognitionRef.current) {
        try {
          recognitionRef.current.stop();
        } catch {
          // ignore cleanup errors
        }
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggleRecording = () => {
    if (!recognitionRef.current) {
      setSpeechNotice(t('intake.speechUnsupported'));
      return;
    }

    if (isRecording) {
      recognitionRef.current.stop();
      setIsRecording(false);
    } else {
      setSpeechNotice(null);
      try {
        recognitionRef.current.start();
        setIsRecording(true);
      } catch (e: any) {
        setSpeechNotice(`Could not start recording: ${e.message}`);
      }
    }
  };

  const handleAudioFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      setAudioFile(file);
      setAudioUrl(URL.createObjectURL(file));
      setVoiceTranscript(`[Uploaded Audio: ${file.name}]`);
    }
  };

  const removeAudioFile = () => {
    setAudioFile(null);
    if (audioUrl) URL.revokeObjectURL(audioUrl);
    setAudioUrl(null);
    if (audioInputRef.current) audioInputRef.current.value = '';
  };

  const handleFileSelect = (file: File | null) => {
    if (!file) {
      setSelectedFile(null);
      setFileError(null);
      return;
    }

    if (file.size > MAX_FILE_SIZE_BYTES) {
      setFileError(t('intake.errFileTooLarge'));
      setSelectedFile(null);
      return;
    }

    const ext = '.' + file.name.split('.').pop()?.toLowerCase();
    if (!ACCEPTED_FILE_EXTS.includes(ext)) {
      setFileError(t('intake.errFileType'));
      setSelectedFile(null);
      return;
    }

    setFileError(null);
    setSelectedFile(file);
  };

  const handleEvidenceSelect = (file: File | null) => {
    if (evidencePreviewUrl) {
      URL.revokeObjectURL(evidencePreviewUrl);
      setEvidencePreviewUrl(null);
    }
    if (!file) {
      setEvidenceFile(null);
      setEvidenceError(null);
      return;
    }
    const ext = `.${file.name.split('.').pop()?.toLowerCase()}`;
    if (!ACCEPTED_FILE_EXTS.includes(ext)) {
      setEvidenceError(t('intake.errFileType'));
      setEvidenceFile(null);
      return;
    }
    if (file.size > MAX_FILE_SIZE_BYTES) {
      setEvidenceError('Evidence file exceeds 25MB maximum size limit.');
      setEvidenceFile(null);
      return;
    }
    setEvidenceError(null);
    setEvidenceFile(file);
    if (file.type.startsWith('image/') || ['.jpg', '.jpeg', '.png'].includes(ext)) {
      setEvidencePreviewUrl(URL.createObjectURL(file));
    }
  };

  const removeEvidenceFile = () => {
    if (evidencePreviewUrl) {
      URL.revokeObjectURL(evidencePreviewUrl);
      setEvidencePreviewUrl(null);
    }
    setEvidenceFile(null);
    setEvidenceError(null);
    if (evidenceInputRef.current) evidenceInputRef.current.value = '';
  };

  const handleResetForm = () => {
    setTextValue('');
    setTextError(null);
    removeEvidenceFile();
    setVoiceTranscript('');
    removeAudioFile();
    setSelectedFile(null);
    setFileError(null);
    if (fileInputRef.current) fileInputRef.current.value = '';
    setPipelineStep(0);
    setCreatedEvents([]);
    setPipelineError(null);
    setClarificationAnswers({});
    setClarifyingEventId(null);
    setClarifyError({});
    setCandidateMatches({});
    setReopenModalActivity(null);
  };

  const proceedMatchingAndChecking = async (events: ExecutionEvent[]) => {
    setIsProcessing(true);
    setPipelineError(null);
    try {
      setPipelineStep(2); // Matching
      const matchMap: Record<string, CandidateMatch[]> = {};
      await Promise.all(
        events.map(async (ev) => {
          const matchRes = await claimsApi.match(ev.event_id);
          ev.status = matchRes.status;
          if (matchRes.matches) {
            matchMap[ev.event_id] = matchRes.matches;
          }
        })
      );
      setCandidateMatches(matchMap);

      // Load activities map and quality gates for target metadata display
      try {
        const [acts, qgs, impacts] = await Promise.all([
          schedulesApi.getActivities(currentScheduleVersion.id),
          qualityGatesApi.getGates({ scheduleId: currentScheduleVersion.id }),
          impactApi.getScheduleImpacts(currentProject.id, currentScheduleVersion.id).catch(() => []),
        ]);
        const map: Record<string, ScheduleActivity> = {};
        acts.forEach((a) => {
          map[a.activity_id] = a;
        });
        setActivitiesMap(map);
        setScheduleQualityGates(qgs);
        setScheduleImpacts(impacts || []);
      } catch {
        // ignore error
      }

      setPipelineStep(3); // Checking
      await Promise.all(
        events.map(async (ev) => {
          const checkRes = await claimsApi.check(ev.event_id);
          ev.status = checkRes.status;
        })
      );

      setPipelineStep(4); // Complete
      // Refresh events from backend to capture updated matched_activity_id and check state
      const refreshedEvents = await Promise.all(
        events.map(async (ev) => {
          try {
            const fresh = await claimsApi.getEvent(ev.event_id);
            return { ...ev, ...fresh };
          } catch {
            return ev;
          }
        })
      );
      setCreatedEvents(refreshedEvents);
    } catch (err: any) {
      setPipelineError(err.message || t('intake.pipelineFailed'));
    } finally {
      setIsProcessing(false);
      isSubmittingRef.current = false;
    }
  };

  const handleClarifySubmit = async (eventId: string) => {
    const answer = clarificationAnswers[eventId]?.trim();
    if (!answer) {
      setClarifyError((prev) => ({ ...prev, [eventId]: 'Please provide a clarification answer before submitting.' }));
      return;
    }

    try {
      setClarifyingEventId(eventId);
      setClarifyError((prev) => ({ ...prev, [eventId]: null }));

      const { event: updatedEvent } = await claimsApi.clarify(eventId, answer);

      const updatedList = createdEvents.map((ev) =>
        ev.event_id === eventId
          ? { ...ev, ...updatedEvent, clarification_status: 'ANSWERED' as const, clarification_answer: answer }
          : ev
      );
      setCreatedEvents(updatedList);

      // Check if all pending clarifications are now answered
      const hasPending = updatedList.some((ev) => ev.clarification_status === 'PENDING');
      if (!hasPending) {
        // Resume pipeline: proceed to Matching -> Checking -> Complete!
        await proceedMatchingAndChecking(updatedList);
      }
    } catch (err: any) {
      setClarifyError((prev) => ({
        ...prev,
        [eventId]: err?.message || 'Failed to submit clarification response.',
      }));
    } finally {
      setClarifyingEventId(null);
    }
  };

  // State for Schedule Export Progress Mode
  const [isScheduleExport, setIsScheduleExport] = useState(false);

  // Submit Claim & Execute Pipeline (Intake -> Match -> Check -> Complete)
  const runPipeline = async (claimText: string, file: File | null = null, asScheduleExport = false) => {
    if (isSubmittingRef.current) return;
    isSubmittingRef.current = true;
    setIsProcessing(true);
    setPipelineError(null);
    setCreatedEvents([]);
    setPipelineStep(1); // Submitting (Intake)

    try {
      let events: ExecutionEvent[];
      if (file && activeTab === 'text') {
        const res = await claimsApi.submitText(claimText, file);
        events = [res.event];
      } else if (file && (asScheduleExport || file.name.endsWith('.xer') || file.name.endsWith('.xml'))) {
        const res = await claimsApi.submitScheduleExport(file);
        events = res.events;
      } else if (file) {
        const res = await claimsApi.submitFile(file);
        events = res.events;
      } else {
        const res = await claimsApi.submitText(claimText);
        events = [res.event];
      }

      setCreatedEvents(events);

      // Feature 29: If clarification is required, STOP BEFORE MATCHING
      const hasPending = events.some((ev) => ev.clarification_status === 'PENDING');
      if (hasPending) {
        setPipelineStep(1);
        setIsProcessing(false);
        isSubmittingRef.current = false;
        return;
      }

      // No pending clarification: proceed to Matching -> Checking -> Complete
      await proceedMatchingAndChecking(events);
    } catch (err: any) {
      setPipelineError(err.message || t('intake.pipelineFailed'));
      setIsProcessing(false);
      isSubmittingRef.current = false;
    }
  };

  const handleSubmitText = () => {
    const trimmed = textValue.trim();
    if (trimmed.length < MIN_TEXT_LENGTH) {
      setTextError(t('intake.errTextTooShort'));
      return;
    }
    setTextError(null);
    setEvidenceError(null);

    runPipeline(trimmed, evidenceFile);
  };

  const handleSubmitVoice = () => {
    const trimmed = voiceTranscript.trim();
    if (!trimmed && !audioFile) return;
    runPipeline(trimmed || `Voice upload: ${audioFile?.name}`, audioFile);
  };

  const handleSubmitFile = () => {
    if (!selectedFile) return;
    const isExport = isScheduleExport || selectedFile.name.endsWith('.xer') || selectedFile.name.endsWith('.xml');
    runPipeline(
      isExport ? `P6/MSP Schedule Progress Export: ${selectedFile.name}` : `Uploaded document: ${selectedFile.name}`,
      selectedFile,
      isExport
    );
  };

  const hasPendingClarification = createdEvents.some((ev) => ev.clarification_status === 'PENDING');

  const pipelineSteps = [
    {
      title: t('intake.stepIntakeTitle'),
      desc: hasPendingClarification ? 'Clarification requested from engineer' : t('intake.stepIntakeDesc'),
    },
    {
      title: t('intake.stepMatchingTitle'),
      desc: hasPendingClarification ? 'Paused until clarification answered' : t('intake.stepMatchingDesc'),
    },
    { title: t('intake.stepChecksTitle'), desc: t('intake.stepChecksDesc') },
    { title: t('intake.stepCompleteTitle'), desc: t('intake.stepCompleteDesc') },
  ];

  return (
    <div className="space-y-6 animate-in fade-in duration-200">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="w-8 h-8 rounded-full flex items-center justify-center bg-orange-100 dark:bg-orange-950/60 border border-orange-400/80 text-[#FF7A18] dark:text-[#FF941F] shrink-0 shadow-xs">
          <PlusCircle className="w-5 h-5" />
        </div>
        <div>
          <div className="flex items-center gap-2 text-xs font-mono text-muted-foreground pb-0.5 flex-wrap">
            <span className="flex items-center gap-1 font-bold text-primary">
              <Building2 className="w-3.5 h-3.5 text-[#FF7A18]" />
              {currentProject.name} ({currentProject.code})
            </span>
            <span>·</span>
            <span className="text-[11px] px-2 py-0.2 rounded bg-slate-100 dark:bg-[#0B2742] text-muted-foreground border border-slate-300 dark:border-[#214766]">
              {currentScheduleVersion.versionNumber}
            </span>
          </div>
          <h1 className="text-2xl font-extrabold text-[#071A2D] dark:text-[#F5F7FA] tracking-tight">
            {t('intake.title')}
          </h1>
          <p className="text-[#334155] dark:text-[#CBD5E1] text-xs font-semibold mt-0.5">
            {t('intake.subtitle')}
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* LEFT COLUMN: Input Form (7 Cols) */}
        <div className={cn(activeTab === 'batch' ? 'lg:col-span-12' : 'lg:col-span-7', 'space-y-6')}>
          <Tabs
            value={activeTab}
            onValueChange={(val) => setActiveTab(val as InputTab)}
            className="w-full space-y-6"
          >
            {/* Tab Selector */}
            <TabsList className="grid grid-cols-4 w-full h-12 p-1.5 bg-white/95 dark:bg-[#071A2D]/95 border border-slate-200/80 dark:border-[#214766] rounded-2xl shadow-md backdrop-blur-md">
              <TabsTrigger
                value="batch"
                className="text-xs font-bold gap-2 text-[#071A2D] dark:text-[#C5D2DE] data-[state=active]:bg-gradient-to-r data-[state=active]:from-[#FF7A18] data-[state=active]:to-[#FF941F] data-[state=active]:text-white transition-all rounded-xl h-9 shadow-xs hover:text-[#FF7A18] dark:hover:text-[#F5F7FA]"
              >
                <Files className="w-4 h-4" /> Batch upload
              </TabsTrigger>
              <TabsTrigger
                value="text"
                className="text-xs font-bold gap-2 text-[#071A2D] dark:text-[#C5D2DE] data-[state=active]:bg-gradient-to-r data-[state=active]:from-[#FF7A18] data-[state=active]:to-[#FF941F] data-[state=active]:text-white transition-all rounded-xl h-9 shadow-xs hover:text-[#FF7A18] dark:hover:text-[#F5F7FA]"
              >
                <FileText className="w-4 h-4" /> {t('intake.tabText')}
              </TabsTrigger>
              <TabsTrigger
                value="voice"
                className="text-xs font-bold gap-2 text-[#071A2D] dark:text-[#C5D2DE] data-[state=active]:bg-gradient-to-r data-[state=active]:from-[#FF7A18] data-[state=active]:to-[#FF941F] data-[state=active]:text-white transition-all rounded-xl h-9 shadow-xs hover:text-[#FF7A18] dark:hover:text-[#F5F7FA]"
              >
                <Mic className="w-4 h-4" /> {t('intake.tabVoice')}
              </TabsTrigger>
              <TabsTrigger
                value="file"
                className="text-xs font-bold gap-2 text-[#071A2D] dark:text-[#C5D2DE] data-[state=active]:bg-gradient-to-r data-[state=active]:from-[#FF7A18] data-[state=active]:to-[#FF941F] data-[state=active]:text-white transition-all rounded-xl h-9 shadow-xs hover:text-[#FF7A18] dark:hover:text-[#F5F7FA]"
              >
                <Upload className="w-4 h-4" /> {t('intake.tabFile')}
              </TabsTrigger>
            </TabsList>

            {/* TAB 0: MULTI-FILE BATCH (several reports / files / photos in one operation) */}
            <TabsContent value="batch" className="mt-0 focus-visible:outline-none">
              <BatchUploadPanel />
            </TabsContent>

            {/* TAB 1: TEXT UPDATE */}
            <TabsContent value="text" className="mt-0 focus-visible:outline-none">
              <Card className="border-slate-200/80 dark:border-[#214766] bg-white/95 dark:bg-[#071A2D]/95 shadow-xl rounded-2xl">
                <CardHeader className="p-6 pb-4">
                  <CardTitle className="text-base font-extrabold text-[#071A2D] dark:text-[#F5F7FA]">{t('intake.textCardTitle')}</CardTitle>
                  <CardDescription className="text-[#334155] dark:text-[#C5D2DE] text-xs font-semibold mt-1">
                    {t('intake.textCardDesc')}
                  </CardDescription>
                </CardHeader>
                <CardContent className="p-6 pt-0 space-y-4">
                  <div className="space-y-1.5">
                    <div className="flex items-center justify-between">
                      <Label htmlFor="claim-text-input" className="text-xs font-bold text-[#071A2D] dark:text-[#C5D2DE]">
                        {t('intake.textLabel')}
                      </Label>
                      <span
                        className={cn(
                          'text-xs font-mono font-medium',
                          textValue.length > MAX_TEXT_LENGTH ? 'text-destructive font-bold' : 'text-[#475569] dark:text-[#9FB2C3]'
                        )}
                      >
                        {textValue.length} / {MAX_TEXT_LENGTH}
                      </span>
                    </div>

                    <Textarea
                      id="claim-text-input"
                      rows={5}
                      maxLength={MAX_TEXT_LENGTH}
                      value={textValue}
                      onChange={(e) => {
                        setTextValue(e.target.value);
                        if (textError && e.target.value.trim().length >= MIN_TEXT_LENGTH) {
                          setTextError(null);
                        }
                      }}
                      placeholder="Describe site execution progress in detail..."
                      aria-invalid={!!textError}
                      aria-describedby={textError ? 'claim-text-error' : undefined}
                      className={cn('text-sm rounded-xl p-3.5 bg-white dark:bg-[#0B2742] border-slate-300 dark:border-[#214766] text-[#071A2D] dark:text-[#F5F7FA] placeholder:text-slate-500 dark:placeholder:text-[#8FA6BA] font-medium focus-visible:ring-[#FF7A18] min-h-[140px]', textError && 'border-destructive focus-visible:ring-destructive')}
                    />

                    {textError && (
                      <p id="claim-text-error" role="alert" className="text-xs text-destructive font-semibold">
                        {textError}
                      </p>
                    )}
                  </div>

                  {/* P0-1: Evidence Attachment Section for Typed Claim */}
                  <div className="space-y-2 pt-2 border-t border-slate-200/80 dark:border-[#214766]">
                    <div className="flex items-center justify-between">
                      <Label htmlFor="evidence-file-input" className="text-xs font-bold text-[#071A2D] dark:text-[#C5D2DE] flex items-center gap-1.5">
                        <Paperclip className="w-3.5 h-3.5 text-[#FF7A18]" />
                        <span>Supporting Site Evidence <span className="text-muted-foreground font-normal">(Optional)</span></span>
                      </Label>
                      <span className="text-[10px] text-muted-foreground font-mono">
                        Defaults to placeholder if omitted
                      </span>
                    </div>

                    <input
                      type="file"
                      ref={evidenceInputRef}
                      id="evidence-file-input"
                      accept=".jpg,.jpeg,.png,.pdf,.xlsx,.xls,.csv,.txt,.xer"
                      onChange={(e) => handleEvidenceSelect(e.target.files?.[0] || null)}
                      className="hidden"
                    />

                    {!evidenceFile ? (
                      <div
                        role="button"
                        tabIndex={0}
                        aria-label="Click or drag file to attach supporting site evidence"
                        onClick={() => evidenceInputRef.current?.click()}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault();
                            evidenceInputRef.current?.click();
                          }
                        }}
                        className={cn(
                          'border-2 border-dashed rounded-xl p-4 text-center cursor-pointer transition-all space-y-1.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#FF7A18]',
                          'border-slate-300 dark:border-[#214766] hover:border-[#FF7A18] bg-slate-50/50 dark:bg-[#0A2238]/60'
                        )}
                      >
                        <div className="flex items-center justify-center gap-2 text-slate-400">
                          <Upload className="w-4 h-4 text-[#FF7A18]" />
                          <span className="text-xs text-[#071A2D] dark:text-[#F5F7FA] font-bold">
                            Attach Evidence (Optional: Site Photo, Inspection Report, QC Cert)
                          </span>
                        </div>
                        <div className="text-[10px] text-muted-foreground">
                          Supports JPG, PNG, PDF, CSV, XLSX · Blank 'File not uploaded' placeholder used if omitted
                        </div>
                      </div>
                    ) : (
                      <div className="p-3 rounded-xl border border-orange-500/30 bg-orange-50/5 dark:bg-orange-950/20 space-y-2">
                        <div className="flex items-center justify-between gap-3">
                          <div className="flex items-center gap-2.5 truncate">
                            {evidencePreviewUrl ? (
                              <img
                                src={evidencePreviewUrl}
                                alt="Evidence preview"
                                className="w-10 h-10 rounded-lg object-cover border border-orange-200 dark:border-orange-900/50 shrink-0"
                              />
                            ) : (
                              <div className="w-10 h-10 rounded-lg bg-orange-100 dark:bg-orange-950/60 border border-orange-300 dark:border-orange-800/60 flex items-center justify-center shrink-0 text-[#FF7A18]">
                                <FileText className="w-5 h-5" />
                              </div>
                            )}
                            <div className="truncate">
                              <div className="text-xs font-bold text-[#071A2D] dark:text-[#F5F7FA] truncate">
                                {evidenceFile.name}
                              </div>
                              <div className="text-[10px] text-muted-foreground font-mono">
                                {(evidenceFile.size / 1024).toFixed(1)} KB · {evidenceFile.type || 'Document'}
                              </div>
                            </div>
                          </div>

                          <div className="flex items-center gap-1.5 shrink-0">
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              onClick={() => evidenceInputRef.current?.click()}
                              className="h-7 text-[11px] px-2"
                            >
                              Replace
                            </Button>
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              onClick={removeEvidenceFile}
                              className="h-7 text-[11px] px-2 text-destructive hover:bg-destructive/10"
                            >
                              <X className="w-3.5 h-3.5" />
                            </Button>
                          </div>
                        </div>
                      </div>
                    )}

                    {evidenceError && (
                      <p role="alert" className="text-xs text-destructive font-semibold">
                        {evidenceError}
                      </p>
                    )}
                  </div>

                  <Button
                    onClick={handleSubmitText}
                    disabled={isProcessing || !textValue.trim()}
                    isLoading={isProcessing}
                    className={cn(
                      'w-full font-bold h-12 text-sm shadow-md shadow-orange-500/30 text-white rounded-xl transition-all',
                      !textValue.trim() && !isProcessing
                        ? 'bg-gradient-to-r from-[#FF7A18]/70 to-[#FF941F]/70 hover:from-[#FF7A18]/70 hover:to-[#FF941F]/70 cursor-not-allowed text-white'
                        : 'bg-gradient-to-r from-[#FF7A18] to-[#FF941F] hover:from-[#E06810] hover:to-[#FF7A18] cursor-pointer'
                    )}
                  >
                    {isProcessing ? t('intake.processingClaim') : t('intake.submitText')}
                  </Button>
                </CardContent>
              </Card>
            </TabsContent>

            {/* TAB 2: VOICE INPUT & AUDIO FILE UPLOAD */}
            <TabsContent value="voice" className="mt-0 focus-visible:outline-none">
              <Card className="border-slate-200/80 dark:border-[#214766] bg-white/95 dark:bg-[#071A2D]/95 shadow-xl rounded-2xl">
                <CardHeader className="p-6 pb-4">
                  <CardTitle className="text-base font-extrabold text-[#071A2D] dark:text-[#F5F7FA] flex items-center justify-between">
                    <span>{t('intake.voiceCardTitle')}</span>
                    <span className="text-[10px] bg-primary/10 text-primary border border-primary/30 px-2 py-0.5 rounded-full font-mono font-bold">
                      {t('intake.liveRecognition')}
                    </span>
                  </CardTitle>
                </CardHeader>
                <CardContent className="p-6 pt-0 space-y-5">
                  {speechNotice && (
                    <div
                      role="status"
                      className="flex items-start justify-between gap-3 rounded-xl border border-amber-500/30 bg-amber-500/10 p-3.5 text-xs text-amber-900 dark:text-amber-200"
                    >
                      <div className="flex items-start gap-2.5">
                        <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5 text-amber-600 dark:text-amber-400" />
                        <div className="space-y-0.5">
                          <span className="font-semibold block">{t('intake.speechWarningTitle')}</span>
                          <span className="text-foreground/90">{speechNotice}</span>
                        </div>
                      </div>
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        onClick={() => setSpeechNotice(null)}
                        className="h-7 text-xs text-muted-foreground hover:text-foreground shrink-0"
                      >
                        {t('common.dismiss')}
                      </Button>
                    </div>
                  )}

                  {/* Speech Button */}
                  <div className="flex flex-col items-center justify-center p-6 bg-slate-50 dark:bg-[#0A2238] rounded-xl border border-slate-200 dark:border-[#214766] space-y-3">
                    <button
                      type="button"
                      onClick={toggleRecording}
                      aria-label={isRecording ? 'Stop recording voice claim' : 'Start recording voice claim'}
                      className={cn(
                        'w-16 h-16 rounded-full flex items-center justify-center transition-all shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 cursor-pointer',
                        isRecording
                          ? 'bg-rose-600 text-white animate-pulse'
                          : 'bg-gradient-to-r from-[#FF7A18] to-[#FF941F] hover:from-[#E06810] hover:to-[#FF7A18] text-white shadow-orange-500/25'
                      )}
                    >
                      {isRecording ? <MicOff className="w-8 h-8" /> : <Mic className="w-8 h-8" />}
                    </button>

                    <span className="text-xs text-[#071A2D] dark:text-[#C5D2DE] font-bold">
                      {isRecording ? t('intake.listening') : t('intake.clickToRecord')}
                    </span>
                  </div>

                  {/* Live Transcript Editable Area */}
                  <div className="space-y-1.5">
                    <Label htmlFor="voice-transcript-input" className="text-xs text-[#071A2D] dark:text-[#C5D2DE] font-bold">
                      {t('intake.liveTranscript')}
                    </Label>
                    <Textarea
                      id="voice-transcript-input"
                      rows={4}
                      value={voiceTranscript}
                      onChange={(e) => setVoiceTranscript(e.target.value)}
                      placeholder={t('intake.transcriptPlaceholder')}
                      className="text-xs rounded-xl bg-white dark:bg-[#0B2742] border-slate-300 dark:border-[#214766] text-[#071A2D] dark:text-[#F5F7FA] placeholder:text-slate-500 dark:placeholder:text-[#8FA6BA] font-medium"
                    />
                  </div>

                  {/* Upload Audio File Section */}
                  <div className="pt-3 border-t border-slate-200 dark:border-[#214766] space-y-3">
                    <span className="text-xs font-bold text-[#071A2D] dark:text-[#C5D2DE] block">{t('intake.uploadAudioSection')}</span>
                    <input
                      type="file"
                      ref={audioInputRef}
                      id="audio-file-input"
                      accept="audio/*,.mp3,.wav,.m4a,.ogg,.webm"
                      onChange={handleAudioFileChange}
                      className="hidden"
                    />

                    {!audioFile ? (
                      <Button
                        type="button"
                        variant="outline"
                        onClick={() => audioInputRef.current?.click()}
                        className="w-full text-xs h-10 rounded-xl border-slate-300 dark:border-[#214766] text-[#071A2D] dark:text-[#F5F7FA] dark:bg-[#0A2238] hover:dark:bg-[#0D2942] font-bold"
                      >
                        <FileAudio className="w-4 h-4 mr-2 text-[#FF7A18]" />
                        {t('intake.selectAudioFile')}
                      </Button>
                    ) : (
                      <div className="p-3 bg-slate-50 dark:bg-[#0A2238] rounded-xl border border-slate-200 dark:border-[#214766] space-y-2 text-xs">
                        <div className="flex items-center justify-between">
                          <span className="font-mono font-bold text-[#071A2D] dark:text-[#F5F7FA] truncate">{audioFile.name}</span>
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            onClick={removeAudioFile}
                            className="h-6 text-xs text-destructive hover:bg-destructive/10 cursor-pointer font-bold"
                          >
                            {t('intake.removeAudio')}
                          </Button>
                        </div>
                        {audioUrl && <audio src={audioUrl} controls className="w-full h-8" />}
                      </div>
                    )}
                  </div>

                  <Button
                    onClick={handleSubmitVoice}
                    disabled={isProcessing || (!voiceTranscript.trim() && !audioFile)}
                    isLoading={isProcessing}
                    className={cn(
                      'w-full font-bold h-12 text-sm shadow-md shadow-orange-500/30 text-white rounded-xl transition-all',
                      (!voiceTranscript.trim() && !audioFile) && !isProcessing
                        ? 'bg-gradient-to-r from-[#FF7A18]/70 to-[#FF941F]/70 hover:from-[#FF7A18]/70 hover:to-[#FF941F]/70 cursor-not-allowed text-white'
                        : 'bg-gradient-to-r from-[#FF7A18] to-[#FF941F] hover:from-[#E06810] hover:to-[#FF7A18] cursor-pointer'
                    )}
                  >
                    {isProcessing ? t('intake.processingVoice') : t('intake.submitVoice')}
                  </Button>
                </CardContent>
              </Card>
            </TabsContent>

            {/* TAB 3: FILE UPLOAD & P6/MSP PROGRESS EXPORT */}
            <TabsContent value="file" className="mt-0 focus-visible:outline-none">
              <Card className="border-slate-200/80 dark:border-[#214766] bg-white/95 dark:bg-[#071A2D]/95 shadow-xl rounded-2xl">
                <CardHeader className="p-6 pb-4">
                  <CardTitle className="text-base font-extrabold text-[#071A2D] dark:text-[#F5F7FA]">{t('intake.fileCardTitle')}</CardTitle>
                  <CardDescription className="text-[#334155] dark:text-[#C5D2DE] text-xs font-semibold mt-1">
                    Ingest field progress from documents, scanned logs, or subcontractor P6/MSP schedule export files.
                  </CardDescription>
                </CardHeader>
                <CardContent className="p-6 pt-0 space-y-4">
                  {/* File Intake Categories Guidance */}
                  <div className="p-3 rounded-xl bg-slate-50 dark:bg-[#0A2238] border border-slate-200/80 dark:border-[#214766] space-y-1.5 text-xs">
                    <span className="font-bold text-[#071A2D] dark:text-[#F5F7FA] block text-[11px]">
                      Supported Progress Input Categories:
                    </span>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-[11px] text-muted-foreground">
                      <div className="flex items-center gap-1.5">
                        <FileText className="w-3.5 h-3.5 text-[#FF7A18] shrink-0" />
                        <span>Daily Reports & Diaries (.pdf, .txt, .png)</span>
                      </div>
                      <div className="flex items-center gap-1.5">
                        <Upload className="w-3.5 h-3.5 text-blue-500 shrink-0" />
                        <span>P6 / MSP Progress Exports (.xer, .xml, .csv, .xlsx)</span>
                      </div>
                    </div>
                  </div>

                  <input
                    type="file"
                    ref={fileInputRef}
                    id="doc-file-input"
                    accept=".pdf,.xlsx,.xls,.csv,.txt,.xer,.xml,.jpg,.jpeg,.png"
                    onChange={(e) => handleFileSelect(e.target.files?.[0] || null)}
                    className="hidden"
                  />

                  <div
                    role="button"
                    tabIndex={0}
                    aria-label={selectedFile ? `Selected file: ${selectedFile.name}. Click to change.` : 'Select document file to upload'}
                    onClick={() => fileInputRef.current?.click()}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        fileInputRef.current?.click();
                      }
                    }}
                    className={cn(
                      'border-2 border-dashed rounded-xl p-6 text-center cursor-pointer transition-all space-y-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
                      fileError
                        ? 'border-destructive bg-destructive/5'
                        : selectedFile
                        ? 'border-[#FF7A18] bg-orange-500/5'
                        : 'border-slate-300 dark:border-[#214766] hover:border-[#FF7A18] bg-slate-50/50 dark:bg-[#0A2238]/60'
                    )}
                  >
                    <Upload className={cn('w-7 h-7 mx-auto', selectedFile ? 'text-[#FF7A18]' : 'text-slate-400')} />
                    <div className="text-xs text-[#071A2D] dark:text-[#F5F7FA] font-bold">
                      {selectedFile ? selectedFile.name : t('intake.clickToBrowse')}
                    </div>
                    <div className="text-[10px] text-[#475569] dark:text-[#9FB2C3] font-medium">
                      PDF, XLSX, CSV, TXT, XER (Primavera P6), XML (MS Project), JPG/PNG
                    </div>
                  </div>

                  {/* Schedule Progress Export Mode Selector */}
                  {selectedFile && (
                    <div className="p-3 rounded-xl bg-orange-500/10 border border-orange-500/30 flex items-center justify-between text-xs">
                      <div className="space-y-0.5">
                        <span className="font-bold text-[#071A2D] dark:text-[#F5F7FA] block text-[11px]">
                          P6 / MS Project Export Mode
                        </span>
                        <span className="text-[10px] text-muted-foreground">
                          {selectedFile.name.endsWith('.xer') || selectedFile.name.endsWith('.xml')
                            ? 'Auto-detected P6/MSP file: Rows will be parsed directly as activity progress claims.'
                            : 'Treat tabular rows as structured subcontractor progress claims.'}
                        </span>
                      </div>
                      <label className="flex items-center gap-2 font-bold cursor-pointer text-xs shrink-0 text-primary">
                        <input
                          type="checkbox"
                          checked={isScheduleExport || selectedFile.name.endsWith('.xer') || selectedFile.name.endsWith('.xml')}
                          onChange={(e) => setIsScheduleExport(e.target.checked)}
                          className="w-4 h-4 rounded border-slate-300 text-[#FF7A18] focus:ring-[#FF7A18]"
                        />
                        <span>P6 Export Mode</span>
                      </label>
                    </div>
                  )}

                  {fileError && (
                    <p role="alert" className="text-xs text-destructive font-semibold">
                      {fileError}
                    </p>
                  )}

                  <Button
                    onClick={handleSubmitFile}
                    disabled={isProcessing || !selectedFile}
                    isLoading={isProcessing}
                    className={cn(
                      'w-full font-bold h-12 text-sm shadow-md shadow-orange-500/30 text-white rounded-xl transition-all',
                      !selectedFile && !isProcessing
                        ? 'bg-gradient-to-r from-[#FF7A18]/70 to-[#FF941F]/70 hover:from-[#FF7A18]/70 hover:to-[#FF941F]/70 cursor-not-allowed text-white'
                        : 'bg-gradient-to-r from-[#FF7A18] to-[#FF941F] hover:from-[#E06810] hover:to-[#FF7A18] cursor-pointer'
                    )}
                  >
                    {isProcessing
                      ? t('intake.extractingDocument')
                      : isScheduleExport || selectedFile?.name.endsWith('.xer') || selectedFile?.name.endsWith('.xml')
                      ? 'Ingest Schedule Progress Export Claims'
                      : t('intake.ingestDocument')}
                  </Button>
                </CardContent>
              </Card>
            </TabsContent>
          </Tabs>
        </div>

        {/* RIGHT COLUMN: Pipeline Stepper & Result (5 Cols); the batch tab shows its own full-width result */}
        <div className={cn('lg:col-span-5 space-y-6', activeTab === 'batch' && 'hidden')}>
          <Card className="border-slate-200/80 dark:border-[#214766] bg-white/95 dark:bg-[#071A2D]/95 shadow-xl rounded-2xl">
            <CardHeader className="p-6 pb-4 border-b border-slate-300 dark:border-[#214766]/60">
              <CardTitle className="text-base font-extrabold text-[#071A2D] dark:text-[#F5F7FA] flex items-center justify-between">
                <span>{t('intake.pipelineTitle')}</span>
                {pipelineStep > 0 && (
                  <span className="text-xs font-mono text-[#334155] dark:text-[#9FB2C3] font-bold">
                    {pipelineStep === 4 ? t('intake.stepCompleteTitle') : `${pipelineStep} / 4`}
                  </span>
                )}
              </CardTitle>
            </CardHeader>

            <CardContent className="p-6 space-y-6">
              {pipelineError && (
                <ErrorState
                  title="Submission Pipeline Error"
                  message={pipelineError}
                  onRetry={() => {
                    setPipelineError(null);
                    setPipelineStep(0);
                  }}
                  retryText={t('common.retry')}
                />
              )}

              {/* Stepper Display with vertical connecting line */}
              <div aria-live="polite" className="relative space-y-6">
                {/* Vertical Connector line behind circular badges */}
                <div className="absolute left-4 top-4 bottom-4 w-0.5 bg-slate-200 dark:bg-[#214766] -translate-x-1/2 z-0" />

                {pipelineSteps.map((st, idx) => {
                  const stepNum = idx + 1;
                  const isCurrent = pipelineStep === stepNum;
                  const isCompleted = pipelineStep > stepNum || (pipelineStep === 4 && stepNum === 4);

                  return (
                    <div key={idx} className="relative z-10 flex items-start gap-3.5">
                      <div
                        className={cn(
                          'w-8 h-8 rounded-full flex items-center justify-center font-mono text-xs font-bold shrink-0 transition-all shadow-xs',
                          isCompleted
                            ? 'bg-[#10B981] text-white'
                            : isCurrent
                            ? 'bg-[#0284C7] text-white shadow-md shadow-blue-500/30 animate-pulse'
                            : 'bg-[#0284C7] text-white'
                        )}
                      >
                        {isCompleted ? <CheckCircle2 className="w-4 h-4" /> : stepNum}
                      </div>

                      <div className="flex-1 pt-0.5">
                        <div
                          className={cn(
                            'text-xs font-extrabold leading-tight',
                            isCurrent
                              ? 'text-[#0284C7] dark:text-[#38BDF8]'
                              : isCompleted
                              ? 'text-[#071A2D] dark:text-[#F5F7FA]'
                              : 'text-[#071A2D] dark:text-[#F5F7FA]'
                          )}
                        >
                          {st.title}
                        </div>
                        <div className="text-[11px] text-[#334155] dark:text-[#C5D2DE] font-semibold mt-0.5">{st.desc}</div>
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* Created Events Result Card with Trust Framing */}
              {createdEvents.length > 0 && (
                <div
                  className={cn(
                    'p-4 rounded-xl border text-foreground space-y-3 animate-in fade-in duration-300',
                    hasPendingClarification
                      ? 'bg-amber-500/10 border-amber-500/30'
                      : 'bg-status-approved/10 dark:bg-emerald-950/30 border-status-approved/30 dark:border-emerald-800/40'
                  )}
                >
                  <div
                    className={cn(
                      'flex items-start gap-2 text-xs font-bold',
                      hasPendingClarification ? 'text-amber-600 dark:text-amber-400' : 'text-status-approved'
                    )}
                  >
                    {hasPendingClarification ? (
                      <Sparkles className="w-4 h-4 shrink-0 mt-0.5 text-amber-500" />
                    ) : (
                      <CheckCircle2 className="w-4 h-4 shrink-0 mt-0.5" />
                    )}
                    <div>
                      <div>
                        {hasPendingClarification ? 'Clarification Required Before Matching' : t('intake.claimSubmitted')}
                      </div>
                      <div className="text-[11px] font-normal text-muted-foreground mt-0.5">
                        {hasPendingClarification
                          ? 'Copilot needs specific missing information before proceeding to activity matching.'
                          : createdEvents.length === 1
                          ? t('intake.oneClaim')
                          : t('intake.nClaims', { count: createdEvents.length })}
                      </div>
                    </div>
                  </div>

                  <p className="text-[11px] text-muted-foreground italic border-t border-border/40 pt-2">
                    {t('intake.trustNote')}
                  </p>

                  <div className="space-y-3 max-h-72 overflow-y-auto divide-y divide-border/40">
                    {createdEvents.map((ev) => (
                      <div key={ev.event_id} className="pt-2 space-y-2 text-xs">
                        <div className="flex items-center justify-between gap-2">
                          <div className="truncate">
                            {ev.reported_activity_id && (
                              <span className="font-mono text-accent font-semibold mr-1.5">
                                {ev.reported_activity_id}
                              </span>
                            )}
                            <span className="font-mono font-bold text-[#071A2D] dark:text-[#F5F7FA]">{ev.event_id}</span>
                          </div>
                          <StatusBadge status={ev.status} size="sm" />
                        </div>

                        {/* Read-only Quality Gate / Hold Point Context */}
                        {(() => {
                          const targetActId = ev.matched_activity_id || ev.reported_activity_id || candidateMatches[ev.event_id]?.[0]?.activity_id;
                          if (!targetActId) return null;
                          const targetGates = scheduleQualityGates.filter((g) => g.activityId === targetActId);
                          if (targetGates.length === 0) return null;
                          const completedCount = targetGates.filter((g) => g.status === 'COMPLETED' || g.status === 'WAIVED').length;
                          const hasHoldPoint = targetGates.some((g) => g.gateType === 'HOLD_POINT' && g.required && (g.status === 'PENDING' || g.status === 'BLOCKED'));

                          return (
                            <div className="p-2 rounded-lg bg-slate-50 dark:bg-[#0B2742] border border-slate-200 dark:border-slate-800 flex items-center justify-between text-[11px]">
                              <div className="flex items-center gap-1.5">
                                <ShieldAlert className={cn("w-3.5 h-3.5", hasHoldPoint ? "text-rose-500" : "text-teal-500")} />
                                <span className="font-semibold text-foreground">
                                  Quality Gates: {completedCount}/{targetGates.length} Cleared
                                </span>
                              </div>
                              {hasHoldPoint && (
                                <span className="text-[10px] font-bold text-rose-600 dark:text-rose-400">
                                  Hold Point Pending
                                </span>
                              )}
                            </div>
                          );
                        })()}

                        {/* Read-only Downstream Impact Indicator */}
                        {(() => {
                          const targetActId = ev.matched_activity_id || ev.reported_activity_id || candidateMatches[ev.event_id]?.[0]?.activity_id;
                          if (!targetActId) return null;
                          const imp = scheduleImpacts.find((i) => i.activityId === targetActId);
                          if (!imp || (imp.impactLevel === 'LOW' && imp.totalDownstreamCount === 0)) return null;

                          return (
                            <div className="p-2 rounded-lg bg-amber-500/5 dark:bg-[#0B2742] border border-amber-300/40 dark:border-amber-900/40 flex items-center justify-between text-[11px]">
                              <div className="flex items-center gap-1.5">
                                <GitFork className="w-3.5 h-3.5 text-amber-500 shrink-0" />
                                <span className="font-semibold text-foreground">
                                  Downstream Impact: {imp.impactLevel} ({imp.totalDownstreamCount} activities affected)
                                </span>
                              </div>
                              <span className="text-[10px] text-muted-foreground font-mono">
                                {imp.directSuccessorCount} Direct Successors
                              </span>
                            </div>
                          );
                        })()}

                        {/* Feature 29: Adaptive Field Copilot — Clarification Prompt */}
                        {ev.clarification_status === 'PENDING' || ev.clarification_question ? (
                          <div className="p-3 rounded-lg bg-amber-500/10 border border-amber-500/30 space-y-2.5">
                            <div className="flex items-center gap-1.5 text-amber-700 dark:text-amber-300 font-semibold text-[11px]">
                              <Sparkles className="w-3.5 h-3.5 text-amber-500" />
                              <span>
                                {ev.clarification_status === 'PENDING'
                                  ? 'Clarification Requested by Copilot'
                                  : 'Adaptive Copilot Clarification'}
                              </span>
                            </div>

                            <p className="text-xs text-foreground/90 font-medium">
                              {ev.clarification_question || 'Additional context needed to validate claim specifics.'}
                            </p>

                            {ev.clarification_status === 'ANSWERED' || ev.clarification_status === 'RESOLVED' ? (
                              <div className="flex items-center gap-1.5 text-[11px] text-emerald-600 dark:text-emerald-400 font-medium bg-emerald-50 dark:bg-emerald-950/40 p-2 rounded-md border border-emerald-200 dark:border-emerald-900/50">
                                <CheckCircle2 className="w-3.5 h-3.5" />
                                <span>Clarification answered: &ldquo;{ev.clarification_answer}&rdquo;</span>
                              </div>
                            ) : (
                              <div className="space-y-2">
                                <Textarea
                                  rows={2}
                                  placeholder="Type clarification response (e.g. verified scope, drawing ref)..."
                                  value={clarificationAnswers[ev.event_id] || ''}
                                  onChange={(e) =>
                                    setClarificationAnswers((prev) => ({
                                      ...prev,
                                      [ev.event_id]: e.target.value,
                                    }))
                                  }
                                  className="text-xs rounded-xl bg-white dark:bg-[#0B2742] border-slate-300 dark:border-[#214766] text-[#071A2D] dark:text-[#F5F7FA] placeholder:text-slate-500 dark:placeholder:text-[#8FA6BA]"
                                />

                                {clarifyError[ev.event_id] && (
                                  <p className="text-[10px] text-destructive font-medium">
                                    {clarifyError[ev.event_id]}
                                  </p>
                                )}

                                <Button
                                  type="button"
                                  size="sm"
                                  onClick={() => handleClarifySubmit(ev.event_id)}
                                  disabled={clarifyingEventId === ev.event_id || !clarificationAnswers[ev.event_id]?.trim()}
                                  isLoading={clarifyingEventId === ev.event_id}
                                  className="w-full text-xs font-semibold h-8 gap-1.5"
                                >
                                  <Send className="w-3 h-3" />
                                  Submit Clarification
                                </Button>
                              </div>
                            )}
                          </div>
                        ) : null}
                        {/* Completed Activity Protection & Exception Flow */}
                        {(() => {
                          const matches = candidateMatches[ev.event_id] || [];
                          const topMatch = matches[0];
                          const isCompletedProtected =
                            topMatch?.is_completed_protected ||
                            topMatch?.match_tier === 'COMPLETED_PROTECTED' ||
                            ev.is_completed_activity_target;
                          const targetActId = topMatch?.activity_id || ev.reported_activity_id || ev.matched_activity_id;
                          const targetAct = (targetActId ? activitiesMap[targetActId] : null) || (targetActId ? ({
                            activity_id: targetActId,
                            schedule_id: currentScheduleVersion.id,
                            activity_name: topMatch?.supporting_signals?.split('.')[0] || 'Completed Milestone Package',
                            discipline: ev.discipline || 'CIVIL',
                            location: ev.location || 'Site Area',
                            wbs_code: topMatch?.wbs_code || 'WBS-1.0',
                            planned_start: '2026-09-01',
                            planned_finish: '2026-09-08',
                            actual_start: '2026-09-01',
                            actual_finish: '2026-09-08',
                            planned_quantity: null,
                            uom: null,
                            baseline_pct_complete: 100,
                            actual_pct_complete: 100,
                            execution_state: 'COMPLETED' as ExecutionState,
                          } as ScheduleActivity) : null);

                          if (!isCompletedProtected || !targetAct) return null;

                          const isAlreadyRequested =
                            reopenedActivityIds.has(targetAct.activity_id) ||
                            targetAct.execution_state === 'REOPEN_REQUESTED';

                          return (
                            <div className="p-3.5 rounded-xl border border-purple-300 dark:border-purple-800/80 bg-purple-500/10 dark:bg-purple-950/30 space-y-3">
                              <div className="flex items-center justify-between">
                                <div className="flex items-center gap-2 text-purple-700 dark:text-purple-300 font-bold text-xs">
                                  <ShieldAlert className="w-4 h-4 text-purple-600 dark:text-purple-400 shrink-0" />
                                  <span>Activity Already Completed & Protected</span>
                                </div>
                                <ExecutionStateBadge state={targetAct.execution_state || 'COMPLETED'} size="sm" />
                              </div>

                              <p className="text-xs text-foreground/90 font-medium leading-relaxed">
                                This claim targets <strong className="text-purple-700 dark:text-purple-300 font-mono">{targetAct.activity_id}</strong> ({targetAct.activity_name}), which is already marked <strong>COMPLETED (100%)</strong>. To protect authoritative schedule actuals from inadvertent corruption, ordinary claims cannot overwrite completed activities without a Supervisor-approved reopen.
                              </p>

                              {/* Locked Actuals Summary Box */}
                              <div className="p-2.5 rounded-lg bg-white/80 dark:bg-[#0B2742]/80 border border-purple-200 dark:border-purple-900/50 space-y-2 text-[11px]">
                                <div className="flex items-center gap-1.5 text-purple-800 dark:text-purple-300 font-semibold">
                                  <Lock className="w-3.5 h-3.5 text-purple-600 dark:text-purple-400" />
                                  <span>Authoritative Locked Actuals Summary</span>
                                </div>
                                <div className="grid grid-cols-3 gap-2 text-center">
                                  <div className="p-1.5 rounded bg-slate-50 dark:bg-[#081E33] border border-slate-200 dark:border-slate-800">
                                    <span className="text-muted-foreground block text-[10px]">Actual Start</span>
                                    <span className="font-mono font-medium text-foreground">{targetAct.actual_start || targetAct.planned_start || '2026-09-01'}</span>
                                  </div>
                                  <div className="p-1.5 rounded bg-slate-50 dark:bg-[#081E33] border border-slate-200 dark:border-slate-800">
                                    <span className="text-muted-foreground block text-[10px]">Actual Finish</span>
                                    <span className="font-mono font-medium text-foreground">{targetAct.actual_finish || targetAct.planned_finish || '2026-09-08'}</span>
                                  </div>
                                  <div className="p-1.5 rounded bg-slate-50 dark:bg-[#081E33] border border-slate-200 dark:border-slate-800">
                                    <span className="text-muted-foreground block text-[10px]">Completion</span>
                                    <span className="font-mono font-bold text-emerald-600 dark:text-emerald-400">100%</span>
                                  </div>
                                </div>
                              </div>

                              {isAlreadyRequested ? (
                                <div className="p-2.5 rounded-lg bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-300 dark:border-emerald-800 flex items-center gap-2 text-xs text-emerald-700 dark:text-emerald-300 font-semibold">
                                  <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0" />
                                  <span>Reopen request submitted for {targetAct.activity_id}. Status transitioned to REOPEN_REQUESTED pending Supervisor approval.</span>
                                </div>
                              ) : (
                                <Button
                                  type="button"
                                  size="sm"
                                  onClick={() => {
                                    setReopenModalActivity(targetAct);
                                    setReopenModalEventId(ev.event_id);
                                  }}
                                  className="w-full text-xs font-bold h-8.5 bg-gradient-to-r from-purple-600 to-indigo-600 hover:from-purple-700 hover:to-indigo-700 text-white shadow-xs gap-1.5 cursor-pointer"
                                >
                                  <Unlock className="w-3.5 h-3.5" />
                                  Request Activity Reopen from Supervisor
                                </Button>
                              )}
                            </div>
                          );
                        })()}
                      </div>
                    ))}
                  </div>

                  <Button
                    type="button"
                    variant="outline"
                    onClick={handleResetForm}
                    className="w-full text-xs font-bold h-9 mt-2 gap-1.5 border-slate-300 dark:border-[#214766] text-[#071A2D] dark:text-[#F5F7FA] dark:bg-[#0A2238] hover:dark:bg-[#0D2942]"
                  >
                    <RotateCcw className="w-3.5 h-3.5" />
                    {t('intake.submitAnother')}
                  </Button>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>

      {/* Reopen Request Modal */}
      {reopenModalActivity && (
        <ReopenRequestModal
          activity={reopenModalActivity}
          eventId={reopenModalEventId}
          isOpen={!!reopenModalActivity}
          onClose={() => {
            setReopenModalActivity(null);
            setReopenModalEventId(undefined);
          }}
          onSuccess={(req) => {
            setReopenedActivityIds((prev) => new Set(prev).add(req.activity_id));
            setActivitiesMap((prev) => ({
              ...prev,
              [req.activity_id]: {
                ...prev[req.activity_id],
                execution_state: 'REOPEN_REQUESTED',
              },
            }));
          }}
        />
      )}
    </div>
  );
}