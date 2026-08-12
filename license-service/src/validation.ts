import { ApiError } from "./errors";
import type { DeviceProof, SignedDeviceRequest } from "./types";

export type JsonObject = Record<string, unknown>;

export function asObject(value: unknown): JsonObject {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new ApiError(400, "INVALID_REQUEST", "The JSON body must be an object.");
  }
  return value as JsonObject;
}

export function requireString(
  object: JsonObject,
  key: string,
  options: { min?: number; max?: number; pattern?: RegExp } = {},
): string {
  const value = object[key];
  if (typeof value !== "string") {
    throw new ApiError(400, "INVALID_REQUEST", `${key} must be a string.`);
  }
  const { min = 1, max = 512, pattern } = options;
  if (value.length < min || value.length > max || (pattern && !pattern.test(value))) {
    throw new ApiError(400, "INVALID_REQUEST", `${key} is invalid.`);
  }
  return value;
}

export function optionalString(
  object: JsonObject,
  key: string,
  options: { max?: number } = {},
): string | undefined {
  if (object[key] === undefined || object[key] === null) return undefined;
  return requireString(object, key, { min: 0, max: options.max ?? 200 });
}

export function requireProtocol(object: JsonObject): 1 {
  if (object.protocol_version !== 1) {
    throw new ApiError(400, "UNSUPPORTED_PROTOCOL", "protocol_version must be 1.");
  }
  return 1;
}

export function parseDevice(value: unknown): DeviceProof {
  const object = asObject(value);
  return {
    public_key: requireString(object, "public_key", {
      min: 43,
      max: 43,
      pattern: /^[A-Za-z0-9_-]+$/u,
    }),
    fingerprint_hash: requireString(object, "fingerprint_hash", {
      min: 64,
      max: 64,
      pattern: /^[a-f0-9]+$/u,
    }),
    label: optionalString(object, "label", { max: 100 }),
  };
}

export function parseSignedDeviceRequest(value: unknown): SignedDeviceRequest & JsonObject {
  const object = asObject(value);
  const requestedAt = requireString(object, "requested_at", { min: 20, max: 35 });
  const date = new Date(requestedAt);
  if (!Number.isFinite(date.getTime()) || requestedAt !== date.toISOString()) {
    throw new ApiError(400, "INVALID_REQUEST_TIME", "requested_at must be UTC ISO-8601.");
  }
  return {
    ...object,
    protocol_version: requireProtocol(object),
    request_id: requireString(object, "request_id", {
      min: 36,
      max: 36,
      pattern: /^[a-f0-9]{8}-[a-f0-9]{4}-[1-8][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/iu,
    }),
    requested_at: requestedAt,
    app_version: requireString(object, "app_version", {
      min: 1,
      max: 40,
      pattern: /^[0-9A-Za-z][0-9A-Za-z.+_-]*$/u,
    }),
    device: parseDevice(object.device),
    proof: requireString(object, "proof", {
      min: 86,
      max: 86,
      pattern: /^[A-Za-z0-9_-]+$/u,
    }),
  };
}

export function assertFreshRequest(requestedAt: string, now: Date): void {
  const delta = Math.abs(now.getTime() - new Date(requestedAt).getTime());
  if (delta > 5 * 60 * 1000) {
    throw new ApiError(401, "STALE_REQUEST", "The signed request is outside the allowed time window.");
  }
}

export function normalizeCode(value: string, prefix: "SC" | "TR"): string {
  const normalized = value.trim().toUpperCase().replaceAll(/\s+/gu, "");
  const pattern = new RegExp(`^${prefix}-[23456789A-HJ-NP-Z]{5}(?:-[23456789A-HJ-NP-Z]{5}){3}$`, "u");
  if (!pattern.test(normalized)) {
    throw new ApiError(400, "INVALID_CODE", "The code format is invalid.");
  }
  return normalized;
}

export function parsePositiveInt(value: string | undefined, fallback: number, max: number): number {
  const parsed = Number(value ?? fallback);
  if (!Number.isInteger(parsed) || parsed < 1 || parsed > max) return fallback;
  return parsed;
}
