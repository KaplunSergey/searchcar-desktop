// Bindings used by this application; keep in sync with the worker's Env.
declare module "cloudflare:workers" {
  export const env: import("./index").Env;
}
