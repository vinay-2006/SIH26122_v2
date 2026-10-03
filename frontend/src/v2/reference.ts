/** Controlled vocabularies of the v2 schema (db/migrations/0002_reference_data.sql). They are reference data, not secrets; the server validates every value. */
export const DISCIPLINES: { code: string; name: string }[] = [
  { code: 'CIVIL', name: 'Civil' }, { code: 'STRUCTURAL', name: 'Structural' }, { code: 'PIPING', name: 'Piping' },
  { code: 'STATIC_ROTATING_EQUIPMENT', name: 'Static & Rotating Equipment' }, { code: 'ELECTRICAL', name: 'Electrical' }, { code: 'INSTRUMENTATION', name: 'Instrumentation' },
  { code: 'PROCESS', name: 'Process & Commissioning' }, { code: 'DRILLING', name: 'Drilling & Well Engineering' }, { code: 'LOGISTICS', name: 'Marine & Materials Logistics' },
  { code: 'HSE', name: 'Health, Safety & Environment' }, { code: 'OTHER', name: 'Other / Unclassified' },
];
export const UNITS: { code: string; name: string }[] = [
  { code: 'M', name: 'Metre' }, { code: 'KM', name: 'Kilometre' }, { code: 'MM', name: 'Millimetre' }, { code: 'M2', name: 'Square metre' }, { code: 'M3', name: 'Cubic metre' },
  { code: 'KG', name: 'Kilogram' }, { code: 'TONNE', name: 'Tonne' }, { code: 'NOS', name: 'Numbers' }, { code: 'SET', name: 'Set' }, { code: 'JOINT', name: 'Weld joint' },
  { code: 'MH', name: 'Man-hour' }, { code: 'HR', name: 'Equipment hour' }, { code: 'LS', name: 'Lump sum' }, { code: 'PCT', name: 'Percent' },
];
export const ISSUE_CATEGORIES: { code: string; name: string }[] = [
  { code: 'LABOUR_SHORTAGE', name: 'Shortage of workers' }, { code: 'EQUIPMENT_SHORTAGE', name: 'Lack of equipment' }, { code: 'MATERIAL_SHORTAGE', name: 'Lack of materials' },
  { code: 'MATERIAL_DELIVERY_DELAY', name: 'Material delivery delay' }, { code: 'CONTRACTOR_ISSUE', name: 'Contractor issue' }, { code: 'WEATHER', name: 'Weather disruption' },
  { code: 'SITE_ACCESS', name: 'Site access problem' }, { code: 'SAFETY', name: 'Safety issue' }, { code: 'TECHNICAL', name: 'Technical issue' },
  { code: 'DESIGN_DOCUMENTATION', name: 'Design / documentation issue' }, { code: 'PERMIT_APPROVAL', name: 'Permit / approval delay' }, { code: 'OTHER', name: 'Other' },
];
export const categoryName = (code: string) => ISSUE_CATEGORIES.find((c) => c.code === code)?.name ?? code;
export const WBS_TYPES = ['PROJECT', 'STAGE', 'AREA', 'SUB_ASSET', 'PACKAGE'];
