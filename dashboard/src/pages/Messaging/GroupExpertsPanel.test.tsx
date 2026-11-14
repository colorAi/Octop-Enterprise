import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  communicationApi,
  CommunicationError,
} from "../../api/modules/communication";
import type { GroupExperts } from "../../api/types/communication";
import GroupExpertsPanel from "./GroupExpertsPanel";

const { t } = vi.hoisted(() => ({ t: (key: string) => key }));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t }) }));
const source = {
  source_agent_id: "source",
  source_row_id: 12,
  display_name: "New Expert",
  avatar: {},
  can_call: true,
};
const initial: GroupExperts = {
  items: [],
  candidates: [source],
  scope_sources: [],
  scope_revision: 2,
  can_manage: true,
  can_configure: true,
  experts_enabled: true,
  ready: true,
  syncing: false,
};
beforeEach(() => vi.restoreAllMocks());
afterEach(() => vi.useRealTimers());
it("explains department resources before inviting and distinguishes group scope", async () => {
  const listing = vi.spyOn(communicationApi, "groupExperts").mockResolvedValue({
    ...initial,
    candidates: [],
    group_kind: "department",
  });
  const view = render(<GroupExpertsPanel group="department" organization />);
  expect(
    await screen.findByText("groupExperts.noDepartmentCandidates"),
  ).toBeInTheDocument();
  view.unmount();
  listing.mockResolvedValue({
    ...initial,
    candidates: [],
    can_configure: false,
  });
  render(<GroupExpertsPanel group="project" organization={false} />);
  expect(
    await screen.findByText("groupExperts.noScopeCandidates"),
  ).toBeInTheDocument();
});
async function chooseExpert() {
  fireEvent.mouseDown(
    await screen.findByRole("combobox", { name: "groupExperts.choose" }),
  );
  fireEvent.click(await screen.findByText("New Expert"));
}
it("retries a lost invitation response with the original request and displays the committed binding", async () => {
  const committed = {
    ...initial,
    items: [
      {
        ...source,
        binding_id: "binding",
        generation: 1,
        joined_seq: 5,
        state: "active" as const,
      },
    ],
  };
  const list = vi
    .spyOn(communicationApi, "groupExperts")
    .mockResolvedValue(initial);
  const invite = vi
    .spyOn(communicationApi, "inviteExpert")
    .mockRejectedValueOnce(new Error("connection lost"))
    .mockImplementationOnce(async () => {
      list.mockResolvedValue(committed);
      return {};
    });
  render(<GroupExpertsPanel group="group-one" organization />);
  await chooseExpert();
  fireEvent.click(screen.getByRole("button", { name: "groupExperts.invite" }));
  await waitFor(() => expect(invite).toHaveBeenCalledTimes(1));
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "groupExperts.invite" }),
    ).toBeEnabled(),
  );
  fireEvent.click(screen.getByRole("button", { name: "groupExperts.invite" }));
  await waitFor(() => expect(invite).toHaveBeenCalledTimes(2));
  expect(invite.mock.calls[1]).toEqual(invite.mock.calls[0]);
  expect(invite.mock.calls[0][1]).toMatchObject({
    source_agent_id: "source",
    source_row_id: 12,
    revision: 2,
    request_id: expect.any(String),
  });
  expect(await screen.findByText("1/3")).toBeInTheDocument();
});
it("refreshes a rejected scope revision and creates a new management request", async () => {
  const list = vi
    .spyOn(communicationApi, "groupExperts")
    .mockResolvedValue(initial);
  const save = vi
    .spyOn(communicationApi, "expertScope")
    .mockImplementationOnce(async () => {
      list.mockResolvedValue({ ...initial, scope_revision: 4 });
      throw new CommunicationError(
        "group_expert_scope_changed",
        409,
        false,
        "scope changed",
      );
    })
    .mockResolvedValue({});
  render(<GroupExpertsPanel group="group-one" organization />);
  fireEvent.click(
    await screen.findByRole("button", { name: "communication.save" }),
  );
  await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "communication.save" }),
    ).toBeEnabled(),
  );
  fireEvent.click(screen.getByRole("button", { name: "communication.save" }));
  await waitFor(() => expect(save).toHaveBeenCalledTimes(2));
  expect(save.mock.calls[1][1].revision).toBe(4);
  expect(save.mock.calls[1][1].request_id).not.toBe(
    save.mock.calls[0][1].request_id,
  );
});
it("enforces three displayed slots and hides management from ordinary members", async () => {
  const items = Array.from({ length: 3 }, (_, i) => ({
    ...source,
    source_agent_id: `active-${i}`,
    binding_id: `binding-${i}`,
    display_name: `Expert ${i}`,
    generation: 1,
    joined_seq: 1,
    state: "active" as const,
  }));
  const list = vi
    .spyOn(communicationApi, "groupExperts")
    .mockResolvedValue({ ...initial, items });
  const view = render(<GroupExpertsPanel group="group-one" organization />);
  await chooseExpert();
  expect(screen.getByText("3/3")).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "groupExperts.invite" }),
  ).toBeDisabled();
  view.unmount();
  list.mockResolvedValue({
    ...initial,
    items,
    can_manage: false,
    can_configure: false,
  });
  render(<GroupExpertsPanel group="group-one" organization={false} />);
  await screen.findByText("3/3");
  expect(
    screen.queryByRole("button", { name: "groupExperts.invite" }),
  ).toBeNull();
  expect(
    screen.queryByRole("button", { name: "communication.remove" }),
  ).toBeNull();
  expect(screen.queryByRole("switch")).toBeNull();
});

it("keeps an edited scope while synchronization refreshes the listing", async () => {
  vi.useFakeTimers();
  const list = vi.spyOn(communicationApi, "groupExperts").mockResolvedValue({
    ...initial,
    syncing: true,
  });
  const save = vi.spyOn(communicationApi, "expertScope").mockResolvedValue({});
  await act(async () => {
    render(<GroupExpertsPanel group="group-one" organization />);
  });
  fireEvent.mouseDown(
    screen.getByRole("combobox", { name: "groupExperts.scope" }),
  );
  fireEvent.click(screen.getByText("New Expert"));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2000);
  });
  expect(list).toHaveBeenCalledTimes(2);
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "communication.save" }));
  });
  expect(save).toHaveBeenCalledWith(
    "group-one",
    expect.objectContaining({
      sources: [{ source_agent_id: "source", source_row_id: 12 }],
    }),
  );
});

it("ignores an old polling response after the saved scope has loaded", async () => {
  vi.useFakeTimers();
  let resolveOld!: (value: GroupExperts) => void;
  const old = new Promise<GroupExperts>((resolve) => {
    resolveOld = resolve;
  });
  const committed = {
    ...initial,
    scope_revision: 3,
    scope_sources: [{ source_agent_id: "source", source_row_id: 12 }],
  };
  const list = vi
    .spyOn(communicationApi, "groupExperts")
    .mockResolvedValue(committed)
    .mockResolvedValueOnce({ ...initial, syncing: true })
    .mockReturnValueOnce(old);
  const save = vi.spyOn(communicationApi, "expertScope").mockResolvedValue({});
  await act(async () => {
    render(<GroupExpertsPanel group="group-one" organization />);
  });
  fireEvent.mouseDown(
    screen.getByRole("combobox", { name: "groupExperts.scope" }),
  );
  fireEvent.click(screen.getByText("New Expert"));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2000);
  });
  expect(list).toHaveBeenCalledTimes(2);
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "communication.save" }));
  });
  expect(list).toHaveBeenCalledTimes(3);
  await act(async () => resolveOld(initial));
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "communication.save" }));
  });
  expect(save.mock.calls[1]).toEqual([
    "group-one",
    expect.objectContaining({
      revision: 3,
      sources: [{ source_agent_id: "source", source_row_id: 12 }],
    }),
  ]);
});

it("keeps changes made while an earlier scope save is pending", async () => {
  const second = {
    ...source,
    source_agent_id: "second",
    source_row_id: 13,
    display_name: "Another Expert",
  };
  const list = vi.spyOn(communicationApi, "groupExperts").mockResolvedValue({
    ...initial,
    candidates: [source, second],
  });
  let finishSave!: (value: unknown) => void;
  const pending = new Promise((resolve) => {
    finishSave = resolve;
  });
  const save = vi
    .spyOn(communicationApi, "expertScope")
    .mockResolvedValue({})
    .mockReturnValueOnce(pending);
  await act(async () => {
    render(<GroupExpertsPanel group="group-one" organization />);
  });
  fireEvent.mouseDown(
    screen.getByRole("combobox", { name: "groupExperts.scope" }),
  );
  fireEvent.click(screen.getByText("New Expert"));
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "communication.save" }));
  });
  fireEvent.click(screen.getByText("Another Expert"));
  list.mockResolvedValue({
    ...initial,
    candidates: [source, second],
    scope_revision: 3,
    scope_sources: [{ source_agent_id: "source", source_row_id: 12 }],
  });
  await act(async () => finishSave({}));
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "communication.save" }));
  });
  expect(save.mock.calls[1]).toEqual([
    "group-one",
    expect.objectContaining({
      revision: 3,
      sources: [
        { source_agent_id: "source", source_row_id: 12 },
        { source_agent_id: "second", source_row_id: 13 },
      ],
    }),
  ]);
});
