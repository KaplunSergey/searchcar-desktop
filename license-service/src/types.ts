export interface Env {
  LICENSE_DB: D1Database;
  LICENSE_SIGNING_PRIVATE_KEY: string;
  LICENSE_SIGNING_PUBLIC_KEY: string;
  LICENSE_SIGNING_KEY_ID: string;
  CODE_PEPPER: string;
  RATE_LIMIT_PEPPER: string;
  ADMIN_API_TOKEN: string;
  LEASE_HOURS?: string;
  TRIAL_DAYS?: string;
  PUBLIC_RATE_LIMIT_PER_MINUTE?: string;
  TRIAL_RATE_LIMIT_PER_HOUR?: string;
}

export interface DeviceProof {
  public_key: string;
  fingerprint_hash: string;
  label?: string;
}

export interface SignedDeviceRequest {
  protocol_version: 1;
  request_id: string;
  requested_at: string;
  app_version: string;
  device: DeviceProof;
  proof: string;
}

export interface LicenseRow {
  id: string;
  kind: "TRIAL" | "SUBSCRIPTION" | "PERPETUAL";
  status: "ACTIVE" | "SUSPENDED";
  started_at: string;
  expires_at: string | null;
  perpetual: number;
  activation_count: number;
}

export interface DeviceRow {
  id: string;
  license_id: string;
  public_key: string;
  fingerprint_hash: string;
  label: string | null;
  is_active: number;
}

export interface ActivationCodeRow {
  id: string;
  license_id: string;
  duration_months: number | null;
  makes_perpetual: number;
  expires_at: string | null;
  used_at: string | null;
}

export interface TransferRow {
  id: string;
  status: "PENDING" | "APPROVED" | "CLAIMED" | "EXPIRED" | "REJECTED";
  claim_token_hash: string;
  requested_public_key: string;
  requested_fingerprint_hash: string;
  requested_label: string | null;
  requested_app_version: string;
  expires_at: string;
  license_id: string | null;
  old_device_id: string | null;
  new_device_id: string | null;
}

export interface LeasePayload {
  type: "searchcar-license-lease";
  protocol_version: 1;
  key_id: string;
  server_time: string;
  issued_at: string;
  license_id: string;
  device_id: string;
  license_type: LicenseRow["kind"];
  subscription_expires_at: string | null;
  lease_expires_at: string;
  entitlements: {
    search: boolean;
    data_access: true;
    backup_restore: true;
  };
  app_version: string;
}

export interface ApiEnvelope<T = unknown> {
  ok: boolean;
  data?: T;
  error?: {
    code: string;
    message: string;
  };
}
