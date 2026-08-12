import { auditStatement, ipHash } from "./database";
import { constantTimeEqual, pepperedHash, randomFriendlyCode, randomToken, signLease } from "./crypto";
import { ApiError } from "./errors";
import type {
  DeviceRow,
  Env,
  LeasePayload,
  LicenseRow,
  SignedDeviceRequest,
  TransferRow,
} from "./types";
import { asObject, normalizeCode, optionalString, requireString } from "./validation";

interface LeaseSource {
  license: LicenseRow;
  device: DeviceRow;
  appVersion: string;
  now: Date;
}

interface AdminCodeRow extends LicenseRow {
  activation_code_id: string;
  code_license_id: string;
  duration_months: number | null;
  makes_perpetual: number;
  code_expires_at: string | null;
  used_at: string | null;
}

export function addCalendarMonths(date: Date, months: number): Date {
  const result = new Date(date.getTime());
  const originalDay = result.getUTCDate();
  result.setUTCDate(1);
  result.setUTCMonth(result.getUTCMonth() + months);
  const lastDay = new Date(
    Date.UTC(result.getUTCFullYear(), result.getUTCMonth() + 1, 0),
  ).getUTCDate();
  result.setUTCDate(Math.min(originalDay, lastDay));
  return result;
}

function leaseHours(env: Env): number {
  const parsed = Number(env.LEASE_HOURS ?? "48");
  return Number.isInteger(parsed) && parsed >= 24 && parsed <= 72 ? parsed : 48;
}

function trialDays(env: Env): number {
  const parsed = Number(env.TRIAL_DAYS ?? "30");
  return Number.isInteger(parsed) && parsed >= 1 && parsed <= 90 ? parsed : 30;
}

function canSearch(license: LicenseRow, now: Date): boolean {
  if (license.status !== "ACTIVE") return false;
  if (license.perpetual === 1) return true;
  return license.expires_at !== null && new Date(license.expires_at).getTime() > now.getTime();
}

async function createLease(env: Env, source: LeaseSource) {
  const nowIso = source.now.toISOString();
  const payload: LeasePayload = {
    type: "searchcar-license-lease",
    protocol_version: 1,
    key_id: env.LICENSE_SIGNING_KEY_ID,
    server_time: nowIso,
    issued_at: nowIso,
    license_id: source.license.id,
    device_id: source.device.id,
    license_type: source.license.kind,
    subscription_expires_at: source.license.expires_at,
    lease_expires_at: new Date(
      source.now.getTime() + leaseHours(env) * 60 * 60 * 1000,
    ).toISOString(),
    entitlements: {
      search: canSearch(source.license, source.now),
      data_access: true,
      backup_restore: true,
    },
    app_version: source.appVersion,
  };
  return signLease(env, payload);
}

function isConstraintError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error);
  return /UNIQUE constraint failed|constraint failed/iu.test(message);
}

async function findActiveDevice(env: Env, licenseId: string): Promise<DeviceRow | null> {
  return env.LICENSE_DB.prepare(
    `SELECT id, license_id, public_key, fingerprint_hash, label, is_active
       FROM devices WHERE license_id = ? AND is_active = 1`,
  )
    .bind(licenseId)
    .first<DeviceRow>();
}

function assertDeviceMatches(device: DeviceRow, request: SignedDeviceRequest): void {
  if (
    device.is_active !== 1 ||
    device.public_key !== request.device.public_key ||
    device.fingerprint_hash !== request.device.fingerprint_hash
  ) {
    throw new ApiError(403, "LICENSE_NOT_AVAILABLE", "The license is not available for this device.");
  }
}

export async function activateTrial(
  _request: Request,
  env: Env,
  body: SignedDeviceRequest & Record<string, unknown>,
  now: Date,
) {
  const subjectHash = requireString(body, "subject_hash", {
    min: 64,
    max: 64,
    pattern: /^[a-f0-9]{64}$/u,
  });
  const existing = await env.LICENSE_DB.prepare(
    `SELECT license_id FROM trial_claims
      WHERE subject_hash = ? OR fingerprint_hash = ?
     UNION ALL
     SELECT license_id FROM devices WHERE fingerprint_hash = ?
     LIMIT 1`,
  )
    .bind(subjectHash, body.device.fingerprint_hash, body.device.fingerprint_hash)
    .first();
  if (existing) {
    throw new ApiError(409, "TRIAL_UNAVAILABLE", "A trial cannot be activated for this subject or device.");
  }

  const licenseId = crypto.randomUUID();
  const deviceId = crypto.randomUUID();
  const nowIso = now.toISOString();
  const expiresAt = new Date(now.getTime() + trialDays(env) * 24 * 60 * 60 * 1000).toISOString();
  const license: LicenseRow = {
    id: licenseId,
    kind: "TRIAL",
    status: "ACTIVE",
    started_at: nowIso,
    expires_at: expiresAt,
    perpetual: 0,
    activation_count: 1,
  };
  const device: DeviceRow = {
    id: deviceId,
    license_id: licenseId,
    public_key: body.device.public_key,
    fingerprint_hash: body.device.fingerprint_hash,
    label: body.device.label ?? null,
    is_active: 1,
  };
  try {
    await env.LICENSE_DB.batch([
      env.LICENSE_DB.prepare(
        `INSERT INTO licenses
           (id, kind, status, started_at, expires_at, perpetual, activation_count,
            created_at, updated_at)
         VALUES (?, 'TRIAL', 'ACTIVE', ?, ?, 0, 1, ?, ?)`,
      ).bind(licenseId, nowIso, expiresAt, nowIso, nowIso),
      env.LICENSE_DB.prepare(
        `INSERT INTO devices
           (id, license_id, public_key, fingerprint_hash, label, is_active,
            first_seen_at, last_seen_at, created_at)
         VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)`,
      ).bind(
        deviceId,
        licenseId,
        device.public_key,
        device.fingerprint_hash,
        device.label,
        nowIso,
        nowIso,
        nowIso,
      ),
      env.LICENSE_DB.prepare(
        `INSERT INTO trial_claims (license_id, subject_hash, fingerprint_hash, claimed_at)
         VALUES (?, ?, ?, ?)`,
      ).bind(licenseId, subjectHash, device.fingerprint_hash, nowIso),
      auditStatement(env, {
        id: crypto.randomUUID(),
        dedupeKey: `trial:${licenseId}`,
        actorType: "DEVICE",
        actorId: deviceId,
        action: "TRIAL_ACTIVATED",
        targetType: "LICENSE",
        targetId: licenseId,
        createdAt: nowIso,
      }),
    ]);
  } catch (error) {
    if (isConstraintError(error)) {
      throw new ApiError(409, "TRIAL_UNAVAILABLE", "A trial cannot be activated for this subject or device.");
    }
    throw error;
  }
  const lease = await createLease(env, { license, device, appVersion: body.app_version, now });
  return { license_id: licenseId, device_id: deviceId, lease };
}

export async function checkLicense(
  request: Request,
  env: Env,
  body: SignedDeviceRequest & Record<string, unknown>,
  now: Date,
) {
  const licenseId = requireString(body, "license_id", { min: 36, max: 36 });
  const deviceId = requireString(body, "device_id", { min: 36, max: 36 });
  const license = await env.LICENSE_DB.prepare(
    `SELECT id, kind, status, started_at, expires_at, perpetual, activation_count
       FROM licenses WHERE id = ?`,
  )
    .bind(licenseId)
    .first<LicenseRow>();
  const device = await env.LICENSE_DB.prepare(
    `SELECT id, license_id, public_key, fingerprint_hash, label, is_active
       FROM devices WHERE id = ? AND license_id = ?`,
  )
    .bind(deviceId, licenseId)
    .first<DeviceRow>();
  if (!license || !device) {
    throw new ApiError(403, "LICENSE_NOT_AVAILABLE", "The license is not available for this device.");
  }
  assertDeviceMatches(device, body);
  const nowIso = now.toISOString();
  await env.LICENSE_DB.batch([
    env.LICENSE_DB.prepare(
      `UPDATE devices SET last_seen_at = ? WHERE id = ? AND is_active = 1`,
    ).bind(nowIso, deviceId),
    env.LICENSE_DB.prepare(
      `INSERT INTO license_checks
         (device_id, license_id, last_checked_at, app_version, check_count, last_ip_hash)
       VALUES (?, ?, ?, ?, 1, ?)
       ON CONFLICT(device_id) DO UPDATE SET
         last_checked_at = excluded.last_checked_at,
         app_version = excluded.app_version,
         check_count = license_checks.check_count + 1,
         last_ip_hash = excluded.last_ip_hash`,
    ).bind(deviceId, licenseId, nowIso, body.app_version, await ipHash(request, env)),
  ]);
  return { lease: await createLease(env, { license, device, appVersion: body.app_version, now }) };
}

export async function redeemActivationCode(
  _request: Request,
  env: Env,
  body: SignedDeviceRequest & Record<string, unknown>,
  now: Date,
) {
  const code = normalizeCode(requireString(body, "activation_code", { min: 10, max: 40 }), "SC");
  const codeHash = await pepperedHash(env.CODE_PEPPER, code);
  const row = await env.LICENSE_DB.prepare(
    `SELECT
       ac.id AS activation_code_id, ac.license_id AS code_license_id, ac.duration_months,
       ac.makes_perpetual, ac.expires_at AS code_expires_at, ac.used_at,
       l.id, l.kind, l.status, l.started_at, l.expires_at, l.perpetual,
       l.activation_count
     FROM activation_codes ac
     JOIN licenses l ON l.id = ac.license_id
     WHERE ac.code_hash = ?`,
  )
    .bind(codeHash)
    .first<AdminCodeRow>();
  if (
    !row ||
    row.used_at !== null ||
    row.status !== "ACTIVE" ||
    (row.code_expires_at !== null && new Date(row.code_expires_at).getTime() <= now.getTime())
  ) {
    throw new ApiError(409, "CODE_NOT_AVAILABLE", "The activation code cannot be used.");
  }
  let device = await findActiveDevice(env, row.code_license_id);
  const newBinding = device === null;
  if (device) {
    assertDeviceMatches(device, body);
  } else {
    device = {
      id: crypto.randomUUID(),
      license_id: row.code_license_id,
      public_key: body.device.public_key,
      fingerprint_hash: body.device.fingerprint_hash,
      label: body.device.label ?? null,
      is_active: 1,
    };
  }

  const nowIso = now.toISOString();
  const oldExpiresAt = row.expires_at;
  const oldPerpetual = row.perpetual;
  const newPerpetual = row.makes_perpetual === 1 ? 1 : oldPerpetual;
  const newExpiresAt =
    newPerpetual === 1
      ? null
      : addCalendarMonths(
          oldExpiresAt && new Date(oldExpiresAt).getTime() > now.getTime()
            ? new Date(oldExpiresAt)
            : now,
          row.duration_months ?? 0,
        ).toISOString();
  const renewalId = crypto.randomUUID();
  const statements: D1PreparedStatement[] = [];
  if (newBinding) {
    statements.push(
      env.LICENSE_DB.prepare(
        `INSERT INTO devices
           (id, license_id, public_key, fingerprint_hash, label, is_active,
            first_seen_at, last_seen_at, created_at)
         VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)`,
      ).bind(
        device.id,
        device.license_id,
        device.public_key,
        device.fingerprint_hash,
        device.label,
        nowIso,
        nowIso,
        nowIso,
      ),
    );
  }
  statements.push(
    env.LICENSE_DB.prepare(
      `UPDATE activation_codes
          SET used_at = ?, used_by_device_id = ?
        WHERE id = ? AND used_at IS NULL`,
    ).bind(nowIso, device.id, row.activation_code_id),
    env.LICENSE_DB.prepare(
      `INSERT INTO renewals
         (id, license_id, activation_code_id, old_expires_at, new_expires_at,
          old_perpetual, new_perpetual, actor, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, 'DEVICE', ?)`,
    ).bind(
      renewalId,
      row.code_license_id,
      row.activation_code_id,
      oldExpiresAt,
      newExpiresAt,
      oldPerpetual,
      newPerpetual,
      nowIso,
    ),
    env.LICENSE_DB.prepare(
      `UPDATE licenses
          SET kind = ?, expires_at = ?, perpetual = ?,
              activation_count = activation_count + ?, updated_at = ?
        WHERE id = ? AND status = 'ACTIVE'`,
    ).bind(
      newPerpetual === 1 ? "PERPETUAL" : "SUBSCRIPTION",
      newExpiresAt,
      newPerpetual,
      newBinding ? 1 : 0,
      nowIso,
      row.code_license_id,
    ),
    auditStatement(env, {
      id: crypto.randomUUID(),
      dedupeKey: `redeem:${row.activation_code_id}`,
      actorType: "DEVICE",
      actorId: device.id,
      action: "ACTIVATION_CODE_REDEEMED",
      targetType: "LICENSE",
      targetId: row.code_license_id,
      metadata: { renewal_id: renewalId, new_binding: newBinding },
      createdAt: nowIso,
    }),
  );
  try {
    await env.LICENSE_DB.batch(statements);
  } catch (error) {
    if (isConstraintError(error)) {
      throw new ApiError(409, "CODE_NOT_AVAILABLE", "The activation code cannot be used.");
    }
    throw error;
  }

  const license: LicenseRow = {
    id: row.code_license_id,
    kind: newPerpetual === 1 ? "PERPETUAL" : "SUBSCRIPTION",
    status: row.status,
    started_at: row.started_at,
    expires_at: newExpiresAt,
    perpetual: newPerpetual,
    activation_count: row.activation_count + (newBinding ? 1 : 0),
  };
  return {
    license_id: license.id,
    device_id: device.id,
    lease: await createLease(env, { license, device, appVersion: body.app_version, now }),
  };
}

export async function requestTransfer(
  _request: Request,
  env: Env,
  body: SignedDeviceRequest & Record<string, unknown>,
  now: Date,
) {
  const transferId = crypto.randomUUID();
  const transferCode = randomFriendlyCode("TR");
  const claimToken = randomToken(24);
  const nowIso = now.toISOString();
  const expiresAt = new Date(now.getTime() + 30 * 60 * 1000).toISOString();
  await env.LICENSE_DB.batch([
    env.LICENSE_DB.prepare(
      `INSERT INTO device_transfers
         (id, public_code_hash, public_code_hint, claim_token_hash,
          requested_public_key, requested_fingerprint_hash, requested_label,
          requested_app_version, status, requested_at, expires_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)`,
    ).bind(
      transferId,
      await pepperedHash(env.CODE_PEPPER, transferCode),
      transferCode.slice(-5),
      await pepperedHash(env.CODE_PEPPER, claimToken),
      body.device.public_key,
      body.device.fingerprint_hash,
      body.device.label ?? null,
      body.app_version,
      nowIso,
      expiresAt,
    ),
    auditStatement(env, {
      id: crypto.randomUUID(),
      dedupeKey: `transfer-request:${transferId}`,
      actorType: "DEVICE",
      action: "TRANSFER_REQUESTED",
      targetType: "DEVICE_TRANSFER",
      targetId: transferId,
      createdAt: nowIso,
    }),
  ]);
  return { transfer_code: transferCode, claim_token: claimToken, expires_at: expiresAt };
}

export async function claimTransfer(
  _request: Request,
  env: Env,
  body: SignedDeviceRequest & Record<string, unknown>,
  now: Date,
) {
  const transferCode = normalizeCode(requireString(body, "transfer_code", { max: 40 }), "TR");
  const claimToken = requireString(body, "claim_token", {
    min: 32,
    max: 64,
    pattern: /^[A-Za-z0-9_-]+$/u,
  });
  const transfer = await env.LICENSE_DB.prepare(
    `SELECT id, status, claim_token_hash, requested_public_key,
            requested_fingerprint_hash, requested_label, requested_app_version,
            expires_at, license_id, old_device_id, new_device_id
       FROM device_transfers WHERE public_code_hash = ?`,
  )
    .bind(await pepperedHash(env.CODE_PEPPER, transferCode))
    .first<TransferRow>();
  if (
    !transfer ||
    transfer.status === "PENDING" ||
    (transfer.status !== "APPROVED" && transfer.status !== "CLAIMED") ||
    new Date(transfer.expires_at).getTime() <= now.getTime() ||
    !(await constantTimeEqual(
      transfer.claim_token_hash,
      await pepperedHash(env.CODE_PEPPER, claimToken),
    )) ||
    transfer.requested_public_key !== body.device.public_key ||
    transfer.requested_fingerprint_hash !== body.device.fingerprint_hash ||
    !transfer.license_id ||
    !transfer.new_device_id
  ) {
    if (transfer?.status === "PENDING") {
      throw new ApiError(409, "TRANSFER_PENDING", "The transfer is waiting for owner approval.");
    }
    throw new ApiError(409, "TRANSFER_NOT_AVAILABLE", "The transfer cannot be claimed.");
  }
  const license = await env.LICENSE_DB.prepare(
    `SELECT id, kind, status, started_at, expires_at, perpetual, activation_count
       FROM licenses WHERE id = ?`,
  )
    .bind(transfer.license_id)
    .first<LicenseRow>();
  const device = await env.LICENSE_DB.prepare(
    `SELECT id, license_id, public_key, fingerprint_hash, label, is_active
       FROM devices WHERE id = ? AND license_id = ?`,
  )
    .bind(transfer.new_device_id, transfer.license_id)
    .first<DeviceRow>();
  if (!license || !device) {
    throw new ApiError(409, "TRANSFER_NOT_AVAILABLE", "The transfer cannot be claimed.");
  }
  assertDeviceMatches(device, body);
  const nowIso = now.toISOString();
  if (transfer.status === "APPROVED") {
    await env.LICENSE_DB.batch([
      env.LICENSE_DB.prepare(
        `UPDATE device_transfers SET status = 'CLAIMED', claimed_at = ?
          WHERE id = ? AND status = 'APPROVED'`,
      ).bind(nowIso, transfer.id),
      auditStatement(env, {
        id: crypto.randomUUID(),
        dedupeKey: `transfer-claim:${transfer.id}`,
        actorType: "DEVICE",
        actorId: device.id,
        action: "TRANSFER_CLAIMED",
        targetType: "DEVICE_TRANSFER",
        targetId: transfer.id,
        createdAt: nowIso,
      }),
    ]);
  }
  return {
    license_id: license.id,
    device_id: device.id,
    lease: await createLease(env, { license, device, appVersion: body.app_version, now }),
  };
}

export async function latestRelease(env: Env) {
  const release = await env.LICENSE_DB.prepare(
    `SELECT version, channel, minimum_version, published_at, metadata_json
       FROM app_releases WHERE channel = 'stable' AND is_current = 1`,
  ).first<{
    version: string;
    channel: string;
    minimum_version: string | null;
    published_at: string;
    metadata_json: string;
  }>();
  if (!release) throw new ApiError(404, "RELEASE_NOT_CONFIGURED", "No release is configured.");
  return {
    version: release.version,
    channel: release.channel,
    minimum_version: release.minimum_version,
    published_at: release.published_at,
    metadata: JSON.parse(release.metadata_json) as unknown,
  };
}

export async function createAdminCustomer(env: Env, value: unknown, now: Date) {
  const body = asObject(value);
  const id = crypto.randomUUID();
  const displayName = requireString(body, "display_name", { min: 1, max: 120 });
  const contact = optionalString(body, "contact", { max: 240 }) ?? null;
  const nowIso = now.toISOString();
  await env.LICENSE_DB.batch([
    env.LICENSE_DB.prepare(
      `INSERT INTO customers
         (id, display_name, contact, contact_hash, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?)`,
    ).bind(
      id,
      displayName,
      contact,
      contact ? await pepperedHash(env.CODE_PEPPER, contact.trim().toLowerCase()) : null,
      nowIso,
      nowIso,
    ),
    auditStatement(env, {
      id: crypto.randomUUID(),
      dedupeKey: `customer-create:${id}`,
      actorType: "ADMIN",
      actorId: "bootstrap-token",
      action: "CUSTOMER_CREATED",
      targetType: "CUSTOMER",
      targetId: id,
      createdAt: nowIso,
    }),
  ]);
  return { customer_id: id };
}

export async function createAdminLicense(env: Env, value: unknown, now: Date) {
  const body = asObject(value);
  const customerId = requireString(body, "customer_id", { min: 36, max: 36 });
  const customer = await env.LICENSE_DB.prepare("SELECT id FROM customers WHERE id = ?")
    .bind(customerId)
    .first();
  if (!customer) throw new ApiError(404, "CUSTOMER_NOT_FOUND", "Customer was not found.");
  const id = crypto.randomUUID();
  const nowIso = now.toISOString();
  await env.LICENSE_DB.batch([
    env.LICENSE_DB.prepare(
      `INSERT INTO licenses
         (id, customer_id, kind, status, started_at, expires_at, perpetual,
          activation_count, created_at, updated_at)
       VALUES (?, ?, 'SUBSCRIPTION', 'ACTIVE', ?, ?, 0, 0, ?, ?)`,
    ).bind(id, customerId, nowIso, nowIso, nowIso, nowIso),
    auditStatement(env, {
      id: crypto.randomUUID(),
      dedupeKey: `license-create:${id}`,
      actorType: "ADMIN",
      actorId: "bootstrap-token",
      action: "LICENSE_CREATED",
      targetType: "LICENSE",
      targetId: id,
      createdAt: nowIso,
    }),
  ]);
  return { license_id: id, expires_at: nowIso };
}

export async function createAdminActivationCode(env: Env, value: unknown, now: Date) {
  const body = asObject(value);
  const licenseId = requireString(body, "license_id", { min: 36, max: 36 });
  const duration = requireString(body, "duration", { min: 3, max: 10 });
  const durationMap: Record<string, number | null> = {
    P1M: 1,
    P3M: 3,
    P6M: 6,
    P12M: 12,
    PERPETUAL: null,
  };
  if (!(duration in durationMap)) {
    throw new ApiError(400, "INVALID_DURATION", "duration must be P1M, P3M, P6M, P12M or PERPETUAL.");
  }
  const license = await env.LICENSE_DB.prepare("SELECT id FROM licenses WHERE id = ?")
    .bind(licenseId)
    .first();
  if (!license) throw new ApiError(404, "LICENSE_NOT_FOUND", "License was not found.");
  const code = randomFriendlyCode("SC");
  const id = crypto.randomUUID();
  const nowIso = now.toISOString();
  const expiresAt = new Date(now.getTime() + 30 * 24 * 60 * 60 * 1000).toISOString();
  await env.LICENSE_DB.batch([
    env.LICENSE_DB.prepare(
      `INSERT INTO activation_codes
         (id, license_id, code_hash, code_hint, duration_months, makes_perpetual,
          expires_at, created_by, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, 'bootstrap-token', ?)`,
    ).bind(
      id,
      licenseId,
      await pepperedHash(env.CODE_PEPPER, code),
      code.slice(-5),
      durationMap[duration],
      duration === "PERPETUAL" ? 1 : 0,
      expiresAt,
      nowIso,
    ),
    auditStatement(env, {
      id: crypto.randomUUID(),
      dedupeKey: `activation-code-create:${id}`,
      actorType: "ADMIN",
      actorId: "bootstrap-token",
      action: "ACTIVATION_CODE_CREATED",
      targetType: "ACTIVATION_CODE",
      targetId: id,
      metadata: { license_id: licenseId, duration },
      createdAt: nowIso,
    }),
  ]);
  return { activation_code_id: id, activation_code: code, expires_at: expiresAt };
}

export async function approveAdminTransfer(env: Env, value: unknown, now: Date) {
  const body = asObject(value);
  const transferCode = normalizeCode(requireString(body, "transfer_code", { max: 40 }), "TR");
  const licenseId = requireString(body, "license_id", { min: 36, max: 36 });
  const transfer = await env.LICENSE_DB.prepare(
    `SELECT id, status, claim_token_hash, requested_public_key,
            requested_fingerprint_hash, requested_label, requested_app_version,
            expires_at, license_id, old_device_id, new_device_id
       FROM device_transfers WHERE public_code_hash = ?`,
  )
    .bind(await pepperedHash(env.CODE_PEPPER, transferCode))
    .first<TransferRow>();
  if (
    !transfer ||
    transfer.status !== "PENDING" ||
    new Date(transfer.expires_at).getTime() <= now.getTime()
  ) {
    throw new ApiError(409, "TRANSFER_NOT_AVAILABLE", "The transfer cannot be approved.");
  }
  const oldDevice = await findActiveDevice(env, licenseId);
  if (!oldDevice) throw new ApiError(409, "LICENSE_NOT_BOUND", "The license has no active device.");
  const newDeviceId = crypto.randomUUID();
  const nowIso = now.toISOString();
  try {
    await env.LICENSE_DB.batch([
      env.LICENSE_DB.prepare(
        `UPDATE device_transfers
            SET status = 'APPROVED', license_id = ?, old_device_id = ?,
                new_device_id = ?, approved_at = ?, approved_by = 'bootstrap-token'
          WHERE id = ? AND status = 'PENDING'`,
      ).bind(licenseId, oldDevice.id, newDeviceId, nowIso, transfer.id),
      env.LICENSE_DB.prepare(
        `UPDATE devices
            SET is_active = 0, deactivated_at = ?
          WHERE id = ? AND license_id = ? AND is_active = 1`,
      ).bind(nowIso, oldDevice.id, licenseId),
      env.LICENSE_DB.prepare(
        `INSERT INTO devices
           (id, license_id, public_key, fingerprint_hash, label, is_active,
            first_seen_at, last_seen_at, created_at)
         VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)`,
      ).bind(
        newDeviceId,
        licenseId,
        transfer.requested_public_key,
        transfer.requested_fingerprint_hash,
        transfer.requested_label,
        nowIso,
        nowIso,
        nowIso,
      ),
      env.LICENSE_DB.prepare(
        "UPDATE licenses SET activation_count = activation_count + 1, updated_at = ? WHERE id = ?",
      ).bind(nowIso, licenseId),
      auditStatement(env, {
        id: crypto.randomUUID(),
        dedupeKey: `transfer-approve:${transfer.id}`,
        actorType: "ADMIN",
        actorId: "bootstrap-token",
        action: "TRANSFER_APPROVED",
        targetType: "DEVICE_TRANSFER",
        targetId: transfer.id,
        metadata: { license_id: licenseId, old_device_id: oldDevice.id, new_device_id: newDeviceId },
        createdAt: nowIso,
      }),
    ]);
  } catch (error) {
    if (isConstraintError(error)) {
      throw new ApiError(409, "TRANSFER_NOT_AVAILABLE", "The transfer cannot be approved.");
    }
    throw error;
  }
  return { transfer_id: transfer.id, license_id: licenseId, new_device_id: newDeviceId };
}
