import { z } from "zod";

export const institutionStatusSchema = z.enum(["active", "inactive"]);
export const facilityStatusSchema = z.enum(["active", "inactive"]);
export const deviceStatusSchema = z.enum(["pending_enrollment", "enrolled", "revoked"]);

export const institutionSchema = z.object({
  id: z.uuid(),
  name: z.string(),
  code: z.string(),
  address: z.string().nullable(),
  timezone: z.string(),
  status: institutionStatusSchema,
  createdAt: z.string(),
  updatedAt: z.string(),
});

export const facilitySchema = z.object({
  id: z.uuid(),
  institutionId: z.uuid(),
  name: z.string(),
  code: z.string(),
  address: z.string().nullable(),
  timezone: z.string(),
  status: facilityStatusSchema,
  syncEnabled: z.boolean(),
  createdAt: z.string(),
  updatedAt: z.string(),
});

export const deviceSchema = z.object({
  id: z.uuid(),
  facilityId: z.uuid(),
  installationId: z.uuid().nullable().optional(),
  deviceName: z.string(),
  locationName: z.string().nullable(),
  platform: z.string().nullable(),
  appVersion: z.string().nullable(),
  status: deviceStatusSchema,
  enrolledAt: z.string().nullable(),
  lastSeenAt: z.string().nullable(),
  createdAt: z.string(),
  updatedAt: z.string(),
});

export const createInstitutionSchema = z.object({
  name: z.string().trim().min(1).max(160),
  code: z.string().trim().min(2).max(32).regex(/^[A-Za-z0-9_-]+$/).transform((value) => value.toUpperCase()),
  address: z.string().trim().max(300).nullable().optional(),
  timezone: z.string().trim().min(1).max(80).default("Africa/Accra"),
});

export const createFacilitySchema = z.object({
  name: z.string().trim().min(1).max(160),
  code: z.string().trim().min(2).max(32).regex(/^[A-Za-z0-9_-]+$/).transform((value) => value.toUpperCase()),
  address: z.string().trim().max(300).nullable().optional(),
  timezone: z.string().trim().min(1).max(80).default("Africa/Accra"),
  syncEnabled: z.boolean().default(true),
});

export const createDeviceSchema = z.object({
  deviceName: z.string().trim().min(1).max(160),
  locationName: z.string().trim().max(160).nullable().optional(),
  expiresInHours: z.number().int().min(1).max(168).default(24),
});

export const enrollDeviceSchema = z.object({
  token: z.string().trim().min(32).max(512),
  installationId: z.uuid(),
  deviceName: z.string().trim().min(1).max(160),
  platform: z.string().trim().min(1).max(80),
  appVersion: z.string().trim().min(1).max(40),
});

export const authenticateDeviceSchema = z.object({
  clientKey: z.string().trim().min(1).max(200),
  clientSecret: z.string().min(32).max(512),
});

export type Institution = z.infer<typeof institutionSchema>;
export type Facility = z.infer<typeof facilitySchema>;
export type Device = z.infer<typeof deviceSchema>;
export type CreateInstitutionInput = z.infer<typeof createInstitutionSchema>;
export type CreateFacilityInput = z.infer<typeof createFacilitySchema>;
export type CreateDeviceInput = z.infer<typeof createDeviceSchema>;
export type EnrollDeviceInput = z.infer<typeof enrollDeviceSchema>;
export type AuthenticateDeviceInput = z.infer<typeof authenticateDeviceSchema>;
