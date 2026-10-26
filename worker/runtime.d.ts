// Bindings used by this application; keep in sync with the worker's Env.
declare module "cloudflare:workers" {
  export const env: import("./index").Env;
}

// This lightweight D1 contract must not depend on another Worker's runtime globals.
interface D1Result<T = Record<string, unknown>> {
  success: boolean;
  results?: T[];
  meta: { changes?: number; last_row_id?: number };
}
interface D1PreparedStatement {
  bind(...values: unknown[]): D1PreparedStatement;
  first<T = Record<string, unknown>>(column?: string): Promise<T | null>;
  all<T = Record<string, unknown>>(): Promise<D1Result<T>>;
  run<T = Record<string, unknown>>(): Promise<D1Result<T>>;
}
interface D1Database {
  prepare(query: string): D1PreparedStatement;
  batch<T = Record<string, unknown>>(statements: D1PreparedStatement[]): Promise<D1Result<T>[]>;
  exec(query: string): Promise<{ count: number; duration: number }>;
}
