import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import PrivateImage from "./PrivateImage";
import { communicationApi } from "../../api/modules/communication";
afterEach(() => {
  vi.restoreAllMocks();
});
it("downloads authenticated bytes and revokes the local URL on removal", async () => {
  const create = vi.fn(() => "blob:private-image");
  const revoke = vi.fn();
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    value: create,
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    configurable: true,
    value: revoke,
  });
  vi.spyOn(communicationApi, "media").mockResolvedValue(new Blob(["image"]));
  const view = render(<PrivateImage asset="private" />);
  await screen.findByAltText("communication.image");
  expect(communicationApi.media).toHaveBeenCalledWith(
    "private",
    expect.any(AbortSignal),
  );
  view.unmount();
  expect(revoke).toHaveBeenCalledWith("blob:private-image");
});
it("discards a late media response after the message is removed", async () => {
  const create = vi.fn();
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    value: create,
  });
  let finish!: (value: Blob) => void;
  vi.spyOn(communicationApi, "media").mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const view = render(<PrivateImage asset="old" />);
  view.unmount();
  finish(new Blob(["private"]));
  await waitFor(() => expect(create).not.toHaveBeenCalled());
});
