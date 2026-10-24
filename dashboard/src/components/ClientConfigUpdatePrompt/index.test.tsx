import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { useState } from "react";
import type { OctopUser } from "../../api/modules/auth";
import type { ClientConfigStatus } from "../../api/modules/clientConfig";
import {
  CurrentUserProvider,
  useCurrentUser,
} from "../../hooks/useCurrentUser";
import {
  BEFORE_CLIENT_CONFIG_REFRESH_EVENT,
  CLIENT_CONFIG_CHECK_EVENT,
} from "../../utils/clientConfigUpdate";

const state = vi.hoisted(() => ({
  status: vi.fn(),
  apply: vi.fn(),
  reload: vi.fn(),
  info: vi.fn(),
  error: vi.fn(),
  chatBusy: false,
  desktop: false,
}));
vi.mock("antd", () => ({
  App: {
    useApp: () => ({ message: { info: state.info, error: state.error } }),
  },
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock("../../api/modules/clientConfig", () => ({
  clientConfigApi: { status: state.status, apply: state.apply },
}));
vi.mock("../../api/desktopRuntime", () => ({
  isCompanyDesktop: () => state.desktop,
}));
vi.mock("../../pages/Chat/hooks/chatStore", () => ({
  hasActiveChatWork: () => state.chatBusy,
}));
vi.mock("../../utils/clientConfigUpdate", async (original) => ({
  ...(await original<typeof import("../../utils/clientConfigUpdate")>()),
  reloadClientConfig: state.reload,
}));

import ClientConfigUpdatePrompt from ".";
import { buildNavSections } from "../../layouts/sidebarNav";
import { sectionsFromLayout } from "../../layouts/sidebarNavLayout";

const employee: OctopUser = {
  id: 2,
  username: "employee",
  display_name: null,
  role: "user",
  locale: "zh",
  permissions: ["browser"],
};
const status = (
  revision: string,
  permissions = ["browser"],
  userId = 2,
): ClientConfigStatus => ({
  user_id: userId,
  role: "user",
  permissions,
  revision,
  message: "",
  published_at: null,
});
function Rights() {
  const user = useCurrentUser();
  const sections = sectionsFromLayout(buildNavSections(user), {
    groups: [{ id: "settings" }],
    items: [
      { key: "chat" },
      { key: "personalization", group: "settings" },
      { key: "bridge", hidden: true },
    ],
  });
  return (
    <>
      <div data-testid="rights">{user?.permissions?.join(",")}</div>
      <nav>
        {sections.flatMap((section) =>
          section.items.map((item) => (
            <button key={item.key}>{item.key}</button>
          )),
        )}
      </nav>
    </>
  );
}
function Harness() {
  const [user, setUser] = useState<OctopUser | null>(employee);
  return (
    <CurrentUserProvider user={user} setUser={setUser}>
      <Rights />
      <ClientConfigUpdatePrompt />
      <button onClick={() => setUser({ ...employee, id: 3 })}>
        switch account
      </button>
    </CurrentUserProvider>
  );
}
async function check() {
  await act(async () => {
    window.dispatchEvent(new Event(CLIENT_CONFIG_CHECK_EVENT));
  });
}
async function mount() {
  render(<Harness />);
  await act(async () => {});
}

describe("company configuration updates", () => {
  it("shows content updates missed while offline on the first status check", async () => {
    state.status.mockResolvedValue({
      ...status("source-content-v2"),
      resource_update_pending: true,
    });
    await mount();
    expect(screen.getByRole("status")).toBeTruthy();
    expect(state.apply).not.toHaveBeenCalled();
    await act(async () =>
      fireEvent.click(screen.getByText("clientConfig.apply")),
    );
    expect(state.apply).toHaveBeenCalledOnce();
    expect(state.reload).toHaveBeenCalledOnce();
  });

  it("retains the notice when the server reports work started during the refresh", async () => {
    await mount();
    state.status.mockResolvedValue(status("new-content"));
    await check();
    state.apply.mockRejectedValue(
      new Error(
        JSON.stringify({
          error: { details: { reason: "client_config_busy" } },
        }),
      ),
    );
    await act(async () =>
      fireEvent.click(screen.getByText("clientConfig.apply")),
    );
    expect(state.reload).not.toHaveBeenCalled();
    expect(state.info).toHaveBeenCalledWith("clientConfig.busy");
    expect(screen.getByRole("status")).toBeTruthy();
  });

  it("does not reload after leaving while resource synchronization is pending", async () => {
    const view = render(<Harness />);
    await act(async () => {});
    state.status.mockResolvedValue(status("new-content"));
    await check();
    let complete!: (value: ClientConfigStatus) => void;
    state.apply.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          complete = resolve;
        }),
    );
    await act(async () =>
      fireEvent.click(screen.getByText("clientConfig.apply")),
    );
    view.unmount();
    await act(async () => complete(status("new-content")));
    expect(state.reload).not.toHaveBeenCalled();
  });

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    vi.clearAllMocks();
    state.chatBusy = false;
    state.desktop = false;
    state.status.mockReset().mockResolvedValue(status("first"));
    state.apply.mockReset().mockResolvedValue(status("new"));
  });
  afterEach(() => {
    cleanup();
    delete window.__octopRuntime;
    vi.useRealTimers();
  });

  it("notifies employees without update permission and refreshes only after their click", async () => {
    await mount();
    expect(screen.queryByRole("status")).toBeNull();
    state.status.mockResolvedValue(status("new", ["terminal"]));
    await check();
    expect(screen.getByTestId("rights").textContent).toBe("terminal");
    expect(screen.getByRole("status")).toBeTruthy();
    expect(state.reload).not.toHaveBeenCalled();
    await act(async () => {
      fireEvent.click(screen.getByText("clientConfig.apply"));
    });
    expect(state.reload).toHaveBeenCalledOnce();
  });

  it("finds permission changes even if they happened between login and the first check", async () => {
    state.status.mockResolvedValue(status("changed", []));
    await mount();
    expect(screen.getByTestId("rights").textContent).toBe("");
    expect(screen.getByRole("status")).toBeTruthy();
  });

  it("shows newly authorized menus through the employee's existing custom layout", async () => {
    await mount();
    expect(screen.queryByRole("button", { name: "channels" })).toBeNull();
    state.status.mockResolvedValue(status("granted", ["browser", "channels"]));
    await check();
    expect(screen.getByRole("button", { name: "channels" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "workbench" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "bridge" })).toBeNull();
    await act(async () => {
      fireEvent.click(screen.getByText("clientConfig.apply"));
    });
    expect(state.reload).toHaveBeenCalledOnce();
    state.status.mockResolvedValue(status("revoked", []));
    await check();
    expect(screen.queryByRole("button", { name: "channels" })).toBeNull();
    expect(screen.queryByRole("button", { name: "workbench" })).toBeNull();
  });

  it("keeps a dismissed notice dismissed until a newer revision arrives", async () => {
    await mount();
    state.status.mockResolvedValue(status("models-v2"));
    await check();
    fireEvent.click(screen.getByText("clientConfig.later"));
    await check();
    expect(screen.queryByRole("status")).toBeNull();
    state.status.mockResolvedValue(status("models-v3"));
    await check();
    expect(screen.getByRole("status")).toBeTruthy();
    expect(state.reload).not.toHaveBeenCalled();
  });

  it("checks on focus and falls back to polling after a missed notification", async () => {
    await mount();
    state.status.mockResolvedValue(status("offline-change"));
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
    });
    expect(screen.getByRole("status")).toBeTruthy();
    fireEvent.click(screen.getByText("clientConfig.later"));
    state.status.mockResolvedValue(status("poll-change"));
    await act(async () => {
      vi.advanceTimersByTime(35_000);
    });
    expect(screen.getByRole("status")).toBeTruthy();
  });

  it("coalesces notifications during a slow check and performs one trailing check", async () => {
    let resolve!: (value: ClientConfigStatus) => void;
    state.status.mockImplementationOnce(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    await mount();
    await check();
    await check();
    await act(async () => window.dispatchEvent(new Event("focus")));
    expect(state.status).toHaveBeenCalledTimes(1);
    state.status.mockResolvedValue(status("after-notification"));
    await act(async () => resolve(status("before-notification")));
    expect(state.status).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("status")).toBeTruthy();
  });

  it("jitters polling and keeps the immediate check when returning from offline", async () => {
    vi.spyOn(Math, "random").mockReturnValue(0.8);
    try {
      await mount();
      await act(async () => {
        vi.advanceTimersByTime(30_000);
      });
      expect(state.status).toHaveBeenCalledTimes(1);
      await act(async () => {
        vi.advanceTimersByTime(3_000);
      });
      expect(state.status).toHaveBeenCalledTimes(2);
      await check();
      expect(state.status).toHaveBeenCalledTimes(3);
    } finally {
      vi.mocked(Math.random).mockRestore();
    }
  });

  it("retains a usable session and pending notice when the server is unreachable", async () => {
    await mount();
    state.status.mockResolvedValue(status("new"));
    await check();
    state.status.mockRejectedValue(new Error("offline"));
    await act(async () => {
      fireEvent.click(screen.getByText("clientConfig.apply"));
    });
    expect(state.reload).not.toHaveBeenCalled();
    expect(state.error).toHaveBeenCalledWith("clientConfig.failed");
    expect(screen.getByRole("status")).toBeTruthy();
    expect(screen.getByTestId("rights").textContent).toBe("browser");
  });

  it.each(["stream", "unsent draft resources", "native task"])(
    "blocks refresh during %s",
    async (mode) => {
      await mount();
      state.status.mockResolvedValue(status("new"));
      await check();
      state.chatBusy = mode === "stream";
      const cancel = (event: Event) => event.preventDefault();
      if (mode === "unsent draft resources")
        window.addEventListener(BEFORE_CLIENT_CONFIG_REFRESH_EVENT, cancel);
      if (mode === "native task") {
        state.desktop = true;
        window.__octopRuntime = {
          version: 1,
          status: vi
            .fn()
            .mockResolvedValue({ active_tasks: [{ task_id: "working" }] }),
        } as unknown as typeof window.__octopRuntime;
      }
      await act(async () => {
        fireEvent.click(screen.getByText("clientConfig.apply"));
      });
      window.removeEventListener(BEFORE_CLIENT_CONFIG_REFRESH_EVENT, cancel);
      expect(state.reload).not.toHaveBeenCalled();
      expect(state.info).toHaveBeenCalledWith("clientConfig.busy");
    },
  );

  it("checks native work without stopping the app or invoking cancellation", async () => {
    state.desktop = true;
    const nativeStatus = vi.fn().mockResolvedValue({ active_tasks: [] });
    const startTools = vi
      .fn()
      .mockRejectedValue(new Error("project not paired"));
    window.__octopRuntime = {
      version: 1,
      refreshStatus: nativeStatus,
      status: startTools,
    } as unknown as typeof window.__octopRuntime;
    await mount();
    state.status.mockResolvedValue(status("new"));
    await check();
    await act(async () => {
      fireEvent.click(screen.getByText("clientConfig.apply"));
    });
    expect(nativeStatus).toHaveBeenCalledOnce();
    expect(startTools).not.toHaveBeenCalled();
    expect(state.reload).toHaveBeenCalledOnce();
  });

  it.each(["completed", "failed", "cancelled", "unknown"])(
    "allows refresh when native task history only contains %s receipts",
    async (taskStatus) => {
      state.desktop = true;
      window.__octopRuntime = {
        version: 1,
        refreshStatus: vi.fn().mockResolvedValue({
          active_tasks: [{ task_id: "past-task", status: taskStatus }],
        }),
      } as unknown as typeof window.__octopRuntime;
      await mount();
      state.status.mockResolvedValue(status("new"));
      await check();
      await act(async () => {
        fireEvent.click(screen.getByText("clientConfig.apply"));
      });
      expect(state.reload).toHaveBeenCalledOnce();
      expect(state.info).not.toHaveBeenCalled();
    },
  );

  it.each(["running", "cancel_requested", "unrecognized"])(
    "still protects a %s native task alongside historical receipts",
    async (taskStatus) => {
      state.desktop = true;
      window.__octopRuntime = {
        version: 1,
        refreshStatus: vi.fn().mockResolvedValue({
          active_tasks: [
            { task_id: "finished", status: "completed" },
            { task_id: "current", status: taskStatus },
          ],
        }),
      } as unknown as typeof window.__octopRuntime;
      await mount();
      state.status.mockResolvedValue(status("new"));
      await check();
      await act(async () => {
        fireEvent.click(screen.getByText("clientConfig.apply"));
      });
      expect(state.reload).not.toHaveBeenCalled();
      expect(state.info).toHaveBeenCalledWith("clientConfig.busy");
    },
  );

  it("ignores a late response after changing accounts", async () => {
    let complete!: (value: ClientConfigStatus) => void;
    state.status.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          complete = resolve;
        }),
    );
    await mount();
    state.status.mockResolvedValue(status("other", ["terminal"], 3));
    await act(async () => {
      fireEvent.click(screen.getByText("switch account"));
    });
    await act(async () => {
      complete(status("stale", ["users"]));
    });
    expect(screen.getByTestId("rights").textContent).toBe("terminal");
  });

  it("does not refresh a later page when the user leaves during an update check", async () => {
    const view = render(<Harness />);
    await act(async () => {});
    state.status.mockResolvedValue(status("new"));
    await check();
    let complete!: (value: ClientConfigStatus) => void;
    state.status.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          complete = resolve;
        }),
    );
    await act(async () => {
      fireEvent.click(screen.getByText("clientConfig.apply"));
    });
    view.unmount();
    await act(async () => {
      complete(status("new"));
    });
    expect(state.reload).not.toHaveBeenCalled();
  });
});
