-- 0002 controlled reference data (idempotent inserts): disciplines, aliases, units of measure, issue categories.

CREATE TABLE disciplines (
  code       TEXT PRIMARY KEY CHECK (code = upper(code) AND code ~ '^[A-Z_]+$'),
  name       TEXT NOT NULL,
  sort_order INTEGER NOT NULL DEFAULT 100
);
INSERT INTO disciplines (code, name, sort_order) VALUES
  ('CIVIL', 'Civil', 10), ('STRUCTURAL', 'Structural', 20), ('PIPING', 'Piping', 30),
  ('STATIC_ROTATING_EQUIPMENT', 'Static & Rotating Equipment', 40), ('ELECTRICAL', 'Electrical', 50),
  ('INSTRUMENTATION', 'Instrumentation', 60), ('PROCESS', 'Process & Commissioning', 70),
  ('DRILLING', 'Drilling & Well Engineering', 80), ('LOGISTICS', 'Marine & Materials Logistics', 90),
  ('HSE', 'Health, Safety & Environment', 100), ('OTHER', 'Other / Unclassified', 999)
ON CONFLICT (code) DO NOTHING;

-- Free-text labels in an import resolve through this table; an unknown label maps to OTHER and is surfaced to the PM.
CREATE TABLE discipline_aliases (
  alias TEXT PRIMARY KEY CHECK (alias = lower(btrim(alias))),
  code  TEXT NOT NULL REFERENCES disciplines(code) ON UPDATE CASCADE ON DELETE RESTRICT
);
INSERT INTO discipline_aliases (alias, code) VALUES
  ('civil works','CIVIL'),('civil and structural','CIVIL'),('structural works','STRUCTURAL'),('structure','STRUCTURAL'),
  ('steel structure','STRUCTURAL'),('piping works','PIPING'),('pipe','PIPING'),('pipeline','PIPING'),
  ('static/rotating equipment','STATIC_ROTATING_EQUIPMENT'),('static rotating equipment','STATIC_ROTATING_EQUIPMENT'),
  ('mechanical','STATIC_ROTATING_EQUIPMENT'),('mechanical works','STATIC_ROTATING_EQUIPMENT'),('equipment','STATIC_ROTATING_EQUIPMENT'),
  ('electrical works','ELECTRICAL'),('power','ELECTRICAL'),('instrumentation works','INSTRUMENTATION'),
  ('instrument','INSTRUMENTATION'),('control systems','INSTRUMENTATION'),('process','PROCESS'),('commissioning','PROCESS'),
  ('drilling','DRILLING'),('well engineering','DRILLING'),('logistics','LOGISTICS'),('marine logistics','LOGISTICS'),
  ('procurement','LOGISTICS'),('hse','HSE'),('safety','HSE'),('health safety environment','HSE')
ON CONFLICT (alias) DO NOTHING;

-- Units never mix across a dimension; conversion only happens inside one (to_base_factor).
CREATE TABLE units_of_measure (
  code           TEXT PRIMARY KEY CHECK (code = upper(code)),
  name           TEXT NOT NULL,
  dimension      TEXT NOT NULL CHECK (dimension IN
                   ('LENGTH','AREA','VOLUME','MASS','COUNT','WELD_JOINT','EFFORT','MACHINE_TIME','LUMPSUM','PERCENT')),
  to_base_factor NUMERIC(20,8) NOT NULL CHECK (to_base_factor > 0)
);
INSERT INTO units_of_measure (code, name, dimension, to_base_factor) VALUES
  ('M','Metre','LENGTH',1),('KM','Kilometre','LENGTH',1000),('MM','Millimetre','LENGTH',0.001),
  ('M2','Square metre','AREA',1),('M3','Cubic metre','VOLUME',1),
  ('KG','Kilogram','MASS',1),('TONNE','Tonne','MASS',1000),
  ('NOS','Numbers','COUNT',1),('SET','Set','COUNT',1),
  ('JOINT','Weld joint','WELD_JOINT',1),
  ('MH','Man-hour','EFFORT',1),('HR','Equipment hour','MACHINE_TIME',1),
  ('LS','Lump sum','LUMPSUM',1),('PCT','Percent','PERCENT',1)
ON CONFLICT (code) DO NOTHING;

CREATE TABLE issue_categories (
  code       TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  sort_order INTEGER NOT NULL DEFAULT 100
);
INSERT INTO issue_categories (code, name, sort_order) VALUES
  ('LABOUR_SHORTAGE','Shortage of workers',10),('EQUIPMENT_SHORTAGE','Lack of equipment',20),
  ('MATERIAL_SHORTAGE','Lack of materials',30),('MATERIAL_DELIVERY_DELAY','Material delivery delay',40),
  ('CONTRACTOR_ISSUE','Contractor issue',50),('WEATHER','Weather disruption',60),('SITE_ACCESS','Site access problem',70),
  ('SAFETY','Safety issue',80),('TECHNICAL','Technical issue',90),('DESIGN_DOCUMENTATION','Design / documentation issue',100),
  ('PERMIT_APPROVAL','Permit / approval delay',110),('OTHER','Other',999)
ON CONFLICT (code) DO NOTHING;
