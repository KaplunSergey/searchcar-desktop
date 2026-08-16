import type { Env, LeasePayload, SignedDeviceRequest } from "./types";
import { ApiError } from "./errors";

const encoder = new TextEncoder();

export function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const record = value as Record<string, unknown>;
  const keys = Object.keys(record).sort();
  return `{${keys
    .filter((key) => record[key] !== undefined)
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`)
    .join(",")}}`;
}

export function base64UrlEncode(bytes: ArrayBuffer | Uint8Array): string {
  const view = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  let binary = "";
  for (const byte of view) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/u, "");
}

export function base64UrlDecode(value: string): ArrayBuffer {
  if (!/^[A-Za-z0-9_-]+$/u.test(value)) {
    throw new ApiError(400, "INVALID_ENCODING", "A base64url value is invalid.");
  }
  const padded = value.replaceAll("-", "+").replaceAll("_", "/").padEnd(
    Math.ceil(value.length / 4) * 4,
    "=",
  );
  try {
    const binary = atob(padded);
    return Uint8Array.from(binary, (character) => character.charCodeAt(0)).buffer as ArrayBuffer;
  } catch {
    throw new ApiError(400, "INVALID_ENCODING", "A base64url value is invalid.");
  }
}

export async function sha256(value: string): Promise<string> {
  return base64UrlEncode(await crypto.subtle.digest("SHA-256", encoder.encode(value)));
}

export async function pepperedHash(pepper: string, value: string): Promise<string> {
  return sha256(`${pepper.length}:${pepper}:${value}`);
}

async function idempotencyEncryptionKey(pepper: string): Promise<CryptoKey> {
  const material = await crypto.subtle.digest(
    "SHA-256",
    encoder.encode(`SEARCHCAR-IDEMPOTENCY-ENCRYPTION-V1:${pepper}`),
  );
  return crypto.subtle.importKey("raw", material, { name: "AES-GCM" }, false, [
    "encrypt",
    "decrypt",
  ]);
}

export async function sealIdempotencyResponse(pepper: string, value: string): Promise<string> {
  const nonce = new Uint8Array(12);
  crypto.getRandomValues(nonce);
  const ciphertext = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv: nonce, additionalData: encoder.encode("searchcar-idempotency-v1") },
    await idempotencyEncryptionKey(pepper),
    encoder.encode(value),
  );
  return `v1.${base64UrlEncode(nonce)}.${base64UrlEncode(ciphertext)}`;
}

export async function openIdempotencyResponse(pepper: string, sealed: string): Promise<string> {
  const [version, nonceValue, ciphertextValue, ...extra] = sealed.split(".");
  if (version !== "v1" || !nonceValue || !ciphertextValue || extra.length > 0) {
    throw new ApiError(500, "IDEMPOTENCY_STATE_INVALID", "Stored request state is invalid.");
  }
  try {
    const plaintext = await crypto.subtle.decrypt(
      {
        name: "AES-GCM",
        iv: base64UrlDecode(nonceValue),
        additionalData: encoder.encode("searchcar-idempotency-v1"),
      },
      await idempotencyEncryptionKey(pepper),
      base64UrlDecode(ciphertextValue),
    );
    return new TextDecoder().decode(plaintext);
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(500, "IDEMPOTENCY_STATE_INVALID", "Stored request state is invalid.");
  }
}

export async function constantTimeEqual(left: string, right: string): Promise<boolean> {
  const [leftHash, rightHash] = await Promise.all([sha256(left), sha256(right)]);
  if (leftHash.length !== rightHash.length) return false;
  let difference = 0;
  for (let index = 0; index < leftHash.length; index += 1) {
    difference |= leftHash.charCodeAt(index) ^ rightHash.charCodeAt(index);
  }
  return difference === 0;
}

export function deviceProofPayload(request: SignedDeviceRequest): string {
  const unsigned = { ...request } as Record<string, unknown>;
  delete unsigned.proof;
  return `SEARCHCAR-DEVICE-REQUEST-V1\n${canonicalJson(unsigned)}`;
}

export async function verifyDeviceProof(request: SignedDeviceRequest): Promise<void> {
  const publicKeyBytes = base64UrlDecode(request.device.public_key);
  if (publicKeyBytes.byteLength !== 32) {
    throw new ApiError(400, "INVALID_DEVICE_KEY", "The device public key is invalid.");
  }
  const fingerprintPrefix = encoder.encode("SEARCHCAR-DEVICE-FINGERPRINT-V1\n");
  const fingerprintInput = new Uint8Array(
    fingerprintPrefix.byteLength + publicKeyBytes.byteLength,
  );
  fingerprintInput.set(fingerprintPrefix, 0);
  fingerprintInput.set(new Uint8Array(publicKeyBytes), fingerprintPrefix.byteLength);
  const fingerprintDigest = new Uint8Array(
    await crypto.subtle.digest("SHA-256", fingerprintInput),
  );
  const expectedFingerprint = Array.from(
    fingerprintDigest,
    (byte) => byte.toString(16).padStart(2, "0"),
  ).join("");
  if (request.device.fingerprint_hash !== expectedFingerprint) {
    throw new ApiError(
      400,
      "INVALID_DEVICE_FINGERPRINT",
      "The device fingerprint is invalid.",
    );
  }
  const signature = base64UrlDecode(request.proof);
  if (signature.byteLength !== 64) {
    throw new ApiError(401, "INVALID_DEVICE_PROOF", "The device proof is invalid.");
  }
  let key: CryptoKey;
  try {
    key = await crypto.subtle.importKey("raw", publicKeyBytes, { name: "Ed25519" }, false, [
      "verify",
    ]);
  } catch {
    throw new ApiError(400, "INVALID_DEVICE_KEY", "The device public key is invalid.");
  }
  const valid = await crypto.subtle.verify(
    { name: "Ed25519" },
    key,
    signature,
    encoder.encode(deviceProofPayload(request)),
  );
  if (!valid) {
    throw new ApiError(401, "INVALID_DEVICE_PROOF", "The device proof is invalid.");
  }
}

async function importSigningKey(env: Env): Promise<CryptoKey> {
  const keyBytes = base64UrlDecode(env.LICENSE_SIGNING_PRIVATE_KEY);
  try {
    return await crypto.subtle.importKey("pkcs8", keyBytes, { name: "Ed25519" }, false, [
      "sign",
    ]);
  } catch {
    throw new ApiError(500, "SIGNING_KEY_INVALID", "The signing key is unavailable.");
  }
}

export async function signLease(
  env: Env,
  payload: LeasePayload,
): Promise<{ payload: LeasePayload; signature: string }> {
  const key = await importSigningKey(env);
  const message = encoder.encode(`SEARCHCAR-LICENSE-LEASE-V1\n${canonicalJson(payload)}`);
  const signature = await crypto.subtle.sign({ name: "Ed25519" }, key, message);
  try {
    const publicKeyBytes = base64UrlDecode(env.LICENSE_SIGNING_PUBLIC_KEY);
    if (publicKeyBytes.byteLength !== 32) throw new Error("invalid public key length");
    const publicKey = await crypto.subtle.importKey(
      "raw",
      publicKeyBytes,
      { name: "Ed25519" },
      false,
      ["verify"],
    );
    const matches = await crypto.subtle.verify(
      { name: "Ed25519" },
      publicKey,
      signature,
      message,
    );
    if (!matches) {
      throw new ApiError(500, "SIGNING_KEY_MISMATCH", "The signing key pair does not match.");
    }
  } catch (error) {
    if (error instanceof ApiError && error.code === "SIGNING_KEY_MISMATCH") throw error;
    throw new ApiError(
      500,
      "SIGNING_PUBLIC_KEY_INVALID",
      "The signing public key is unavailable.",
    );
  }
  return { payload, signature: base64UrlEncode(signature) };
}

export function randomToken(bytes = 24): string {
  const value = new Uint8Array(bytes);
  crypto.getRandomValues(value);
  return base64UrlEncode(value);
}

const FRIENDLY_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ";

export function randomFriendlyCode(prefix: "SC" | "TR"): string {
  const bytes = new Uint8Array(20);
  crypto.getRandomValues(bytes);
  let body = "";
  for (let index = 0; index < 20; index += 1) {
    body += FRIENDLY_ALPHABET[bytes[index] % FRIENDLY_ALPHABET.length];
  }
  return `${prefix}-${body.slice(0, 5)}-${body.slice(5, 10)}-${body.slice(10, 15)}-${body.slice(15)}`;
}
