export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

export function asApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error;
  console.error("Unhandled license service error", error);
  return new ApiError(500, "INTERNAL_ERROR", "The request could not be completed.");
}
