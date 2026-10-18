/**
 * EpisodesList.test.tsx — paginated episode list + detail drawer.
 *
 * What we cover:
 *   - mount → listEpisodes with default pagination
 *   - row renders summary + emotion tag + topics
 *   - clicking a row opens drawer showing verbatim_quote and people
 *   - empty state
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { makeEpisode, listEpisodesResp } from "../../../test/memoryFixtures";

vi.mock("../../../api/modules/memoryDashboard", () => ({
  memoryDashboardApi: {
    listEpisodes: vi.fn(),
  },
}));

import { memoryDashboardApi } from "../../../api/modules/memoryDashboard";
import EpisodesList from "./EpisodesList";

const api = vi.mocked(memoryDashboardApi, true);

beforeEach(() => {
  vi.clearAllMocks();
});

describe("<EpisodesList />", () => {
  it("loads episodes and renders summary + topic tags", async () => {
    api.listEpisodes.mockResolvedValue(
      listEpisodesResp([
        makeEpisode({
          id: "ep-1",
          summary: "周末看了一场感人的电影。",
          topics: ["movie", "weekend", "emotion"],
        }),
      ]),
    );

    render(<EpisodesList agentId="ZYWZTD" />);

    await waitFor(() => {
      expect(screen.getByText("周末看了一场感人的电影。")).toBeInTheDocument();
    });

    expect(api.listEpisodes).toHaveBeenCalledWith("ZYWZTD", {
      offset: 0,
      limit: 20,
    });
    // first 3 topic tags rendered (component slices to 3)
    expect(screen.getByText("movie")).toBeInTheDocument();
    expect(screen.getByText("weekend")).toBeInTheDocument();
  });

  it("opens detail drawer with verbatim_quote + people on row click", async () => {
    api.listEpisodes.mockResolvedValue(
      listEpisodesResp([
        makeEpisode({
          id: "ep-1",
          summary: "和小李一起做了个项目。",
          verbatim_quote: "上周我和小李结对编程，最后凌晨三点完成。",
          people: ["小李"],
          topics: ["pair-programming"],
        }),
      ]),
    );

    const user = userEvent.setup();
    render(<EpisodesList agentId="ZYWZTD" />);

    await waitFor(() => {
      expect(screen.getByText("和小李一起做了个项目。")).toBeInTheDocument();
    });
    await user.click(screen.getByText("和小李一起做了个项目。"));

    await waitFor(() => {
      expect(screen.getByText(/凌晨三点完成/)).toBeInTheDocument();
    });
    expect(screen.getAllByText("小李").length).toBeGreaterThanOrEqual(1);
  });

  it("renders empty state on no items", async () => {
    api.listEpisodes.mockResolvedValue(listEpisodesResp([]));
    render(<EpisodesList agentId="ZYWZTD" />);
    await waitFor(() => expect(api.listEpisodes).toHaveBeenCalled());
    expect(document.querySelector(".ant-empty-image")).not.toBeNull();
  });
});
