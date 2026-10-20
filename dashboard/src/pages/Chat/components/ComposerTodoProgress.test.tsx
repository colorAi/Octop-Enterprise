import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ChatMessage } from "../hooks/useChat";
import ComposerTodoProgress from "./ComposerTodoProgress";

function messages(turn: string, done = false): ChatMessage[] {
  return [
    { id: turn, role: "user", content: "Create report", timestamp: 1 },
    {
      id: turn + "-plan",
      role: "assistant",
      content: "",
      status: "done",
      timestamp: 2,
      toolData: {
        name: "write_todos",
        arguments: JSON.stringify({
          todos: [
            { id: "1", content: "Read source files", status: "completed" },
            {
              id: "2",
              content: "Write report",
              status: done ? "completed" : "in_progress",
            },
          ],
        }),
      },
    },
  ];
}

describe("ComposerTodoProgress", () => {
  it("starts collapsed and toggles details with an accessible button", () => {
    render(<ComposerTodoProgress messages={messages("turn")} isStreaming />);
    const toggle = screen.getByRole("button", { name: /Task progress/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveTextContent("1/2 done");
    expect(toggle).toHaveTextContent("Current: Write report");
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(
      within(screen.getByRole("list")).getAllByRole("listitem"),
    ).toHaveLength(2);
    fireEvent.click(toggle);
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });

  it("updates completion without collapsing a plan the user has expanded", () => {
    const { rerender } = render(
      <ComposerTodoProgress messages={messages("turn")} isStreaming />,
    );
    fireEvent.click(screen.getByRole("button"));
    rerender(
      <ComposerTodoProgress
        messages={messages("turn", true)}
        isStreaming={false}
      />,
    );
    expect(screen.getByRole("button")).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button")).toHaveTextContent("2/2 done");
    expect(screen.getByRole("button")).not.toHaveTextContent("Current:");
  });

  it("clears the summary on a new turn and defaults the new plan to collapsed", () => {
    const { rerender } = render(
      <ComposerTodoProgress messages={messages("old")} isStreaming />,
    );
    fireEvent.click(screen.getByRole("button"));
    rerender(
      <ComposerTodoProgress
        messages={[
          ...messages("old"),
          { id: "new", role: "user", content: "New task", timestamp: 3 },
        ]}
        isStreaming
      />,
    );
    expect(screen.queryByRole("region")).not.toBeInTheDocument();
    rerender(<ComposerTodoProgress messages={messages("new")} isStreaming />);
    expect(screen.getByRole("button")).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("does not carry expanded state into a different chat", () => {
    const { rerender } = render(
      <ComposerTodoProgress
        key="chat-a"
        messages={messages("turn")}
        isStreaming
      />,
    );
    fireEvent.click(screen.getByRole("button"));
    rerender(
      <ComposerTodoProgress
        key="chat-b"
        messages={messages("turn")}
        isStreaming={false}
      />,
    );
    expect(screen.getByRole("button")).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });
});
