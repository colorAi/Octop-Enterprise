import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { useBrowserKeyboardInput } from "./useBrowserKeyboardInput";

function InputHarness({
  enabled = true,
  onEvent,
}: {
  enabled?: boolean;
  onEvent: (event: Record<string, unknown>) => void;
}) {
  const { inputRef, focusInput, inputProps } = useBrowserKeyboardInput({
    enabled,
    onEvent,
  });
  return (
    <>
      <canvas tabIndex={0} onFocus={focusInput} data-testid="canvas" />
      <textarea aria-label="remote input" ref={inputRef} {...inputProps} />
      <input aria-label="address" />
    </>
  );
}

function setup(enabled = true) {
  const onEvent = vi.fn();
  const view = render(<InputHarness enabled={enabled} onEvent={onEvent} />);
  const input = screen.getByRole("textbox", { name: "remote input" });
  return { ...view, onEvent, input };
}

describe("browser keyboard input", () => {
  it("focuses an editable IME target when the canvas gains focus", () => {
    const { input } = setup();
    screen.getByTestId("canvas").focus();
    expect(input).toHaveFocus();
  });

  it("forwards ordinary typing once, with physical key down/up", async () => {
    const { input, onEvent } = setup();
    await userEvent.type(input, "ab");
    expect(onEvent.mock.calls.map(([event]) => event.type)).toEqual([
      "keydown",
      "type",
      "keyup",
      "keydown",
      "type",
      "keyup",
    ]);
    expect(onEvent).toHaveBeenCalledWith({ type: "type", text: "a" });
    expect(onEvent).toHaveBeenCalledWith({ type: "type", text: "b" });
    expect(input).toHaveValue("");
  });

  it.each(["Backspace", "Delete", "Tab", "Enter", "Escape", "ArrowLeft"])(
    "keeps %s in the remote page and forwards its release",
    (key) => {
      const { input, onEvent } = setup();
      expect(fireEvent.keyDown(input, { key, code: key })).toBe(false);
      fireEvent.keyUp(input, { key, code: key });
      expect(onEvent.mock.calls.map(([event]) => event.type)).toEqual([
        "keydown",
        "keyup",
      ]);
    },
  );

  it.each([{ ctrlKey: true }, { metaKey: true }])(
    "preserves shortcut modifiers %j",
    (modifiers) => {
      const { input, onEvent } = setup();
      fireEvent.keyDown(input, {
        key: "a",
        code: "KeyA",
        keyCode: 65,
        ...modifiers,
      });
      expect(onEvent).toHaveBeenCalledWith(
        expect.objectContaining({
          type: "keydown",
          key: "a",
          code: "KeyA",
          keyCode: 65,
          ...modifiers,
        }),
      );
    },
  );

  it("does not forward pinyin or the Enter used to select an IME candidate", () => {
    const { input, onEvent } = setup();
    fireEvent.compositionStart(input);
    fireEvent.keyDown(input, { key: "n", code: "KeyN", isComposing: true });
    fireEvent.input(input, { target: { value: "nihao" }, isComposing: true });
    expect(fireEvent.keyDown(input, { key: "Enter", code: "Enter" })).toBe(
      true,
    );
    expect(onEvent).not.toHaveBeenCalled();
    fireEvent.compositionEnd(input, { data: "你好" });
    fireEvent.input(input, {
      target: { value: "你好" },
      data: "你好",
      inputType: "insertFromComposition",
    });
    expect(onEvent.mock.calls).toEqual([[{ type: "type", text: "你好" }]]);
    expect(input).toHaveValue("");
  });

  it("allows repeated Chinese commits and emoji without duplicating text", () => {
    const { input, onEvent } = setup();
    for (const text of ["中文", "中文", "👩‍💻"]) {
      fireEvent.compositionStart(input);
      fireEvent.compositionEnd(input, { data: text });
    }
    expect(onEvent.mock.calls.map(([event]) => event.text)).toEqual([
      "中文",
      "中文",
      "👩‍💻",
    ]);
  });

  it("does not send cancelled compositions or IME setup keys", () => {
    const { input, onEvent } = setup();
    fireEvent.keyDown(input, { key: "Process", keyCode: 229 });
    fireEvent.keyDown(input, { key: "Dead" });
    fireEvent.compositionStart(input);
    fireEvent.input(input, { target: { value: "pinyin" }, isComposing: true });
    fireEvent.compositionEnd(input, { data: "" });
    expect(onEvent).not.toHaveBeenCalled();
    expect(input).toHaveValue("");
  });

  it("pastes local Unicode text without triggering the remote clipboard", () => {
    const { input, onEvent } = setup();
    expect(
      fireEvent.keyDown(input, { key: "v", code: "KeyV", metaKey: true }),
    ).toBe(true);
    fireEvent.paste(input, {
      clipboardData: { getData: () => "中文\nhello 👋" },
    });
    fireEvent.keyUp(input, { key: "v", code: "KeyV", metaKey: true });
    expect(onEvent.mock.calls).toEqual([
      [{ type: "type", text: "中文\nhello 👋" }],
    ]);
  });

  it("leaves local address bar input alone", async () => {
    const { onEvent } = setup();
    await userEvent.type(
      screen.getByRole("textbox", { name: "address" }),
      "abc",
    );
    expect(onEvent).not.toHaveBeenCalled();
  });

  it("releases held keys on blur and when control is revoked", () => {
    const { input, onEvent, rerender } = setup();
    input.focus();
    fireEvent.keyDown(input, {
      key: "Shift",
      code: "ShiftLeft",
      shiftKey: true,
    });
    input.blur();
    expect(onEvent).toHaveBeenLastCalledWith(
      expect.objectContaining({ type: "keyup", key: "Shift", shiftKey: false }),
    );
    fireEvent.keyDown(input, {
      key: "Control",
      code: "ControlLeft",
      ctrlKey: true,
    });
    rerender(<InputHarness enabled={false} onEvent={onEvent} />);
    expect(onEvent).toHaveBeenLastCalledWith(
      expect.objectContaining({
        type: "keyup",
        key: "Control",
        ctrlKey: false,
      }),
    );
    const count = onEvent.mock.calls.length;
    fireEvent.keyDown(input, { key: "Backspace" });
    fireEvent.input(input, { target: { value: "no control" } });
    fireEvent.compositionEnd(input, { data: "不发送" });
    expect(onEvent).toHaveBeenCalledTimes(count);
  });
});
