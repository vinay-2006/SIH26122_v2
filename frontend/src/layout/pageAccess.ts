import type { Permission } from '@/api/projects';
import { IS_V2 } from '@/config';

/** Who may open the WBS Explorer, Project Intelligence and the Audit Trail. Legacy mode keeps the original rules; v2 uses the page-level permissions the server grants
 *  (WBS Explorer: Supervisor + Site Engineer; Project Intelligence: Project Manager + Site Engineer; Audit Trail: Project Manager). Hiding a link never grants anything: the API checks again. */
export const PAGE_WBS: Permission[] = IS_V2 ? ['VIEW_WBS_EXPLORER'] : ['VIEW_SCHEDULE'];
export const PAGE_INTELLIGENCE: Permission[] = IS_V2 ? ['VIEW_PROJECT_INTELLIGENCE'] : ['VIEW_PROJECT'];
export const PAGE_AUDIT: Permission[] = IS_V2 ? ['VIEW_AUDIT_TRAIL'] : ['VIEW_AUDIT'];
