import React, { useCallback, useEffect, useState } from 'react';
import { Sparkles } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { timeAgentApi, ClaimHandoff } from '@/api';

/** v2: claim drafts a Supervisor's Time Agent handed to this Site Engineer. Using one fills the text tab; it becomes a claim only when filed through the normal intake. */
export function HandoffDrafts({ refreshKey, onUse }: { refreshKey: unknown; onUse: (h: ClaimHandoff) => void }) {
  const [items, setItems] = useState<ClaimHandoff[]>([]);
  const load = useCallback(() => {
    timeAgentApi.list().then(setItems).catch(() => setItems([]));
  }, []);
  useEffect(load, [load, refreshKey]);
  if (items.length === 0) return null;
  return (
    <div data-testid="handoff-drafts" className="p-3.5 rounded-xl border border-orange-500/30 bg-orange-50/50 dark:bg-orange-950/20 space-y-2 text-xs">
      <div className="font-bold text-orange-600 dark:text-orange-400 flex items-center gap-1.5 text-[11px]">
        <Sparkles className="w-3.5 h-3.5" />
        Drafts handed to you by your Supervisor ({items.length})
      </div>
      {items.map((h) => (
        <div key={h.handoff_id} className="flex items-center justify-between gap-3 border-t border-orange-200/60 pt-2">
          <span className="text-[11px] text-foreground truncate">{String(h.draft.rawText || '')}</span>
          <span className="flex gap-1.5 shrink-0">
            <Button size="sm" className="h-7 text-[11px] font-bold" onClick={() => onUse(h)}>Use draft</Button>
            <Button size="sm" variant="outline" className="h-7 text-[11px]" onClick={() => timeAgentApi.dismiss(h.handoff_id).then(load).catch(() => {})}>Dismiss</Button>
          </span>
        </div>
      ))}
    </div>
  );
}
