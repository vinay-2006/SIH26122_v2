import { IS_V2, uploadCeilingProblem } from '@/config';

/**
 * Progress-report files. In v2 mode a Site Engineer uploads PROGRESS REPORTS and EVIDENCE only; baseline schedules (Primavera .xer, MS Project .xml/.mpp) are imported by a
 * Project Manager under Schedule. The list below is what the server can actually read (backend/v2/compat/uploads.py + backend/v2/filetypes.py):
 *   CSV, XLSX                  structured rows -> claims (no language model needed)
 *   TXT, DOCX, PDF with text   report text -> claims (language model when enabled, rule-based extraction otherwise)
 *   JPG, PNG, WebP             photographs and scanned / handwritten pages: read with the vision model (when enabled) or OCR (when installed); stored as evidence either way
 * Legacy mode keeps the original list unchanged.
 */
export const REPORT_FILE_EXTS = ['.csv', '.xlsx', '.pdf', '.txt', '.docx', '.jpg', '.jpeg', '.png', '.webp'];
const LEGACY_FILE_EXTS = ['.pdf', '.xlsx', '.xls', '.csv', '.txt', '.xer', '.jpg', '.jpeg', '.png'];

export const ACCEPTED_FILE_EXTS = IS_V2 ? REPORT_FILE_EXTS : LEGACY_FILE_EXTS;
export const ACCEPT_ATTR = ACCEPTED_FILE_EXTS.join(',');
export const FILE_TYPES_TEXT = IS_V2 ? 'CSV · Excel (.xlsx) · PDF · Word (.docx) · TXT · photos and scans (JPG, PNG, WebP)' : '.pdf · .xlsx · .csv · .txt · .xer · .jpg · .png';

const MB = 1024 * 1024;
const LIMIT_MB: Record<string, number> = { '.pdf': 25, '.jpg': 15, '.jpeg': 15, '.png': 15, '.webp': 15, '.xlsx': 15, '.docx': 15, '.csv': 5, '.txt': 5 };
const SCHEDULE_EXTS = ['.xer', '.xml', '.mpp', '.mpx', '.p6', '.pmxml'];

export const extOf = (name: string): string => {
  const i = name.lastIndexOf('.');
  return i >= 0 ? name.slice(i).toLowerCase() : '';
};

export function kindLabel(name: string): string {
  const e = extOf(name);
  if (['.jpg', '.jpeg', '.png', '.webp'].includes(e)) return 'Photo / scan';
  return ({ '.csv': 'CSV', '.xlsx': 'Excel', '.pdf': 'PDF', '.docx': 'Word', '.txt': 'Text' } as Record<string, string>)[e] ?? (e ? e.slice(1).toUpperCase() : 'File');
}

/** v2 mode: why this file cannot be uploaded as a progress report, or null when it can. Legacy mode: null (the original checks apply). */
export function fileProblem(file: { name: string; size: number }): string | null {
  if (!IS_V2) return null;
  const e = extOf(file.name);
  if (SCHEDULE_EXTS.includes(e)) {
    return 'This is a baseline schedule file. Schedules are imported by a Project Manager under Schedule; upload a progress report (CSV, Excel, PDF, Word, text, photo or scan) here instead.';
  }
  if (e === '.xls') return 'Legacy .xls files cannot be read. Open the file and save it as .xlsx (or PDF), then upload that.';
  if (e === '.doc') return 'Legacy .doc files cannot be read. Open the file and save it as .docx (or PDF), then upload that.';
  if (!REPORT_FILE_EXTS.includes(e)) return `${e || 'This file type'} is not supported. Accepted: CSV, Excel (.xlsx), PDF, Word (.docx), TXT, JPG, PNG, WebP.`;
  const ceiling = uploadCeilingProblem(file);
  if (ceiling) return ceiling;
  const limit = LIMIT_MB[e] ?? 5;
  if (file.size > limit * MB) return `${kindLabel(file.name)} files can be at most ${limit} MB.`;
  if (file.size === 0) return 'The file is empty.';
  return null;
}
