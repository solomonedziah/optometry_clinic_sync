import crypto from "node:crypto";
import type { Pool } from "pg";
import type {
  CreateDeviceInput,
  CreateFacilityInput,
  CreateInstitutionInput,
  EnrollDeviceInput,
} from "@optometry-clinic-sync/contracts";
import { createOpaqueSecret, hashSecret, hashToken, verifySecret } from "./crypto.js";

const institutionSelect = `SELECT id, name, code, address, timezone, status,
  created_at AS "createdAt", updated_at AS "updatedAt" FROM institutions`;
const facilitySelect = `SELECT id, institution_id AS "institutionId", name, code, address, timezone, status,
  sync_enabled AS "syncEnabled", created_at AS "createdAt", updated_at AS "updatedAt" FROM facilities`;
const deviceSelect = `SELECT id, facility_id AS "facilityId", installation_id AS "installationId",
  device_name AS "deviceName", location_name AS "locationName", platform, app_version AS "appVersion",
  status, enrolled_at AS "enrolledAt", last_seen_at AS "lastSeenAt",
  created_at AS "createdAt", updated_at AS "updatedAt" FROM devices`;

export class SyncRepository {
  constructor(private readonly pool: Pool) {}

  async listInstitutions() {
    return (await this.pool.query(`${institutionSelect} ORDER BY name`)).rows;
  }

  async createInstitution(input: CreateInstitutionInput) {
    const result = await this.pool.query(
      `INSERT INTO institutions (name, code, address, timezone)
       VALUES ($1, $2, $3, $4)
       RETURNING id, name, code, address, timezone, status,
         created_at AS "createdAt", updated_at AS "updatedAt"`,
      [input.name, input.code, input.address ?? null, input.timezone],
    );
    return result.rows[0];
  }

  async getInstitution(institutionId: string) {
    return (await this.pool.query(`${institutionSelect} WHERE id = $1`, [institutionId])).rows[0] ?? null;
  }

  async listFacilities(institutionId: string) {
    return (await this.pool.query(`${facilitySelect} WHERE institution_id = $1 ORDER BY name`, [institutionId])).rows;
  }

  async createFacility(institutionId: string, input: CreateFacilityInput) {
    if (!await this.getInstitution(institutionId)) throw new Error("INSTITUTION_NOT_FOUND");
    const result = await this.pool.query(
      `INSERT INTO facilities (institution_id, name, code, address, timezone, sync_enabled)
       VALUES ($1, $2, $3, $4, $5, $6)
       RETURNING id, institution_id AS "institutionId", name, code, address, timezone, status,
         sync_enabled AS "syncEnabled", created_at AS "createdAt", updated_at AS "updatedAt"`,
      [institutionId, input.name, input.code, input.address ?? null, input.timezone, input.syncEnabled],
    );
    return result.rows[0];
  }

  async getFacility(institutionId: string, facilityId: string) {
    return (await this.pool.query(
      `${facilitySelect} WHERE id = $1 AND institution_id = $2`,
      [facilityId, institutionId],
    )).rows[0] ?? null;
  }

  async listDevices(facilityId: string) {
    return (await this.pool.query(`${deviceSelect} WHERE facility_id = $1 ORDER BY created_at DESC`, [facilityId])).rows;
  }

  async createPendingDevice(institutionId: string, facilityId: string, input: CreateDeviceInput) {
    const client = await this.pool.connect();
    const token = createOpaqueSecret(32);
    const expiresAt = new Date(Date.now() + input.expiresInHours * 60 * 60 * 1000);
    try {
      await client.query("BEGIN");
      const facility = await client.query(
        `SELECT f.id, f.name, f.institution_id AS "institutionId", i.name AS "institutionName"
           FROM facilities f JOIN institutions i ON i.id = f.institution_id
          WHERE f.id = $1 AND f.institution_id = $2
          FOR UPDATE OF f`,
        [facilityId, institutionId],
      );
      if (!facility.rowCount) throw new Error("FACILITY_NOT_FOUND");
      const deviceResult = await client.query(
        `INSERT INTO devices (facility_id, device_name, location_name)
         VALUES ($1, $2, $3)
         RETURNING id, facility_id AS "facilityId", installation_id AS "installationId",
           device_name AS "deviceName", location_name AS "locationName", platform, app_version AS "appVersion",
           status, enrolled_at AS "enrolledAt", last_seen_at AS "lastSeenAt",
           created_at AS "createdAt", updated_at AS "updatedAt"`,
        [facilityId, input.deviceName, input.locationName ?? null],
      );
      await client.query(
        `INSERT INTO enrollment_tokens (facility_id, device_id, token_hash, expires_at)
         VALUES ($1, $2, $3, $4)`,
        [facilityId, deviceResult.rows[0].id, hashToken(token), expiresAt],
      );
      await client.query("COMMIT");
      return {
        device: deviceResult.rows[0],
        institution: { id: institutionId, name: facility.rows[0].institutionName },
        facility: { id: facility.rows[0].id, name: facility.rows[0].name },
        enrollment: { token, expiresAt: expiresAt.toISOString() },
      };
    } catch (error) {
      await client.query("ROLLBACK");
      throw error;
    } finally {
      client.release();
    }
  }

  async enrollDevice(input: EnrollDeviceInput) {
    const client = await this.pool.connect();
    try {
      await client.query("BEGIN");
      const result = await client.query(
        `SELECT et.id AS token_id, et.expires_at, et.consumed_at,
                d.id AS device_id, d.status AS device_status, d.installation_id,
                d.device_name, d.location_name,
                 f.id AS facility_id, f.name AS facility_name,
                 i.id AS institution_id, i.name AS institution_name
           FROM enrollment_tokens et
               JOIN devices d ON d.id = et.device_id AND d.facility_id = et.facility_id
               JOIN facilities f ON f.id = et.facility_id
               JOIN institutions i ON i.id = f.institution_id
          WHERE et.token_hash = $1
          FOR UPDATE OF et, d`,
        [hashToken(input.token)],
      );
      const row = result.rows[0];
      if (!row || row.consumed_at || new Date(row.expires_at).getTime() <= Date.now() || row.device_status !== "pending_enrollment") {
        throw new Error("INVALID_ENROLLMENT");
      }
      const installationCollision = await client.query(
        "SELECT id FROM devices WHERE installation_id = $1 AND id <> $2",
        [input.installationId, row.device_id],
      );
      if (installationCollision.rowCount) throw new Error("INSTALLATION_ALREADY_BOUND");

      const clientKey = `dev_${crypto.randomUUID()}`;
      const clientSecret = createOpaqueSecret(48);
      const issuedAt = new Date();
      await client.query("UPDATE enrollment_tokens SET consumed_at = $1 WHERE id = $2", [issuedAt, row.token_id]);
      const deviceResult = await client.query(
        `UPDATE devices SET installation_id = $1, device_name = $2, platform = $3, app_version = $4,
            status = 'enrolled', enrolled_at = $5, last_seen_at = $5, updated_at = $5
          WHERE id = $6
          RETURNING id, facility_id AS "facilityId", installation_id AS "installationId",
            device_name AS "deviceName", location_name AS "locationName", platform, app_version AS "appVersion",
            status, enrolled_at AS "enrolledAt", last_seen_at AS "lastSeenAt",
            created_at AS "createdAt", updated_at AS "updatedAt"`,
        [input.installationId, input.deviceName, input.platform, input.appVersion, issuedAt, row.device_id],
      );
      await client.query(
        `INSERT INTO device_credentials (device_id, client_key, client_secret_hash, issued_at)
         VALUES ($1, $2, $3, $4)`,
        [row.device_id, clientKey, await hashSecret(clientSecret), issuedAt],
      );
      await client.query("COMMIT");
      return {
        institution: { id: row.institution_id, name: row.institution_name },
        facility: { id: row.facility_id, name: row.facility_name },
        device: deviceResult.rows[0],
        credentials: { clientKey, clientSecret, issuedAt: issuedAt.toISOString() },
      };
    } catch (error) {
      await client.query("ROLLBACK");
      throw error;
    } finally {
      client.release();
    }
  }

  async authenticateDevice(clientKey: string, clientSecret: string) {
    const result = await this.pool.query(
      `SELECT dc.device_id AS "deviceId", dc.client_secret_hash AS "clientSecretHash",
                  dc.revoked_at AS "revokedAt", d.facility_id AS "facilityId",
                  f.institution_id AS "institutionId", d.status
                FROM device_credentials dc
                JOIN devices d ON d.id = dc.device_id
                JOIN facilities f ON f.id = d.facility_id
        WHERE dc.client_key = $1`,
      [clientKey],
    );
    const row = result.rows[0];
    if (!row || row.revokedAt || row.status !== "enrolled" || !await verifySecret(clientSecret, row.clientSecretHash)) return null;
    await this.pool.query("UPDATE device_credentials SET last_authenticated_at = NOW() WHERE client_key = $1", [clientKey]);
    await this.pool.query("UPDATE devices SET last_seen_at = NOW(), updated_at = NOW() WHERE id = $1", [row.deviceId]);
    return { deviceId: row.deviceId, facilityId: row.facilityId, institutionId: row.institutionId };
  }

  async getDeviceForFacility(deviceId: string, facilityId: string) {
    return (await this.pool.query(`${deviceSelect} WHERE id = $1 AND facility_id = $2`, [deviceId, facilityId])).rows[0] ?? null;
  }
}
