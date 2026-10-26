import { WorkerEntrypoint } from "cloudflare:workers";
import { verifyDeviceProof } from "./crypto";
import { ApiError } from "./errors";
import type { Env } from "./types";
import { assertFreshRequest, parseSignedDeviceRequest, requireString } from "./validation";

// Named RPC only: no public HTTP route, lease issuance, activation or database writes.
export class TelegramAuthorization extends WorkerEntrypoint<Env> {
  async authorize(value: unknown): Promise<boolean> {
    try {
      if (new TextEncoder().encode(JSON.stringify(value)).byteLength > 16384) return false;
      const body = parseSignedDeviceRequest(value);
      const now = new Date();
      assertFreshRequest(body.requested_at, now);
      if (body.audience !== "searchcar-telegram-pairing-v1"
          && body.audience !== "searchcar-telegram-delivery-v1") return false;
      await verifyDeviceProof(body);
      const pattern = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/u;
      const license = requireString(body, "license_id", { pattern });
      const device = requireString(body, "device_id", { pattern });
      const row = await this.env.LICENSE_DB.prepare(`SELECT d.public_key,d.fingerprint_hash,d.is_active,
        l.status,l.expires_at,l.perpetual,l.deleted_at FROM devices d JOIN licenses l ON l.id=d.license_id
        WHERE d.id=? AND l.id=?`).bind(device, license).first<{
        public_key: string; fingerprint_hash: string; is_active: number;
        status: string; expires_at: string | null; perpetual: number; deleted_at: string | null;
      }>();
      return !!row && row.public_key === body.device.public_key
        && row.fingerprint_hash === body.device.fingerprint_hash && row.is_active === 1
        && row.status === "ACTIVE" && row.deleted_at === null
        && (row.perpetual === 1 || (!!row.expires_at && Date.parse(row.expires_at) > now.getTime()));
    } catch (error) {
      if (error instanceof ApiError) return false;
      // No raw database error or signed request crosses the service boundary.
      throw new Error("Authorization unavailable");
    }
  }
}
