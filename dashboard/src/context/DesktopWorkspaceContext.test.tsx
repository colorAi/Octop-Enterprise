import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  DesktopWorkspaceProvider,
  useDesktopWorkspace,
} from "./DesktopWorkspaceContext";
import { devicesApi } from "../api/modules/devices";
import type { DesktopWorkspaceState } from "../api/desktopWorkspace";
import { StrictMode } from "react";

vi.mock("../api/modules/devices", () => ({
  devicesApi: { list: vi.fn(), pair: vi.fn() },
}));
vi.mock("../hooks/useCurrentUser", () => ({
  useCurrentUser: () => ({ id: 7 }),
}));
const initial: DesktopWorkspaceState = {
  state: "unpaired",
  device_id: "",
  owner_user_id: 0,
  path: "",
  grant: null,
};
const connected: DesktopWorkspaceState = {
  ...initial,
  state: "online",
  owner_user_id: 7,
  device_id: "this-computer",
  path: "/work",
  grant: {
    id: "work",
    name: "Work",
    version: 1,
    writable: true,
    allow_company: true,
  },
};
function Probe() {
  const desktop = useDesktopWorkspace();
  return (
    <>
      <div>Chat available</div>
      <output>{desktop.state?.path || desktop.error || "waiting"}</output>
      <button onClick={() => void desktop.choose()}>Choose folder</button>
      <button onClick={desktop.retry}>Retry</button>
    </>
  );
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(devicesApi.list).mockResolvedValue({ enabled: true, devices: [] });
  vi.mocked(devicesApi.pair).mockResolvedValue({
    code: "octop-pair.once",
    expires_at: 9999999999,
    credential_expires_at: 9999999999,
  });
  window.octopDesktopWorkspace = {
    status: vi.fn().mockResolvedValue(initial),
    pair: vi.fn().mockResolvedValue(connected),
    choose: vi.fn().mockResolvedValue({ ...connected, path: "/new-work" }),
    clear: vi.fn(),
    disconnect: vi.fn().mockResolvedValue(initial),
  };
});
describe("employee desktop connection", () => {
  it("pairs on sign-in and updates the folder inside settings", async () => {
    render(
      <DesktopWorkspaceProvider>
        <Probe />
      </DesktopWorkspaceProvider>,
    );
    expect(await screen.findByText("/work")).toBeInTheDocument();
    expect(window.octopDesktopWorkspace!.pair).toHaveBeenCalledWith(
      "octop-pair.once",
    );
    await act(async () => screen.getByText("Choose folder").click());
    expect(screen.getByText("/new-work")).toBeInTheDocument();
  });
  it("connects when the native bridge arrives after React", async () => {
    const bridge = window.octopDesktopWorkspace;
    delete window.octopDesktopWorkspace;
    render(
      <DesktopWorkspaceProvider>
        <Probe />
      </DesktopWorkspaceProvider>,
    );
    expect(devicesApi.pair).not.toHaveBeenCalled();
    await act(async () => {
      window.octopDesktopWorkspace = bridge;
      window.dispatchEvent(new Event("octop:desktop-workspace-ready"));
    });
    expect(await screen.findByText("/work")).toBeInTheDocument();
  });
  it("disconnects a mismatched account before binding this login", async () => {
    vi.mocked(window.octopDesktopWorkspace!.status).mockResolvedValue({
      ...connected,
      owner_user_id: 8,
    });
    render(
      <DesktopWorkspaceProvider>
        <Probe />
      </DesktopWorkspaceProvider>,
    );
    await screen.findByText("/work");
    expect(window.octopDesktopWorkspace!.disconnect).toHaveBeenCalledOnce();
  });
  it("leaves server chat usable when local connection fails", async () => {
    vi.mocked(window.octopDesktopWorkspace!.pair).mockRejectedValue(
      new Error("network"),
    );
    render(
      <DesktopWorkspaceProvider>
        <Probe />
      </DesktopWorkspaceProvider>,
    );
    await screen.findByText("connection_failed");
    expect(screen.getByText("Chat available")).toBeInTheDocument();
  });
  it("does not require pairing when the administrator disabled local tools", async () => {
    vi.mocked(devicesApi.list).mockResolvedValue({
      enabled: false,
      devices: [],
    });
    render(
      <DesktopWorkspaceProvider>
        <Probe />
      </DesktopWorkspaceProvider>,
    );
    await waitFor(() => expect(devicesApi.list).toHaveBeenCalledOnce());
    expect(devicesApi.pair).not.toHaveBeenCalled();
  });
  it("does not consume two pairing codes under StrictMode", async () => {
    render(
      <StrictMode>
        <DesktopWorkspaceProvider>
          <Probe />
        </DesktopWorkspaceProvider>
      </StrictMode>,
    );
    await waitFor(() => expect(screen.getByText("/work")).toBeInTheDocument(), {
      timeout: 4500,
    });
    expect(devicesApi.pair).toHaveBeenCalledOnce();
  });
});
