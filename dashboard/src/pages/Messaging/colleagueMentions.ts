export interface ColleagueMention {
  userId: string;
  start: number;
  end: number;
}

export interface MentionDraft {
  text: string;
  mentions: ColleagueMention[];
}

// Preserve identities only while the selected text remains intact. In particular,
// editing one of two colleagues with the same name must not mention the other.
export function updateMentionDraft(
  draft: MentionDraft,
  text: string,
  cursor?: number,
): MentionDraft {
  let start = 0;
  // The caret disambiguates deleting/inserting one of several identical names.
  const prefixEnd =
    cursor === undefined
      ? text.length
      : Math.max(0, cursor - Math.max(0, text.length - draft.text.length));
  while (
    start < draft.text.length &&
    start < prefixEnd &&
    draft.text[start] === text[start]
  )
    start++;
  let oldEnd = draft.text.length;
  let newEnd = text.length;
  while (
    oldEnd > start &&
    newEnd > start &&
    draft.text[oldEnd - 1] === text[newEnd - 1]
  ) {
    oldEnd--;
    newEnd--;
  }
  const offset = newEnd - oldEnd;
  const mentions = draft.mentions.flatMap((mention) => {
    let next = mention;
    if (mention.start >= oldEnd) {
      next = {
        ...mention,
        start: mention.start + offset,
        end: mention.end + offset,
      };
    } else if (mention.end > start) {
      return [];
    }
    if (text[next.end] && !/\s/.test(text[next.end])) return [];
    return [next];
  });
  return { text, mentions };
}

export function mentionAtCursor(draft: MentionDraft, cursor: number) {
  const match = /@([^@\s]*)$/.exec(draft.text.slice(0, cursor));
  if (!match) return null;
  const start = cursor - match[0].length;
  if (start > 0 && /[\w.+-]/.test(draft.text[start - 1])) return null;
  if (
    draft.mentions.some(
      (mention) => start >= mention.start && start < mention.end,
    )
  )
    return null;
  return { start, end: cursor, query: match[1] };
}

export function mentionedUserIds(draft: MentionDraft) {
  return [...new Set(draft.mentions.map((mention) => mention.userId))];
}
