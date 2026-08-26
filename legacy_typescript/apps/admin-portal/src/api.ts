import type {
  CreateDeviceInput,
  CreateFacilityInput,
  CreateInstitutionInput,
  Device,
  Facility,
  Institution,
} from "@optometry-clinic-sync/contracts";

const baseUrl = String(import.meta.env.VITE_API_URL || "http://localhost:4000").replace(/\/$/, "");

type ApiOptions = RequestInit & { adminKey: string };

async function request<T>(path: string, options: ApiOptions): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Admin-Key": options.adminKey,
      ...options.headers,
    },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `Request failed (${response.status}).`);
  return body as T;
}

export const api = {
  listInstitutions: (adminKey: string) => request<{ institutions: Institution[] }>("/api/institutions", { adminKey }),
  createInstitution: (adminKey: string, input: CreateInstitutionInput) => request<{ institution: Institution }>("/api/institutions", {
    adminKey,
    method: "POST",
    body: JSON.stringify(input),
  }),
  listFacilities: (adminKey: string, institutionId: string) => request<{ facilities: Facility[] }>(`/api/institutions/${institutionId}/facilities`, { adminKey }),
  createFacility: (adminKey: string, institutionId: string, input: CreateFacilityInput) => request<{ facility: Facility }>(`/api/institutions/${institutionId}/facilities`, {
    adminKey,
    method: "POST",
    body: JSON.stringify(input),
  }),
  listDevices: (adminKey: string, institutionId: string, facilityId: string) => request<{ devices: Device[] }>(`/api/institutions/${institutionId}/facilities/${facilityId}/devices`, { adminKey }),
  createDevice: (adminKey: string, institutionId: string, facilityId: string, input: CreateDeviceInput) => request<{
    institution: { id: string; name: string };
    facility: { id: string; name: string };
    device: Device;
    enrollment: { token: string; expiresAt: string };
  }>(`/api/institutions/${institutionId}/facilities/${facilityId}/devices`, {
    adminKey,
    method: "POST",
    body: JSON.stringify(input),
  }),
};
