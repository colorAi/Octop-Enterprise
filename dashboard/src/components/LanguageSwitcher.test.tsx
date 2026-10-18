import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { preferencesApi } from "../api/modules/preferences";
import { applyUserLocale } from "../utils/locale";
import LanguageSwitcher from "./LanguageSwitcher";

vi.mock("../utils/locale", () => ({
  applyUserLocale: vi.fn().mockResolvedValue(undefined),
}));
it("allows language selection before login without calling authenticated preferences", async () => {
  const save = vi.spyOn(preferencesApi, "setLocale");
  render(<LanguageSwitcher guest />);
  fireEvent.click(screen.getByRole("button", { name: "简体中文" }));
  await waitFor(() => expect(applyUserLocale).toHaveBeenCalledWith("zh"));
  expect(save).not.toHaveBeenCalled();
});
