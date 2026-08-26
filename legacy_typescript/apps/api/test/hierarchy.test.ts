import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it, vi } from "vitest";
import type { Pool } from "pg";
import { createFacilitySchema, createInstitutionSchema } from "@optometry-clinic-sync/contracts";
import { SyncRepository } from "../src/repository.js";

describe("institution and facility hierarchy", () => {
  it("normalizes codes and does not accept institution ownership from a facility body", () => {
    const institution = createInstitutionSchema.parse({ name: "University of Cape Coast", code: "ucc" });
    const facility = createFacilitySchema.parse({
      institutionId: "00000000-0000-4000-8000-000000000000",
      name: "Optometry Eye Clinic",
      code: "eye-clinic",
    });

    expect(institution.code).toBe("UCC");
    expect(facility.code).toBe("EYE-CLINIC");
    expect(facility).not.toHaveProperty("institutionId");
  });

  it("scopes facility lookup to its institution and device listing to its facility", async () => {
    const query = vi.fn().mockResolvedValue({ rows: [] });
    const repository = new SyncRepository({ query } as unknown as Pool);

    await repository.getFacility("institution-a", "facility-b");
    await repository.listDevices("facility-b");

    expect(query.mock.calls[0][0]).toContain("WHERE id = $1 AND institution_id = $2");
    expect(query.mock.calls[0][1]).toEqual(["facility-b", "institution-a"]);
    expect(query.mock.calls[1][0]).toContain("WHERE facility_id = $1");
    expect(query.mock.calls[1][1]).toEqual(["facility-b"]);
  });

  it("migrates devices and enrollment tokens to facility foreign keys", async () => {
    const testDirectory = path.dirname(fileURLToPath(import.meta.url));
    const migration = await fs.readFile(path.resolve(testDirectory, "../../../migrations/002_institution_facility_hierarchy.sql"), "utf8");

    expect(migration).toContain("CREATE TABLE facilities");
    expect(migration).toContain("FOREIGN KEY (facility_id) REFERENCES facilities(id)");
    expect(migration.match(/FOREIGN KEY \(facility_id\) REFERENCES facilities\(id\)/g)).toHaveLength(2);
    expect(migration).toContain("UNIQUE (institution_id, code)");
  });
});
