import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { App } from "antd";
import { beforeEach, expect, it, vi } from "vitest";
import { type MediaGenerationSettings } from "../../../api/modules/mediaGeneration";
import MediaGenerationSettingsPanel from ".";

const api = vi.hoisted(() => ({
  get: vi.fn(),
  getPresets: vi.fn(),
  save: vi.fn(),
  test: vi.fn(),
}));
vi.mock("../../../api/modules/mediaGeneration", () => ({
  mediaGenerationApi: api,
}));
vi.mock("react-i18next", () => {
  const t = (key: string) => key;
  return { useTranslation: () => ({ t }) };
});

beforeEach(() => {
  vi.clearAllMocks();
});

it.each(["image", "video"] as const)(
  "submits a provider with only %s enabled",
  async (kind) => {
    const settings: MediaGenerationSettings = {
      enabled: true,
      configured: true,
      default_image_provider: kind === "image" ? "sample" : null,
      default_video_provider: kind === "video" ? "sample" : null,
      providers: [
        {
          id: "sample",
          provider: "volcengine",
          display_name: "Test provider",
          enabled: true,
          base_url: "https://media.example",
          image_enabled: kind === "image",
          video_enabled: kind === "video",
          image_model: "sample-image",
          video_model: "sample-video",
          api_key_set: true,
          configured: true,
        },
      ],
    };
    api.get.mockResolvedValue(settings);
    api.getPresets.mockResolvedValue([]);
    api.save.mockResolvedValue(settings);
    api.test.mockResolvedValue({ ok: true });

    render(
      <App>
        <MediaGenerationSettingsPanel />
      </App>,
    );
    fireEvent.click(await screen.findByText("Test provider"));
    const dialog = within(await screen.findByRole("dialog"));
    fireEvent.click(
      dialog.getByRole("button", { name: "mediaGeneration.testCredentials" }),
    );
    await waitFor(() =>
      expect(api.test).toHaveBeenCalledWith(
        expect.objectContaining({ kind: "credentials" }),
      ),
    );
    fireEvent.click(
      dialog.getByRole("button", {
        name:
          kind === "image"
            ? "mediaGeneration.testImageModel"
            : "mediaGeneration.testVideoModel",
      }),
    );
    await waitFor(() =>
      expect(api.test).toHaveBeenCalledWith(expect.objectContaining({ kind })),
    );
    fireEvent.click(dialog.getByRole("button", { name: "common.save" }));
    await waitFor(() =>
      expect(api.save).toHaveBeenCalledWith(
        expect.objectContaining({
          providers: [
            expect.objectContaining({
              image_model: "sample-image",
              video_model: "sample-video",
              image_enabled: kind === "image",
              video_enabled: kind === "video",
            }),
          ],
        }),
      ),
    );
  },
);
