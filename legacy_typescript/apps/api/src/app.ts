import crypto from "node:crypto";
import cors from "@fastify/cors";
import jwt from "@fastify/jwt";
import Fastify, { type FastifyRequest } from "fastify";
import type { Pool } from "pg";
import { z } from "zod";
import {
  authenticateDeviceSchema,
  createDeviceSchema,
  createFacilitySchema,
  createInstitutionSchema,
  enrollDeviceSchema,
} from "@optometry-clinic-sync/contracts";
import type { AppConfig } from "./config.js";
import { SyncRepository } from "./repository.js";

const institutionParamsSchema = z.object({ institutionId: z.uuid() });
const facilityParamsSchema = z.object({ institutionId: z.uuid(), facilityId: z.uuid() });

type DeviceClaims = { deviceId: string; facilityId: string; institutionId: string };

function constantTimeEqual(actual: string, expected: string): boolean {
  const actualBuffer = Buffer.from(actual);
  const expectedBuffer = Buffer.from(expected);
  return actualBuffer.length === expectedBuffer.length && crypto.timingSafeEqual(actualBuffer, expectedBuffer);
}

export async function buildApp(config: AppConfig, pool: Pool) {
  const app = Fastify({ logger: { redact: ["req.headers.authorization", "req.headers.x-admin-key", "req.body.token", "req.body.clientSecret"] } });
  const repository = new SyncRepository(pool);

  await app.register(cors, { origin: config.CORS_ORIGIN.split(",").map((origin) => origin.trim()) });
  await app.register(jwt, { secret: config.DEVICE_JWT_SECRET });

  const requireAdmin = async (request: FastifyRequest) => {
    const supplied = String(request.headers["x-admin-key"] ?? "");
    if (!constantTimeEqual(supplied, config.ADMIN_API_KEY)) {
      throw Object.assign(new Error("Administrator authentication required."), { statusCode: 401 });
    }
  };

  app.setErrorHandler((error, _request, reply) => {
    const normalizedError = error instanceof Error ? error : new Error("Unknown request error.");
    const code = (normalizedError as Error & { code?: string }).code;
    if (code === "23505") return reply.status(409).send({ error: "A record with those details already exists." });
    if (normalizedError.message === "INSTITUTION_NOT_FOUND") return reply.status(404).send({ error: "Institution not found." });
    if (normalizedError.message === "FACILITY_NOT_FOUND") return reply.status(404).send({ error: "Facility not found." });
    if (normalizedError.message === "INVALID_ENROLLMENT") return reply.status(400).send({ error: "Enrollment token is invalid, expired, or already used." });
    if (normalizedError.message === "INSTALLATION_ALREADY_BOUND") return reply.status(409).send({ error: "This installation is already registered to another device." });
    if (normalizedError instanceof z.ZodError) return reply.status(400).send({ error: "Invalid request.", details: z.flattenError(normalizedError).fieldErrors });
    const statusCode = (normalizedError as Error & { statusCode?: number }).statusCode;
    if (statusCode && statusCode < 500) return reply.status(statusCode).send({ error: normalizedError.message });
    app.log.error({ err: normalizedError }, "request failed");
    return reply.status(500).send({ error: "The request could not be completed." });
  });

  app.get("/health", async () => ({ status: "ok" }));

  app.get("/api/institutions", { preHandler: requireAdmin }, async () => ({ institutions: await repository.listInstitutions() }));

  app.post("/api/institutions", { preHandler: requireAdmin }, async (request, reply) => {
    const institution = await repository.createInstitution(createInstitutionSchema.parse(request.body));
    return reply.status(201).send({ institution });
  });

  app.get("/api/institutions/:institutionId", { preHandler: requireAdmin }, async (request, reply) => {
    const { institutionId } = institutionParamsSchema.parse(request.params);
    const institution = await repository.getInstitution(institutionId);
    if (!institution) return reply.status(404).send({ error: "Institution not found." });
    return { institution };
  });

  app.get("/api/institutions/:institutionId/facilities", { preHandler: requireAdmin }, async (request, reply) => {
    const { institutionId } = institutionParamsSchema.parse(request.params);
    if (!await repository.getInstitution(institutionId)) return reply.status(404).send({ error: "Institution not found." });
    return { facilities: await repository.listFacilities(institutionId) };
  });

  app.post("/api/institutions/:institutionId/facilities", { preHandler: requireAdmin }, async (request, reply) => {
    const { institutionId } = institutionParamsSchema.parse(request.params);
    const facility = await repository.createFacility(institutionId, createFacilitySchema.parse(request.body));
    return reply.status(201).send({ facility });
  });

  app.get("/api/institutions/:institutionId/facilities/:facilityId", { preHandler: requireAdmin }, async (request, reply) => {
    const { institutionId, facilityId } = facilityParamsSchema.parse(request.params);
    const facility = await repository.getFacility(institutionId, facilityId);
    if (!facility) return reply.status(404).send({ error: "Facility not found." });
    return { facility };
  });

  app.get("/api/institutions/:institutionId/facilities/:facilityId/devices", { preHandler: requireAdmin }, async (request, reply) => {
    const { institutionId, facilityId } = facilityParamsSchema.parse(request.params);
    if (!await repository.getFacility(institutionId, facilityId)) return reply.status(404).send({ error: "Facility not found." });
    return { devices: await repository.listDevices(facilityId) };
  });

  app.post("/api/institutions/:institutionId/facilities/:facilityId/devices", { preHandler: requireAdmin }, async (request, reply) => {
    const { institutionId, facilityId } = facilityParamsSchema.parse(request.params);
    const result = await repository.createPendingDevice(institutionId, facilityId, createDeviceSchema.parse(request.body));
    return reply.status(201).send(result);
  });

  app.post("/api/devices/enroll", async (request, reply) => {
    const result = await repository.enrollDevice(enrollDeviceSchema.parse(request.body));
    const accessToken = app.jwt.sign(
      { deviceId: result.device.id, facilityId: result.facility.id, institutionId: result.institution.id },
      { expiresIn: "15m" },
    );
    return reply.status(201).send({
      ...result,
      accessToken,
      expiresIn: 900,
      institutionId: result.institution.id,
      facilityId: result.facility.id,
      nodeId: result.device.id,
    });
  });

  app.post("/api/devices/authenticate", async (request, reply) => {
    const input = authenticateDeviceSchema.parse(request.body);
    const identity = await repository.authenticateDevice(input.clientKey, input.clientSecret);
    if (!identity) return reply.status(401).send({ error: "Invalid device credentials." });
    return {
      accessToken: app.jwt.sign(identity, { expiresIn: "15m" }),
      expiresIn: 900,
    };
  });

  app.get("/api/devices/me", async (request, reply) => {
    try {
      await request.jwtVerify<DeviceClaims>();
    } catch {
      return reply.status(401).send({ error: "Device authentication required." });
    }
    const claims = request.user as DeviceClaims;
    const device = await repository.getDeviceForFacility(claims.deviceId, claims.facilityId);
    if (!device || device.status !== "enrolled") return reply.status(401).send({ error: "Device authentication required." });
    return { device };
  });

  return app;
}
