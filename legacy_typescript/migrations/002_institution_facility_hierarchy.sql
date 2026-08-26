ALTER TABLE organizations RENAME TO institutions;

CREATE TABLE facilities (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  institution_id UUID NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  code TEXT NOT NULL,
  address TEXT,
  timezone TEXT NOT NULL DEFAULT 'Africa/Accra',
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive')),
  sync_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (institution_id, code)
);

CREATE INDEX facilities_institution_id_idx ON facilities(institution_id);

-- Preserve any data created by the initial flat model. Each old clinic becomes
-- an institution with one facility using the same UUID, so device ownership can
-- be migrated without inventing a cross-facility relationship.
INSERT INTO facilities (
  id, institution_id, name, code, address, timezone, status, sync_enabled, created_at, updated_at
)
SELECT id, id, name, code, address, timezone, status, TRUE, created_at, updated_at
FROM institutions;

ALTER TABLE devices DROP CONSTRAINT devices_organization_id_fkey;
DROP INDEX devices_organization_id_idx;
ALTER TABLE devices RENAME COLUMN organization_id TO facility_id;
ALTER TABLE devices
  ADD CONSTRAINT devices_facility_id_fkey
  FOREIGN KEY (facility_id) REFERENCES facilities(id) ON DELETE CASCADE;
CREATE INDEX devices_facility_id_idx ON devices(facility_id);

ALTER TABLE enrollment_tokens DROP CONSTRAINT enrollment_tokens_organization_id_fkey;
ALTER TABLE enrollment_tokens RENAME COLUMN organization_id TO facility_id;
ALTER TABLE enrollment_tokens
  ADD CONSTRAINT enrollment_tokens_facility_id_fkey
  FOREIGN KEY (facility_id) REFERENCES facilities(id) ON DELETE CASCADE;
