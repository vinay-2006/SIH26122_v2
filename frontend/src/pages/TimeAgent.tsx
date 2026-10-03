import React, { useState, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import {
  Mic,
  MicOff,
  Send,
  Sparkles,
  Bot,
  User,
  Clock,
  CheckCircle2,
  AlertTriangle,
  PlayCircle,
  Flag,
  RotateCcw,
  ExternalLink,
  ShieldCheck,
  Activity,
  Layers,
  FileText,
  Zap,
  HardHat,
  ArrowRight,
  TrendingUp,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { StatusBadge } from '@/components/StatusBadge';
import {
  claimsApi,
  schedulesApi,
  dashboardApi,
  timeAgentApi,
  ExecutionEvent,
  ScheduleActivity,
  EventType,
  Discipline,
} from '@/api';
import { cn } from '@/lib/utils';
import { useAuth } from '@/auth/AuthProvider';
import { useProject } from '@/context/ProjectContext';
import { IS_V2 } from '@/config';
import { Building2 } from 'lucide-react';

interface ActionLink {
  label: string;
  to: string;
  variant?: 'default' | 'outline';
}

interface ChatMessage {
  id: string;
  sender: 'user' | 'agent';
  timestamp: string;
  text: string;
  suggestedClaim?: {
    rawText: string;
    reportedActivityId: string | null;
    eventType: EventType;
    discipline: Discipline | null;
    claimedPct: number | null;
    claimedQuantity: number | null;
    claimedUom: string | null;
    location: string | null;
    remarks: string | null;
    evidenceRef: string | null;
    eventDate: string;
  };
  actionLinks?: ActionLink[];
  submittedEvent?: ExecutionEvent;
  handedOff?: boolean;
  isSubmitting?: boolean;
}

// ── Quick Actions for SITE_ENGINEER (Field Reporting Focus) ───────────────────
const SITE_ENGINEER_QUICK_ACTIONS = [
  {
    icon: PlayCircle,
    color: 'text-emerald-600 dark:text-emerald-400',
    label: 'Log Activity Start',
    prompt: 'Foundation pouring for F-4 started at 9:15 AM today in Block-2.',
  },
  {
    icon: Flag,
    color: 'text-blue-600 dark:text-blue-400',
    label: 'Log Activity Finish',
    prompt: 'Mark ACT-401 SCADA Panel installation as 100% finished and energized today.',
  },
  {
    icon: Activity,
    color: 'text-amber-600 dark:text-amber-400',
    label: 'Report Daily Progress',
    prompt: 'Report 75% daily progress on ACT-202 Column C4 rebar placement in Block-2.',
  },
  {
    icon: Layers,
    color: 'text-indigo-600 dark:text-indigo-400',
    label: 'Report Quantity Completed',
    prompt: 'Completed 45 cu.m of concrete pouring for Pump Foundation PS3-FND-001.',
  },
  {
    icon: AlertTriangle,
    color: 'text-rose-600 dark:text-rose-400',
    label: 'Add Field Remark',
    prompt: 'Field Remark: Heavy rain between 2 PM and 4 PM caused temporary work stoppage at Zone B.',
  },
  {
    icon: FileText,
    color: 'text-cyan-600 dark:text-cyan-400',
    label: 'Attach / Reference Evidence',
    prompt: 'Attach evidence reference: Batching plant delivery slip #BP-9821 for Foundation F-4.',
  },
];

// ── Quick Actions for SUPERVISOR (Monitoring & Decision Support Focus) ────────
const SUPERVISOR_QUICK_ACTIONS = [
  {
    icon: PlayCircle,
    color: 'text-emerald-600 dark:text-emerald-400',
    label: 'Log Activity Start',
    prompt: 'Log activity start for Pump Foundation F-4 (CIV-PS3-FND-001) at 09:00 AM today.',
  },
  {
    icon: Flag,
    color: 'text-blue-600 dark:text-blue-400',
    label: 'Log Activity Finish',
    prompt: 'Mark ACT-401 SCADA Panel installation as 100% finished and energized today.',
  },
  {
    icon: ShieldCheck,
    color: 'text-amber-600 dark:text-amber-400',
    label: 'Pending Reviews',
    prompt: 'Which claims are waiting for my review in the queue?',
  },
  {
    icon: Clock,
    color: 'text-rose-600 dark:text-rose-400',
    label: 'Delayed Activities',
    prompt: 'What activities are currently delayed or facing blockers?',
  },
  {
    icon: AlertTriangle,
    color: 'text-orange-600 dark:text-orange-400',
    label: 'Unmatched / New Activities',
    prompt: 'Show me unmatched and new scope activities requiring planner binding.',
  },
  {
    icon: TrendingUp,
    color: 'text-emerald-600 dark:text-emerald-400',
    label: 'Project Progress',
    prompt: 'What is today\'s overall project progress and completion rate?',
  },
  {
    icon: Zap,
    color: 'text-purple-600 dark:text-purple-400',
    label: 'Critical / At-Risk Activities',
    prompt: 'Show critical path activities at risk with zero float.',
  },
  {
    icon: Sparkles,
    color: 'text-blue-600 dark:text-blue-400',
    label: 'Today\'s Execution Summary',
    prompt: 'Synthesize today\'s execution summary across all disciplines.',
  },
];

export default function TimeAgent() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const { currentProject, currentScheduleVersion } = useProject();
  const isSupervisor = user?.role === 'SUPERVISOR';

  const initialGreeting = isSupervisor
    ? `Hello Supervisor (${user?.full_name || 'Planner'}). I am the Setu AI Time Agent. I provide real-time schedule monitoring, delay analytics, unmatched scope identification, and review queue oversight. How can I assist your supervisory decisions today?`
    : `Hello Site Engineer (${user?.full_name || 'Field Engineer'}). I am the Setu AI Time Agent. You can speak or type to log activity starts, completions, daily percentages, quantities, or field remarks. I will structure candidate claims for Supervisor Review.`;

  const [messages, setMessages] = useState<ChatMessage[]>(() => [
    {
      id: 'welcome-msg',
      sender: 'agent',
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      text: initialGreeting,
      actionLinks: isSupervisor
        ? [
            { label: 'Review Workspace', to: '/review' },
            { label: 'Daily Digest', to: '/digest' },
            { label: 'Project Dashboard', to: '/dashboard' },
          ]
        : undefined,
    },
  ]);

  const [inputText, setInputText] = useState('');
  const [isListening, setIsListening] = useState(false);
  const [speechError, setSpeechError] = useState<string | null>(null);
  const [activities, setActivities] = useState<ScheduleActivity[]>([]);
  const [isAgentThinking, setIsAgentThinking] = useState(false);

  const recognitionRef = useRef<any>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Sync greeting when role is resolved
  useEffect(() => {
    setMessages((prev) => {
      if (prev.length === 1 && prev[0].id === 'welcome-msg') {
        return [
          {
            id: 'welcome-msg',
            sender: 'agent',
            timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
            text: isSupervisor
              ? `Hello Supervisor (${user?.full_name || 'Supervisor'}). I am the Setu AI Time Agent. I provide real-time schedule monitoring, delay analytics, unmatched scope identification, and review queue oversight. How can I assist your supervisory decisions today?`
              : `Hello Site Engineer (${user?.full_name || 'Site Engineer'}). I am the Setu AI Time Agent. You can speak or type to log activity starts, completions, daily percentages, quantities, or field remarks. I will structure candidate claims for Supervisor Review.`,
            actionLinks: isSupervisor
              ? [
                  { label: 'Review Workspace', to: '/review' },
                  { label: 'Daily Digest', to: '/digest' },
                  { label: 'Project Dashboard', to: '/dashboard' },
                ]
              : undefined,
          },
        ];
      }
      return prev;
    });
  }, [isSupervisor, user?.full_name]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isAgentThinking]);

  useEffect(() => {
    schedulesApi.getActivities(currentScheduleVersion.id).then(setActivities).catch(() => []);
  }, [currentScheduleVersion.id, currentProject.id]);

  // Web Speech API Initialization
  useEffect(() => {
    const SpeechRecognition =
      (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;

    if (SpeechRecognition) {
      const recognition = new SpeechRecognition();
      recognition.continuous = false;
      recognition.interimResults = true;
      recognition.lang = 'en-US';

      recognition.onresult = (event: any) => {
        let transcript = '';
        for (let i = 0; i < event.results.length; i++) {
          transcript += event.results[i][0].transcript;
        }
        setInputText(transcript);
      };

      recognition.onerror = (err: any) => {
        setSpeechError(
          err.error === 'not-allowed'
            ? 'Microphone permission was denied. Please allow microphone access in your browser settings.'
            : `Voice recognition error (${err.error}).`
        );
        setIsListening(false);
      };

      recognition.onend = () => {
        setIsListening(false);
      };

      recognitionRef.current = recognition;
    }

    return () => {
      if (recognitionRef.current) {
        try {
          recognitionRef.current.stop();
        } catch {
          // ignore
        }
      }
    };
  }, []);

  const toggleVoice = () => {
    if (!recognitionRef.current) {
      setSpeechError('Speech recognition is not supported in this browser. Please type your message.');
      return;
    }

    if (isListening) {
      recognitionRef.current.stop();
      setIsListening(false);
    } else {
      setSpeechError(null);
      try {
        recognitionRef.current.start();
        setIsListening(true);
      } catch (e: any) {
        setSpeechError(`Could not activate microphone: ${e.message}`);
      }
    }
  };

  // ── Field Intent & Entity Parser ───────────────────────────────────────────
  const parseClaimIntent = (text: string) => {
    const lower = text.toLowerCase();
    const todayStr = new Date().toISOString().split('T')[0];

    // Percentage extraction
    let claimedPct: number | null = null;
    const pctMatch = text.match(/(\d+(?:\.\d+)?)\s*%/);
    if (pctMatch) {
      claimedPct = Math.min(100, Math.max(0, parseFloat(pctMatch[1])));
    }

    // Quantity extraction
    let claimedQuantity: number | null = null;
    let claimedUom: string | null = null;
    const qtyMatch = text.match(/(\d+(?:\.\d+)?)\s*(cu\.m|m3|joints?|meters?|m|tons?|nos?|units?|sq\.m)/i);
    if (qtyMatch) {
      claimedQuantity = parseFloat(qtyMatch[1]);
      claimedUom = qtyMatch[2];
    }

    const isStart = /\b(start|started|starting|commenced|commence|began|begin)\b/i.test(lower);
    const isExplicitFinish =
      /\b(finish|finished|finalized|ended|energiz|handed over|commissioned)\b/i.test(lower) ||
      (/\b(completed|complete|done)\b/i.test(lower) && (claimedPct === null || claimedPct === 100) && claimedQuantity === null);
    const isDelay = /\b(delay|delayed|blocked|hold|stuck|waiting|issue|problem|stop|stoppage)\b/i.test(lower);
    const isRemark = /\b(remark|note|weather|rain|incident|stoppage|issue)\b/i.test(lower);

    let eventType: EventType = 'PROGRESS_UPDATE';
    if (claimedPct !== null && claimedPct < 100) {
      if (isDelay && !isStart && claimedPct === 0) {
        eventType = 'DELAY';
      } else {
        eventType = 'PROGRESS_UPDATE';
      }
    } else if (isExplicitFinish || claimedPct === 100) {
      eventType = 'ACTUAL_FINISH';
      if (claimedPct === null) claimedPct = 100;
    } else if (isStart) {
      eventType = 'ACTUAL_START';
      if (claimedPct === null) claimedPct = 10;
    } else if (isDelay) {
      eventType = 'DELAY';
    }

    // Activity matching heuristics
    let detectedActId: string | null = null;
    const actMatch = text.match(/\b(ACT-\d+|CIV-[A-Z0-9-]+|PIP-[A-Z0-9-]+|ELE-[A-Z0-9-]+|INS-[A-Z0-9-]+|F-\d+)\b/i);
    if (actMatch) {
      detectedActId = actMatch[1].toUpperCase();
    } else {
      const found = activities.find((a) =>
        lower.includes(a.activity_name.toLowerCase()) ||
        (a.asset_tag && lower.includes(a.asset_tag.toLowerCase()))
      );
      if (found) detectedActId = found.activity_id;
    }

    // Discipline detection
    let discipline: Discipline | null = null;
    if (/\b(concrete|foundation|rebar|pour|civil|excavation|earthwork|column|f-4)\b/i.test(lower)) {
      discipline = 'CIVIL';
    } else if (/\b(weld|pipe|piping|valve|flange|spool|pipeline|hydrotest)\b/i.test(lower)) {
      discipline = 'PIPING';
    } else if (/\b(cable|transformer|panel|energiz|power|switchgear|electrical)\b/i.test(lower)) {
      discipline = 'ELECTRICAL';
    } else if (/\b(scada|transmitter|loop test|calibration|sensor|instrumentation)\b/i.test(lower)) {
      discipline = 'INSTRUMENTATION';
    } else if (/\b(pump|compressor|skid|turbine|generator|rotating|static)\b/i.test(lower)) {
      discipline = 'STATIC_ROTATING_EQUIPMENT';
    } else if (/\b(safety|hse|permit|ppe|hazard|spill)\b/i.test(lower)) {
      discipline = 'HSE';
    }

    // Location detection
    let location: string | null = null;
    const locMatch = text.match(/\b(Block[-\s]\w+|Pump Station \d+|Unit \w+|Area \w+|Zone \w+|Control Room)\b/i);
    if (locMatch) {
      location = locMatch[1];
    }

    // Evidence reference extraction
    let evidenceRef: string | null = null;
    const evMatch = text.match(/(?:evidence|delivery slip|ticket|dpr|report|slip|#)\s*[:#]?\s*([A-Za-z0-9-#]+)/i);
    if (evMatch) {
      evidenceRef = evMatch[1];
    }

    // Remarks extraction
    let remarks: string | null = null;
    if (isRemark || text.length > 30) {
      remarks = text;
    }

    const isClaimLog = isStart || isExplicitFinish || claimedPct !== null || claimedQuantity !== null || detectedActId !== null || evidenceRef !== null;

    return {
      isClaimLog,
      claim: {
        rawText: text,
        reportedActivityId: detectedActId,
        eventType,
        discipline,
        claimedPct,
        claimedQuantity,
        claimedUom,
        location,
        remarks,
        evidenceRef,
        eventDate: todayStr,
      },
    };
  };

  const handleSendMessage = async (customText?: string) => {
    const text = (customText || inputText).trim();
    if (!text) return;

    if (!customText) setInputText('');

    const userMsg: ChatMessage = {
      id: `user-${Date.now()}`,
      sender: 'user',
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      text,
    };

    setMessages((prev) => [...prev, userMsg]);
    setIsAgentThinking(true);

    setTimeout(async () => {
      const lower = text.toLowerCase();
      let replyText = '';
      let suggestedClaim = undefined;
      let actionLinks: ActionLink[] | undefined = undefined;

      // ── SUPERVISOR-SPECIFIC DECISION SUPPORT RESPONSES ───────────────────────
      if (isSupervisor) {
        if (lower.includes('pending') || lower.includes('review') || lower.includes('queue') || lower.includes('waiting')) {
          replyText = `You currently have 4 field claims waiting in your Supervisor Review Queue:\n\n• EV-101: Actual Start on Pump Foundation F-4 (Civil, Site Engineer)\n• EV-102: Progress Update 75% on Column C4 Rebar (Civil)\n• EV-103: Progress Update on SCADA Loop Calibration (Instrumentation)\n• EV-104: Unmatched Claim — High-Pressure Flange Welding (Piping Subcontractor)\n\nAll candidate actuals are held in candidate state pending your authoritative sign-off.`;
          actionLinks = [
            { label: 'Open Review Workspace', to: '/review' },
            { label: 'Open Daily Digest', to: '/digest' },
          ];
        } else if (lower.includes('delayed') || lower.includes('delay') || lower.includes('blocker')) {
          replyText = `Schedule Telemetry & Delay Analysis:\n\n• 4 activities currently exhibit schedule variance risks.\n• Primary delay driver: Monsoon rains (+2 days) and vendor calibration certificate lead times (+3 days).\n• Critical at-risk: PIP-PS3-WLD-024 (Utility Header Tie-In) with 0 days Total Float.\n• Mitigations: Reallocate crew from civil batching to pre-fabrication shelter.`;
          actionLinks = [
            { label: 'Open Impact Preview', to: '/impact' },
            { label: 'Open Project Dashboard', to: '/dashboard' },
          ];
        } else if (lower.includes('unmatched') || lower.includes('new act') || lower.includes('new scope') || lower.includes('binding')) {
          replyText = `Unmatched & New Scope Governance:\n\n• Found 2 field progress claims without direct baseline schedule bindings:\n  1. EV-104: "High-Pressure Header Flange Torqueing" (Piping Subcontractor)\n  2. EV-105: "Emergency Drainage Trench Excavation" (Zone C)\n\nAs Supervisor performing the Planner-Review function, you can bind these claims to existing WBS schedule activities or split them into new milestone work packages in the Review Workspace.`;
          actionLinks = [
            { label: 'Review & Bind Unmatched Scope', to: '/review?tab=unmatched' },
            { label: 'Explore WBS Structure', to: '/wbs' },
          ];
        } else if (lower.includes('progress') || lower.includes('completion') || lower.includes('rate') || lower.includes('overall')) {
          replyText = `Overall Project Progress Snapshot:\n\n• Total Scheduled Activities: 32 (8 Completed, 16 In Progress, 8 Not Started)\n• Average Progress: 68.4% across active packages\n• Approved Actuals Staged: 18 records prepared for Primavera P6/PMIS sync\n• Active Quality Conflicts: 2 open variance alerts`;
          actionLinks = [
            { label: 'Open Daily Digest', to: '/digest' },
            { label: 'Open Project Dashboard', to: '/dashboard' },
          ];
        } else if (lower.includes('critical') || lower.includes('float') || lower.includes('at-risk') || lower.includes('risk')) {
          replyText = `Critical Path & Precedence Impact:\n\n• CIV-PS3-FND-001 (Pump Foundation Blinding) has 0 days Total Float. Predecessor to skid erection.\n• PIP-PS3-WLD-024 (Utility Header) has -2 days negative float if rain delays persist.\n• Downstream ripple: SCADA energization (ACT-401) will slip by 4 days if pump blinding is not approved by Friday.`;
          actionLinks = [
            { label: 'Open Precedence Impact Graph', to: '/impact' },
            { label: 'View WBS Activity Explorer', to: '/wbs' },
          ];
        } else if (lower.includes('summary') || lower.includes('synthesis') || lower.includes('today\'s execution')) {
          replyText = `Today's Executive Synthesis:\n\n• 6 field progress claims submitted today across Civil, Piping, and Electrical.\n• 4 claims reviewed and approved by Supervisor.\n• 0 safety/HSE incident flags recorded.\n\nNote: For the formal multi-lingual synthesized periodic report with historical trend ratios, please visit the dedicated AI Execution Summary.`;
          actionLinks = [
            { label: 'View AI Execution Summary', to: '/summary' },
            { label: 'Open Daily Digest', to: '/digest' },
          ];
        } else {
          // If supervisor explicitly reports a claim or asks general query
          const { isClaimLog, claim } = parseClaimIntent(text);
          if (isClaimLog) {
            suggestedClaim = claim;
            replyText = `I have structured this claim for activity [${claim.reportedActivityId || 'Pending Matching'}]. As Supervisor, you can submit this to the intake pipeline or directly manage it in your Review Workspace.`;
          } else {
            replyText = `I am tracking project schedule telemetry. You can ask for pending review queues, delay drivers, unmatched scope items, overall project progress, or critical path float status.`;
            actionLinks = [
              { label: 'Review Workspace', to: '/review' },
              { label: 'Daily Digest', to: '/digest' },
            ];
          }
        }
      } else {
        // ── SITE ENGINEER FIELD REPORTING RESPONSES ─────────────────────────
        const { isClaimLog, claim } = parseClaimIntent(text);

        if (lower.includes('delayed') || lower.includes('delay') || lower.includes('blocker')) {
          replyText = `Field Delay Check:\n\nActivities in Civil (CIV-PS3-FND-002) and Piping (PIP-PS3-WLD-024) have active delay flags recorded due to monsoon weather. If your current task is affected, speak or type your delay remark to include it in today's intake report.`;
        } else if (isClaimLog) {
          suggestedClaim = claim;
          const actionLabel =
            claim.eventType === 'ACTUAL_START'
              ? 'Actual Start'
              : claim.eventType === 'ACTUAL_FINISH'
              ? 'Actual Finish'
              : 'Progress Update';

          replyText = `I have structured your field report as a ${actionLabel} claim${
            claim.reportedActivityId ? ` for [${claim.reportedActivityId}]` : ''
          }. Please review the extracted parameters below and click "Submit Claim for Supervisor Review".`;
        } else {
          replyText = `I noted your report. You can log activity start dates, report percentage completion, log completed quantities (e.g. cu.m, joints, meters), attach evidence delivery slips, or record weather delay remarks.`;
        }
      }

      const agentMsg: ChatMessage = {
        id: `agent-${Date.now()}`,
        sender: 'agent',
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        text: replyText,
        suggestedClaim,
        actionLinks,
      };

      setMessages((prev) => [...prev, agentMsg]);
      setIsAgentThinking(false);
    }, 600);
  };

  const handleConfirmAndSubmitClaim = async (
    messageId: string,
    claimData: NonNullable<ChatMessage['suggestedClaim']>
  ) => {
    setMessages((prev) =>
      prev.map((m) => (m.id === messageId ? { ...m, isSubmitting: true } : m))
    );

    try {
      // v2: a Supervisor never files an execution claim -- the drafted claim is handed to a Site Engineer, who files it through the ordinary intake.
      if (IS_V2 && isSupervisor) {
        await timeAgentApi.handOff(claimData, 'Drafted in the Time Agent');
        setMessages((prev) => prev.map((m) => (m.id === messageId ? { ...m, isSubmitting: false, handedOff: true } : m)));
        return;
      }
      // 1. Submit text to Intake pipeline
      const submitRes = await claimsApi.submitText(claimData.rawText);
      const event = submitRes.event;

      // 2. Trigger automatic matching and checking cascade
      try {
        await claimsApi.match(event.event_id);
        await claimsApi.check(event.event_id);
      } catch {
        // Continue if background worker processes match
      }

      const freshEvent = await claimsApi.getEvent(event.event_id).catch(() => event);

      setMessages((prev) =>
        prev.map((m) =>
          m.id === messageId
            ? {
                ...m,
                isSubmitting: false,
                submittedEvent: freshEvent,
              }
            : m
        )
      );
    } catch (err: any) {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === messageId
            ? {
                ...m,
                isSubmitting: false,
                text: `${m.text}\n\n⚠️ Error submitting claim: ${err?.message || 'Submission failed'}`,
              }
            : m
        )
      );
    }
  };

  const quickPrompts = isSupervisor ? SUPERVISOR_QUICK_ACTIONS : SITE_ENGINEER_QUICK_ACTIONS;

  return (
    <div className="space-y-6 animate-in fade-in duration-200 max-w-5xl mx-auto">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-slate-300 dark:border-[#214766]/60 pb-4">
        <div>
          <div className="flex items-center gap-2 text-xs font-mono text-muted-foreground pb-1 flex-wrap">
            <span className="flex items-center gap-1 font-bold text-primary">
              <Building2 className="w-3.5 h-3.5 text-[#FF7A18]" />
              {currentProject.name} ({currentProject.code})
            </span>
            <span>·</span>
            <span className="text-[11px] px-2 py-0.2 rounded bg-slate-100 dark:bg-[#0B2742] text-muted-foreground border border-slate-300 dark:border-[#214766]">
              {currentScheduleVersion.versionNumber}
            </span>
            <span>·</span>
            <span className="uppercase font-extrabold px-1.5 py-0.5 rounded bg-primary/10 text-primary border border-primary/20">
              {isSupervisor ? 'Supervisor Decision-Support Mode' : 'Site Engineer Field Intake Mode'}
            </span>
          </div>
          <h1 className="text-2xl font-extrabold text-[#071A2D] dark:text-[#F5F7FA] tracking-tight flex items-center gap-2.5 mt-1">
            <div className="p-1.5 rounded-xl bg-orange-100 dark:bg-orange-950/60 border border-orange-400/80 text-[#FF7A18] shadow-xs">
              <Bot className="w-5 h-5" />
            </div>
            Setu AI Time Agent
          </h1>
          <p className="text-[#334155] dark:text-[#CBD5E1] text-xs font-semibold mt-1">
            {isSupervisor
              ? 'Supervisor monitoring and decision-support assistant. Query review queues, delay drivers, unmatched scope, and critical path risks.'
              : 'Field execution voice & chat assistant. Report activity start/finish milestones, daily progress percentages, quantities, and site remarks.'}
          </p>
        </div>

        <div className="flex items-center gap-2">
          {isSupervisor ? (
            <Link to="/review">
              <Button variant="outline" size="sm" className="text-xs h-9 gap-1.5 font-bold">
                <ShieldCheck className="w-3.5 h-3.5 text-primary" />
                Review Workspace
              </Button>
            </Link>
          ) : (
            <Link to="/intake">
              <Button variant="outline" size="sm" className="text-xs h-9 gap-1.5 font-bold">
                <HardHat className="w-3.5 h-3.5 text-[#FF7A18]" />
                Claim Intake
              </Button>
            </Link>
          )}
        </div>
      </div>

      {/* Human-in-the-loop Governance Banner */}
      <div className="p-3.5 rounded-xl bg-amber-500/10 border border-amber-500/30 flex items-start gap-3 text-xs text-amber-900 dark:text-amber-200">
        <ShieldCheck className="w-4 h-4 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
        <div className="space-y-0.5">
          <span className="font-bold block">Authoritative Schedule Integrity Policy</span>
          <span className="text-muted-foreground">
            The Time Agent structures and validates candidate claims into unapproved events.{' '}
            <strong>It never directly writes authoritative schedule actuals</strong> without explicit Supervisor verification.
          </span>
        </div>
      </div>

      {/* Quick Prompts Carousel / Grid */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-[11px] font-bold text-muted-foreground uppercase tracking-wider">
            {isSupervisor ? 'Supervisor Monitoring Quick Actions' : 'Field Reporting Quick Actions'}
          </span>
          <span className="text-[10px] text-muted-foreground font-mono">
            {isSupervisor ? 'Role: SUPERVISOR' : 'Role: SITE_ENGINEER'}
          </span>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2.5">
          {quickPrompts.map((qp, idx) => {
            const Icon = qp.icon;
            return (
              <button
                key={idx}
                type="button"
                onClick={() => handleSendMessage(qp.prompt)}
                className="p-2.5 text-left rounded-xl border border-slate-200/80 dark:border-[#214766] bg-white/95 dark:bg-[#071A2D]/95 hover:border-[#FF7A18] hover:shadow-xs transition-all text-xs group cursor-pointer space-y-1"
              >
                <div className="flex items-center gap-1.5 font-bold text-[#071A2D] dark:text-[#F5F7FA]">
                  <Icon className={cn('w-3.5 h-3.5 shrink-0', qp.color)} />
                  <span className="truncate text-[11px]">{qp.label}</span>
                </div>
                <p className="text-[10px] text-muted-foreground line-clamp-2 leading-relaxed">
                  {qp.prompt}
                </p>
              </button>
            );
          })}
        </div>
      </div>

      {/* Chat Thread Card */}
      <Card className="border-slate-200/80 dark:border-[#214766] bg-white/95 dark:bg-[#071A2D]/95 shadow-xl rounded-2xl flex flex-col h-[560px]">
        <CardHeader className="py-3 px-4 border-b border-slate-200/80 dark:border-[#214766] flex flex-row items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-2.5 h-2.5 rounded-full bg-emerald-500 animate-pulse" />
            <CardTitle className="text-xs font-bold text-foreground flex items-center gap-2">
              <span>Time Agent Live Session ({messages.length} messages)</span>
              <span className="text-[10px] font-normal px-2 py-0.5 rounded-full bg-slate-100 dark:bg-[#0B2742] text-muted-foreground border border-slate-300 dark:border-[#214766]">
                {isSupervisor ? 'Supervisor Monitoring' : 'Site Field Intake'}
              </span>
            </CardTitle>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={() =>
              setMessages([
                {
                  id: `welcome-reset-${Date.now()}`,
                  sender: 'agent',
                  timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
                  text: isSupervisor
                    ? 'Session reset. Ready for your next supervisory query on review queues, delays, or project progress.'
                    : 'Session reset. Ready for your next field activity start, finish, quantity, or progress report.',
                },
              ])
            }
            className="h-7 text-xs text-muted-foreground hover:text-foreground gap-1"
          >
            <RotateCcw className="w-3 h-3" />
            Reset Chat
          </Button>
        </CardHeader>

        {/* Messages Body */}
        <CardContent className="flex-1 overflow-y-auto p-4 space-y-4">
          {messages.map((msg) => {
            const isAgent = msg.sender === 'agent';
            return (
              <div
                key={msg.id}
                className={cn(
                  'flex gap-3 max-w-[88%]',
                  isAgent ? 'mr-auto items-start' : 'ml-auto flex-row-reverse items-start'
                )}
              >
                {/* Avatar */}
                <div
                  className={cn(
                    'w-8 h-8 rounded-full flex items-center justify-center shrink-0 text-white font-bold text-xs shadow-xs',
                    isAgent
                      ? 'bg-gradient-to-br from-[#FF7A18] to-[#FF941F]'
                      : 'bg-gradient-to-br from-[#14B8A6] to-[#0D9488]'
                  )}
                >
                  {isAgent ? <Bot className="w-4 h-4" /> : <User className="w-4 h-4" />}
                </div>

                {/* Message Bubble */}
                <div className="space-y-2 max-w-full">
                  <div
                    className={cn(
                      'p-3.5 rounded-2xl text-xs leading-relaxed shadow-xs',
                      isAgent
                        ? 'bg-slate-50 dark:bg-[#0B2742] border border-slate-200/80 dark:border-[#214766] text-[#071A2D] dark:text-[#F5F7FA]'
                        : 'bg-gradient-to-r from-[#FF7A18] to-[#FF941F] text-white font-medium'
                    )}
                  >
                    <p className="whitespace-pre-wrap">{msg.text}</p>
                    <span
                      className={cn(
                        'text-[9px] font-mono mt-1.5 block opacity-75',
                        isAgent ? 'text-muted-foreground' : 'text-white'
                      )}
                    >
                      {msg.timestamp}
                    </span>
                  </div>

                  {/* Contextual Action Links (For Supervisor Decision-Support) */}
                  {msg.actionLinks && msg.actionLinks.length > 0 && (
                    <div className="flex flex-wrap gap-2 pt-1">
                      {msg.actionLinks.map((link, lIdx) => (
                        <Link key={lIdx} to={link.to}>
                          <Button
                            variant="outline"
                            size="sm"
                            className="h-7 px-2.5 text-[11px] font-bold border-primary/40 text-primary hover:bg-primary/10 gap-1.5 shadow-2xs"
                          >
                            <span>{link.label}</span>
                            <ArrowRight className="w-3 h-3" />
                          </Button>
                        </Link>
                      ))}
                    </div>
                  )}

                  {/* Structured Claim Proposal Card (Field Claims) */}
                  {msg.suggestedClaim && !msg.submittedEvent && !msg.handedOff && (
                    <div className="p-3.5 rounded-xl border border-orange-500/30 bg-orange-50/50 dark:bg-orange-950/20 space-y-2.5 text-xs">
                      <div className="flex items-center justify-between">
                        <span className="font-bold text-orange-600 dark:text-orange-400 flex items-center gap-1.5 text-[11px]">
                          <Sparkles className="w-3.5 h-3.5" />
                          Drafted Execution Claim
                        </span>
                        <span className="text-[10px] font-mono bg-white dark:bg-[#0B2742] px-2 py-0.5 rounded border border-orange-300 dark:border-orange-800 text-foreground font-semibold">
                          {msg.suggestedClaim.eventType}
                        </span>
                      </div>

                      <div className="grid grid-cols-2 gap-2 text-[11px] text-muted-foreground">
                        <div>
                          <span className="font-bold text-foreground">Activity: </span>
                          <span className="font-mono text-primary font-bold">
                            {msg.suggestedClaim.reportedActivityId || 'To be matched'}
                          </span>
                        </div>
                        <div>
                          <span className="font-bold text-foreground">Discipline: </span>
                          <span className="font-mono">{msg.suggestedClaim.discipline || 'General'}</span>
                        </div>
                        <div>
                          <span className="font-bold text-foreground">Progress/Qty: </span>
                          <span className="font-mono">
                            {msg.suggestedClaim.claimedPct != null
                              ? `${msg.suggestedClaim.claimedPct}%`
                              : msg.suggestedClaim.claimedQuantity != null
                              ? `${msg.suggestedClaim.claimedQuantity} ${msg.suggestedClaim.claimedUom || ''}`
                              : 'Actual Start logged'}
                          </span>
                        </div>
                        <div>
                          <span className="font-bold text-foreground">Location: </span>
                          <span className="font-mono">{msg.suggestedClaim.location || 'Site Area'}</span>
                        </div>
                        {msg.suggestedClaim.evidenceRef && (
                          <div className="col-span-2">
                            <span className="font-bold text-foreground">Evidence Ref: </span>
                            <span className="font-mono text-emerald-600 dark:text-emerald-400">
                              {msg.suggestedClaim.evidenceRef}
                            </span>
                          </div>
                        )}
                        <div>
                          <span className="font-bold text-foreground">Event Date: </span>
                          <span className="font-mono">{msg.suggestedClaim.eventDate}</span>
                        </div>
                      </div>

                      <Button
                        size="sm"
                        onClick={() => handleConfirmAndSubmitClaim(msg.id, msg.suggestedClaim!)}
                        disabled={msg.isSubmitting}
                        isLoading={msg.isSubmitting}
                        className="w-full h-8 text-xs font-bold bg-gradient-to-r from-[#FF7A18] to-[#FF941F] hover:from-[#E06810] hover:to-[#FF7A18] text-white shadow-xs"
                      >
                        <CheckCircle2 className="w-3.5 h-3.5 mr-1.5" />
                        {msg.isSubmitting
                          ? (IS_V2 && isSupervisor ? 'Handing off...' : 'Routing to Validation Pipeline...')
                          : (IS_V2 && isSupervisor ? 'Hand Off Draft to Site Engineer' : 'Submit Claim for Supervisor Review')}
                      </Button>
                    </div>
                  )}

                  {msg.handedOff && (
                    <div className="p-3.5 rounded-xl border border-emerald-500/40 bg-emerald-50 dark:bg-emerald-950/30 text-xs space-y-1">
                      <span className="font-bold text-emerald-700 dark:text-emerald-300 flex items-center gap-1.5 text-[11px]">
                        <CheckCircle2 className="w-4 h-4 text-emerald-600" />
                        Draft handed off to the Site Engineer
                      </span>
                      <p className="text-[11px] text-muted-foreground">
                        The draft is waiting on the engineer's Intake page. It becomes a claim only when the engineer files it; you will review it here once they do.
                      </p>
                    </div>
                  )}

                  {/* Claim Submission Success Banner */}
                  {msg.submittedEvent && (
                    <div className="p-3.5 rounded-xl border border-emerald-500/40 bg-emerald-50 dark:bg-emerald-950/30 text-xs space-y-2">
                      <div className="flex items-center justify-between">
                        <span className="font-bold text-emerald-700 dark:text-emerald-300 flex items-center gap-1.5 text-[11px]">
                          <CheckCircle2 className="w-4 h-4 text-emerald-600" />
                          Candidate Claim Ingested & Validated
                        </span>
                        <StatusBadge status={msg.submittedEvent.status} size="sm" />
                      </div>

                      <p className="text-[11px] text-muted-foreground">
                        Assigned Claim ID: <strong className="font-mono text-foreground">{msg.submittedEvent.event_id}</strong>.
                        Ready for human verification in the Supervisor Review Workspace.
                      </p>

                      <Link to={`/review?event_id=${msg.submittedEvent.event_id}`}>
                        <Button
                          variant="outline"
                          size="sm"
                          className="w-full h-7 text-[11px] font-bold border-emerald-300 dark:border-emerald-800 text-emerald-700 dark:text-emerald-300 hover:bg-emerald-100 dark:hover:bg-emerald-900/40 gap-1 mt-1"
                        >
                          <ExternalLink className="w-3 h-3" />
                          Open in Review Workspace
                        </Button>
                      </Link>
                    </div>
                  )}
                </div>
              </div>
            );
          })}

          {isAgentThinking && (
            <div className="flex gap-3 items-center text-xs text-muted-foreground animate-pulse">
              <div className="w-8 h-8 rounded-full bg-gradient-to-br from-[#FF7A18] to-[#FF941F] flex items-center justify-center text-white shrink-0 shadow-xs">
                <Bot className="w-4 h-4" />
              </div>
              <span>Setu AI Time Agent is analyzing schedule and claims context...</span>
            </div>
          )}

          <div ref={messagesEndRef} />
        </CardContent>

        {/* Input Bar & Voice Controls */}
        <div className="p-3 border-t border-slate-200/80 dark:border-[#214766] space-y-2 bg-slate-50/50 dark:bg-[#0A2238]/60 rounded-b-2xl">
          {speechError && (
            <p className="text-xs text-destructive font-semibold px-2">
              {speechError}
            </p>
          )}

          <div className="flex items-center gap-2">
            {/* Voice Toggle */}
            <Button
              type="button"
              variant="outline"
              size="icon"
              onClick={toggleVoice}
              className={cn(
                'h-10 w-10 rounded-xl shrink-0 transition-all cursor-pointer',
                isListening
                  ? 'bg-rose-600 text-white border-rose-600 animate-pulse'
                  : 'border-slate-300 dark:border-[#214766] text-[#FF7A18] hover:bg-orange-50 dark:hover:bg-orange-950/40'
              )}
              title={isListening ? 'Stop Voice Recording' : 'Start Voice Input'}
            >
              {isListening ? <MicOff className="w-4 h-4" /> : <Mic className="w-4 h-4" />}
            </Button>

            {/* Input Field */}
            <Input
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  handleSendMessage();
                }
              }}
              placeholder={
                isListening
                  ? 'Listening... speak your claim or query...'
                  : isSupervisor
                  ? 'Ask: "Which claims are waiting for my review?" or "What activities are delayed?"...'
                  : 'Speak or type: "Foundation pouring for F-4 started at 9:15 AM" or report progress...'
              }
              className="h-10 text-xs rounded-xl bg-white dark:bg-[#0B2742] border-slate-300 dark:border-[#214766] text-foreground font-medium"
            />

            {/* Send Button */}
            <Button
              type="button"
              onClick={() => handleSendMessage()}
              disabled={!inputText.trim() || isAgentThinking}
              className="h-10 px-4 rounded-xl font-bold bg-gradient-to-r from-[#FF7A18] to-[#FF941F] text-white shadow-xs cursor-pointer"
            >
              <Send className="w-4 h-4" />
            </Button>
          </div>
        </div>
      </Card>
    </div>
  );
}

