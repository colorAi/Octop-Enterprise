import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { expect, it } from "vitest";
import {
  ConversationNavigationProvider,
  useConversationNavigation,
} from "./ConversationNavigation";
function Workspace() {
  const location = useLocation();
  const navigate = useNavigate();
  const conversationPath = useConversationNavigation();
  return (
    <>
      <button onClick={() => navigate(conversationPath("/contacts"))}>
        Company
      </button>
      <button onClick={() => navigate(conversationPath("/chat"))}>Agent</button>
      <output>{location.pathname + location.search}</output>
    </>
  );
}
it("returns to the previous Agent conversation when switching nav entries", () => {
  render(
    <MemoryRouter initialEntries={["/chat/expert/original-thread"]}>
      <ConversationNavigationProvider>
        <Workspace />
      </ConversationNavigationProvider>
    </MemoryRouter>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Company" }));
  expect(screen.getByText("/contacts")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Agent" }));
  expect(screen.getByText("/chat/expert/original-thread")).toBeInTheDocument();
});

it("restores the selected department when returning to the company", () => {
  render(
    <MemoryRouter initialEntries={["/contacts?department=engineering"]}>
      <ConversationNavigationProvider>
        <Workspace />
      </ConversationNavigationProvider>
    </MemoryRouter>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Agent" }));
  expect(screen.getByText("/chat")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Company" }));
  expect(
    screen.getByText("/contacts?department=engineering"),
  ).toBeInTheDocument();
});
