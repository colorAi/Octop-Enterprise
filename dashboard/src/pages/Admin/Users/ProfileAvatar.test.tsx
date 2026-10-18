import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProfileAvatarPicker } from "./ProfileAvatar";
import ExpertAvatarPicker from "../../Experts/components/ExpertAvatarPicker";

vi.mock("../../../hooks/useAuthImageSrc", () => ({
  useAuthImageSrc: (url: string) => ({ src: url, loadState: "ready" }),
}));

vi.mock("../../Chat/components/ExpertAgentAvatar", () => ({
  default: ({ iconUrl }: { iconUrl?: string | null }) =>
    iconUrl ? <img src={iconUrl} alt="avatar" /> : null,
}));

beforeEach(() => {
  vi.stubGlobal("URL", {
    createObjectURL: vi.fn(() => "blob:avatar-preview"),
    revokeObjectURL: vi.fn(),
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe.each([
  ["user and role avatars", ProfileAvatarPicker],
  ["expert avatars", ExpertAvatarPicker],
] as const)("%s file selection", (_name, Picker) => {
  it("keeps the file input mounted while choosing and uploading", async () => {
    let finishUpload!: () => void;
    const onPick = vi.fn(
      () => new Promise<void>((resolve) => (finishUpload = resolve)),
    );
    const { container, unmount } = render(<Picker onPick={onPick} />);
    const click = vi.spyOn(HTMLInputElement.prototype, "click");
    fireEvent.click(
      screen.getByRole("button", { name: "experts.avatarUpload" }),
    );
    const input =
      container.querySelector<HTMLInputElement>('input[type="file"]')!;
    expect(input).not.toBeNull();
    expect(click).toHaveBeenCalledOnce();
    expect(click.mock.instances[0]).toBe(input);
    expect(input.isConnected).toBe(true);

    const file = new File(["png"], "avatar.png", { type: "image/png" });
    fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() => expect(onPick).toHaveBeenCalledWith(file));
    expect(input.isConnected).toBe(true);
    expect(
      screen.getByRole("button", { name: "experts.avatarChange" }),
    ).toBeInTheDocument();
    finishUpload();
    unmount();
    expect(input.isConnected).toBe(false);
  });

  it("allows choosing again after cancelling the dialog", async () => {
    const onPick = vi.fn();
    const { container } = render(<Picker onPick={onPick} />);
    const input =
      container.querySelector<HTMLInputElement>('input[type="file"]')!;
    fireEvent.click(
      screen.getByRole("button", { name: "experts.avatarUpload" }),
    );
    fireEvent(input, new Event("cancel"));
    expect(onPick).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole("button", { name: "experts.avatarUpload" }),
    );
    const file = new File(["jpeg"], "avatar.jpg", { type: "image/jpeg" });
    fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() => expect(onPick).toHaveBeenCalledWith(file));
  });

  it("clears the failed preview and permits retrying the same file", async () => {
    const onPick = vi
      .fn()
      .mockRejectedValueOnce(new Error("upload failed"))
      .mockResolvedValue(undefined);
    const { container } = render(<Picker onPick={onPick} />);
    const input =
      container.querySelector<HTMLInputElement>('input[type="file"]')!;
    const file = new File(["png"], "avatar.png", { type: "image/png" });
    fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "experts.avatarUpload" }),
      ).toBeInTheDocument(),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "experts.avatarUpload" }),
    );
    expect(input.value).toBe("");
    fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() => expect(onPick).toHaveBeenCalledTimes(2));
    expect(
      screen.getByRole("button", { name: "experts.avatarChange" }),
    ).toBeInTheDocument();
  });
});
