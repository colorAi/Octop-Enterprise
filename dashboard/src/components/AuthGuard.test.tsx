import { describe, expect, it, vi, beforeEach } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { useEffect } from "react";
import AuthGuard from "./AuthGuard";
import { useCurrentUser } from "../hooks/useCurrentUser";

vi.mock("../api/modules/auth", () => ({
  authApi: {
    getAuthStatus: vi.fn(),
    me: vi.fn(),
  },
}));

vi.mock("../api/request", () => ({
  getAuthToken: vi.fn(() => null),
  clearAuthToken: vi.fn(),
  AUTH_TOKEN_CHANGED_EVENT: "octop:auth-token-changed",
}));

vi.mock("../utils/locale", () => ({
  applyUserLocale: vi.fn(() => Promise.resolve()),
}));

import { authApi } from "../api/modules/auth";
import { getAuthToken } from "../api/request";

describe("AuthGuard offline boot", () => {
  beforeEach(() => {
    vi.mocked(authApi.getAuthStatus).mockReset();
    vi.mocked(authApi.me).mockReset();
    vi.mocked(getAuthToken).mockReset();
    vi.mocked(getAuthToken).mockReturnValue(null);
  });

  it("shows offline panel instead of the protected shell when setup/status fails", async () => {
    vi.mocked(authApi.getAuthStatus).mockRejectedValue(
      new TypeError("Failed to fetch"),
    );

    render(
      <MemoryRouter>
        <AuthGuard>
          <div>protected-shell</div>
        </AuthGuard>
      </MemoryRouter>,
    );

    const alert = await screen.findByRole("alert");
    expect(alert).toBeInTheDocument();
    expect(alert.textContent).toMatch(
      /errors\.offlineTitle|Cannot reach|无法连接/,
    );
    expect(
      screen.getByRole("button", { name: /errors\.retry|Retry|重试/ }),
    ).toBeInTheDocument();
    expect(screen.queryByText("protected-shell")).not.toBeInTheDocument();
  });

  it("keeps the authenticated shell mounted across in-app navigations", async () => {
    vi.mocked(getAuthToken).mockReturnValue("tok");
    vi.mocked(authApi.getAuthStatus).mockResolvedValue({
      setup_required: false,
      has_admin: true,
    } as never);
    vi.mocked(authApi.me).mockResolvedValue({
      user_id: 1,
      username: "admin",
      role: "admin",
      locale: "zh",
    } as never);

    function NavProbe() {
      const navigate = useNavigate();
      useEffect(() => {
        navigate("/b");
      }, [navigate]);
      return <div>protected-shell</div>;
    }

    render(
      <MemoryRouter initialEntries={["/a"]}>
        <AuthGuard>
          <Routes>
            <Route path="/a" element={<NavProbe />} />
            <Route path="/b" element={<div>protected-shell</div>} />
          </Routes>
        </AuthGuard>
      </MemoryRouter>,
    );

    await waitFor(() =>
      expect(screen.getByText("protected-shell")).toBeInTheDocument(),
    );
    await waitFor(() => {
      expect(authApi.getAuthStatus).toHaveBeenCalledTimes(1);
    });
    // Give route-driven navigate identity churn a tick; gate must not re-run.
    await waitFor(() => {
      expect(screen.getByText("protected-shell")).toBeInTheDocument();
    });
    expect(authApi.getAuthStatus).toHaveBeenCalledTimes(1);
    expect(authApi.me).toHaveBeenCalledTimes(1);
  });

  it("retries auth from the offline panel without leaving a blank shell", async () => {
    vi.mocked(authApi.getAuthStatus)
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValue({
        setup_required: false,
        has_admin: true,
      } as never);
    vi.mocked(getAuthToken).mockReturnValue("tok");
    vi.mocked(authApi.me).mockResolvedValue({
      user_id: 1,
      username: "admin",
      role: "admin",
      locale: "zh",
    } as never);

    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <AuthGuard>
          <div>protected-shell</div>
        </AuthGuard>
      </MemoryRouter>,
    );

    const retry = await screen.findByRole("button", {
      name: /errors\.retry|Retry|重试/,
    });
    await user.click(retry);
    expect(await screen.findByText("protected-shell")).toBeInTheDocument();
  });

  it("reloads the user and unmounts old account state after a cross-tab account switch", async () => {
    const token = (sub: number, iat = 0) =>
      `header.${btoa(JSON.stringify({ sub, sid: `s${sub}`, iat }))}.sig`;
    vi.mocked(getAuthToken).mockReturnValue(token(1));
    vi.mocked(authApi.getAuthStatus).mockResolvedValue({
      setup_required: false,
      has_admin: true,
    } as never);
    vi.mocked(authApi.me)
      .mockResolvedValueOnce({
        id: 1,
        username: "admin",
        role: "admin",
        locale: "zh",
      } as never)
      .mockResolvedValueOnce({
        id: 2,
        username: "employee",
        role: "user",
        locale: "zh",
      } as never);
    let mounts = 0;
    function Account() {
      const user = useCurrentUser();
      useEffect(() => {
        mounts++;
      }, []);
      return <div>{user?.username}</div>;
    }
    render(
      <MemoryRouter>
        <AuthGuard>
          <Account />
        </AuthGuard>
      </MemoryRouter>,
    );
    await screen.findByText("admin");
    act(() => {
      vi.mocked(getAuthToken).mockReturnValue(token(1, 10));
      window.dispatchEvent(new StorageEvent("storage", { key: "auth_token" }));
    });
    expect(authApi.me).toHaveBeenCalledTimes(1);
    expect(mounts).toBe(1);
    act(() => {
      vi.mocked(getAuthToken).mockReturnValue(token(2));
      window.dispatchEvent(new StorageEvent("storage", { key: "auth_token" }));
    });
    expect(screen.queryByText("admin")).toBeNull();
    await screen.findByText("employee");
    expect(mounts).toBe(2);
  });
});
