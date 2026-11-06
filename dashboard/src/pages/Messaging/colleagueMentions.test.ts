import { describe, expect, it } from "vitest";
import {
  mentionAtCursor,
  mentionedUserIds,
  updateMentionDraft,
  type MentionDraft,
} from "./colleagueMentions";

const draft: MentionDraft = {
  text: "@李明 @李明 请确认",
  mentions: [
    { userId: "engineering-li", start: 0, end: 3 },
    { userId: "design-li", start: 4, end: 7 },
  ],
};

describe("selected colleague identities", () => {
  it("keeps same-name colleagues distinct when one mention is removed", () => {
    expect(mentionedUserIds(updateMentionDraft(draft, "@李明 请确认"))).toEqual(
      ["engineering-li"],
    );
    expect(
      mentionedUserIds(updateMentionDraft(draft, " @李明 请确认")),
    ).toEqual(["design-li"]);
    expect(
      mentionedUserIds(updateMentionDraft(draft, "@李明 请确认", 0)),
    ).toEqual(["design-li"]);
    expect(
      mentionedUserIds(updateMentionDraft(draft, "@李明 请确认", 4)),
    ).toEqual(["engineering-li"]);
  });
  it("shifts mentions when text is inserted before them", () => {
    const next = updateMentionDraft(draft, `大家好 ${draft.text}`);
    expect(next.mentions).toEqual(
      draft.mentions.map((mention) => ({
        ...mention,
        start: mention.start + 4,
        end: mention.end + 4,
      })),
    );
  });
  it("removes edited names, including text appended inside a mention", () => {
    expect(
      mentionedUserIds(updateMentionDraft(draft, "@李华 @李明 请确认")),
    ).toEqual(["design-li"]);
    expect(
      mentionedUserIds(updateMentionDraft(draft, "@李明明 @李明 请确认")),
    ).toEqual(["design-li"]);
    expect(mentionedUserIds(updateMentionDraft(draft, ""))).toEqual([]);
  });
  it("deduplicates repeated selections of the same colleague", () => {
    expect(
      mentionedUserIds({
        ...draft,
        mentions: draft.mentions.map((mention) => ({
          ...mention,
          userId: "same",
        })),
      }),
    ).toEqual(["same"]);
  });
  it("opens at the caret after Chinese text, but not for email or an existing mention", () => {
    expect(
      mentionAtCursor({ text: "请@李 后面保留", mentions: [] }, 3),
    ).toEqual({ start: 1, end: 3, query: "李" });
    expect(
      mentionAtCursor({ text: "user@example.com", mentions: [] }, 16),
    ).toBeNull();
    expect(mentionAtCursor(draft, 3)).toBeNull();
  });
});
