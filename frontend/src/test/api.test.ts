import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, request, setAccessToken } from "../services/api";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const user = { id: "u1", email: "a@b.c", name: "A", created_at: "2026-01-01T00:00:00Z" };

describe("api client", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    setAccessToken(null);
  });

  it("sends the bearer token and parses JSON", async () => {
    setAccessToken("t1");
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(json(200, user));
    await expect(api.me()).resolves.toEqual(user);
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("/api/auth/me");
    expect((init!.headers as Record<string, string>).Authorization).toBe("Bearer t1");
  });

  it("refreshes once on 401 and retries; concurrent calls share one refresh", async () => {
    setAccessToken("expired");
    let refreshes = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === "/api/auth/refresh") {
        refreshes += 1;
        return json(200, { access_token: "fresh", token_type: "bearer", expires_in: 900, user });
      }
      const auth = (init?.headers as Record<string, string>).Authorization;
      return auth === "Bearer fresh"
        ? json(200, user)
        : json(401, { error: { code: "TOKEN_EXPIRED", message: "expired", details: {} } });
    });
    const results = await Promise.all([api.me(), api.me(), api.me()]);
    expect(results).toHaveLength(3);
    expect(refreshes).toBe(1);
  });

  it("surfaces the error envelope as ApiError with field errors", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      json(422, {
        error: {
          code: "VALIDATION_ERROR",
          message: "Invalid request",
          details: { errors: [{ field: "password", message: "Value error, too weak", type: "value_error" }] },
        },
      }),
    );
    const error = await request("/auth/register", { method: "POST", body: {}, auth: false }).catch(
      (e: unknown) => e,
    );
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).code).toBe("VALIDATION_ERROR");
    expect((error as ApiError).fieldErrors()).toEqual({ password: "too weak" });
  });

  it("reports network failures clearly", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(api.projects()).rejects.toMatchObject({ code: "NETWORK_ERROR", status: 0 });
  });
});
