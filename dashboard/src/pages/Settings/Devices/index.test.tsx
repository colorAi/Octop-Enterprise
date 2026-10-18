import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("react-i18next", async () => {
  const { default: zh } = await import("../../../locales/zh.json");
  const t = (key: string) =>
    zh.devices[key.replace("devices.", "") as keyof typeof zh.devices] ?? key;
  return { useTranslation: () => ({ t }) };
});

vi.mock("../../../layouts/PageShell", () => ({
  default: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
}));
vi.mock("../../../api/modules/devices", () => ({
  devicesApi: { list: vi.fn(), pair: vi.fn(), probe: vi.fn(), revoke: vi.fn() },
}));

import { devicesApi } from "../../../api/modules/devices";
import DevicesPage from "./index";

const api = vi.mocked(devicesApi);
const device = {
  device_id: "pc-1",
  name: "月报电脑",
  platform: "windows" as const,
  protocol_version: 1,
  online: true,
  capabilities: ["device_status"],
  expires_at: Math.floor(Date.now() / 1000) + 1000,
  revoked_at: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  api.list.mockResolvedValue({ enabled: true, devices: [device] });
});

describe("company devices", () => {
  it("does not offer pairing when the server feature is disabled", async () => {
    api.list.mockResolvedValue({ enabled: false, devices: [] });
    render(<DevicesPage />);
    expect(
      await screen.findByText("公司尚未启用桌面设备通道。"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "生成配对码" }),
    ).not.toBeInTheDocument();
  });

  it("creates a bounded pairing code and explains automatic desktop connection", async () => {
    const code = "octop-pair.single-use-secret";
    api.pair.mockResolvedValue({
      code,
      expires_at: Math.floor(Date.now() / 1000) + 120,
      credential_expires_at: device.expires_at,
    });
    render(<DevicesPage />);
    fireEvent.click(await screen.findByRole("button", { name: "生成配对码" }));
    await waitFor(() =>
      expect(screen.getByLabelText("一次性配对码")).toHaveValue(code),
    );
    expect(
      screen.getByText(/员工客户端登录后自动连接本机工具/),
    ).toBeInTheDocument();
    expect(localStorage.getItem("octop-device-credential")).toBeNull();
  });

  it("reports a timeout without claiming cancellation succeeded", async () => {
    api.probe.mockResolvedValue({ call_id: "call-1", status: "timed_out" });
    render(<DevicesPage />);
    fireEvent.click(await screen.findByRole("button", { name: "检测连接" }));
    expect(
      await screen.findByText("设备响应超时，已请求取消检测。"),
    ).toBeInTheDocument();
    expect(api.probe).toHaveBeenCalledWith("pc-1");
  });

  it("revokes only the selected device and refreshes its status", async () => {
    api.revoke.mockImplementation(async () => {
      api.list.mockResolvedValue({
        enabled: true,
        devices: [{ ...device, online: false, revoked_at: Date.now() / 1000 }],
      });
    });
    render(<DevicesPage />);
    fireEvent.click(await screen.findByRole("button", { name: "撤销设备" }));
    expect(await screen.findByText("已撤销")).toBeInTheDocument();
    expect(api.revoke).toHaveBeenCalledWith("pc-1");
    expect(screen.getByRole("button", { name: "检测连接" })).toBeDisabled();
  });
});
