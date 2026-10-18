import { describe, expect, it } from "vitest";
import {
  closeBrowserTabOptimistic,
  markBrowserTabActive,
  mergeBrowserTabsStable,
} from "../browserTabs";

const previous = [
  { id: "a", title: "B站", url: "https://www.bilibili.com/", active: false },
  {
    id: "b",
    title: "小红书",
    url: "https://www.xiaohongshu.com/explore",
    active: true,
  },
  { id: "c", title: "微博", url: "https://weibo.com/", active: false },
];

describe("mergeBrowserTabsStable", () => {
  it("keeps existing tab positions when an active-first update arrives", () => {
    const incoming = [
      {
        id: "c",
        title: "微博 - active",
        url: "https://weibo.com/",
        active: true,
      },
      {
        id: "a",
        title: "B站",
        url: "https://www.bilibili.com/",
        active: false,
      },
      {
        id: "b",
        title: "小红书",
        url: "https://www.xiaohongshu.com/explore",
        active: false,
      },
      {
        id: "d",
        title: "中国天气网",
        url: "https://www.weather.com.cn/",
        active: false,
      },
    ];

    const merged = mergeBrowserTabsStable(previous, incoming);

    expect(merged.map((tab) => tab.id)).toEqual(["a", "b", "c", "d"]);
    expect(merged.find((tab) => tab.id === "c")?.title).toBe("微博 - active");
  });

  it("removes closed tabs while preserving the surviving order", () => {
    const incoming = [
      { id: "c", title: "微博", url: "https://weibo.com/", active: true },
      {
        id: "a",
        title: "B站",
        url: "https://www.bilibili.com/",
        active: false,
      },
    ];

    expect(
      mergeBrowserTabsStable(previous, incoming).map((tab) => tab.id),
    ).toEqual(["a", "c"]);
  });

  it("returns the existing array when the stable tab content is unchanged", () => {
    const incoming = [previous[2], previous[0], previous[1]];
    const merged = mergeBrowserTabsStable(previous, incoming);

    expect(merged).toBe(previous);
  });

  it("clears the list when the backend reports no tabs", () => {
    expect(mergeBrowserTabsStable(previous, [])).toEqual([]);
  });

  it("marks a tab active optimistically without reordering tabs", () => {
    const merged = markBrowserTabActive(previous, "c");

    expect(merged.map((tab) => tab.id)).toEqual(["a", "b", "c"]);
    expect(merged.map((tab) => tab.active)).toEqual([false, false, true]);
  });

  it("closes tabs optimistically and activates a neighbor when needed", () => {
    expect(
      closeBrowserTabOptimistic(previous, "b").map((tab) => [
        tab.id,
        tab.active,
      ]),
    ).toEqual([
      ["a", false],
      ["c", true],
    ]);
    expect(closeBrowserTabOptimistic([previous[0]], "a")).toEqual([]);
  });
});
