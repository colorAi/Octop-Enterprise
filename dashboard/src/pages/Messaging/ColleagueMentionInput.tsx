import {
  forwardRef,
  useEffect,
  useId,
  useImperativeHandle,
  useRef,
  useState,
} from "react";
import { Button, Input, Spin } from "antd";
import type { TextAreaRef } from "antd/es/input/TextArea";
import { useTranslation } from "react-i18next";
import { communicationApi } from "../../api/modules/communication";
import type { GroupMember } from "../../api/types/communication";
import { communicationErrorText } from "./communicationUi";
import {
  mentionAtCursor,
  updateMentionDraft,
  type MentionDraft,
} from "./colleagueMentions";
import { PersonAvatar, PersonJobTitle } from "./PersonIdentity";
import chatStyles from "../Chat/index.module.less";
import styles from "./index.module.less";

export interface ColleagueMentionInputRef {
  mention: () => void;
  mentionMember: (member: GroupMember) => void;
}

interface Props {
  draft: MentionDraft;
  onChange: (draft: MentionDraft) => void;
  onSend: () => void;
  groupId?: string;
  userId?: string;
  disabled: boolean;
  maxLength: number;
}

const ColleagueMentionInput = forwardRef<ColleagueMentionInputRef, Props>(
  function ColleagueMentionInput(
    { draft, onChange, onSend, groupId, userId, disabled, maxLength },
    ref,
  ) {
    const { t } = useTranslation();
    const input = useRef<TextAreaRef>(null);
    const menu = useRef<HTMLDivElement>(null);
    const menuId = useId();
    const [query, setQuery] =
      useState<ReturnType<typeof mentionAtCursor>>(null);
    const [members, setMembers] = useState<GroupMember[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<unknown>();
    const [revision, setRevision] = useState(0);
    const [active, setActive] = useState(0);
    const open = !!groupId && !disabled && !!query;

    useEffect(() => {
      if (!open || !groupId) return;
      const controller = new AbortController();
      setMembers([]);
      setLoading(true);
      setError(undefined);
      void communicationApi.members(groupId, controller.signal).then(
        (result) => {
          if (!controller.signal.aborted) {
            setMembers(result.items);
            setLoading(false);
          }
        },
        (reason: unknown) => {
          if (!controller.signal.aborted) {
            setError(reason);
            setLoading(false);
          }
        },
      );
      return () => controller.abort();
    }, [open, groupId, revision]);

    const search = query?.query.toLocaleLowerCase() ?? "";
    const candidates = members.filter(
      (member) =>
        member.user_id !== userId &&
        !member.archived &&
        member.status !== "disabled" &&
        member.status !== "deleted" &&
        [member.nickname, member.my_nickname, member.job_title].some(
          (value) => value?.toLocaleLowerCase().includes(search),
        ),
    );
    const activeIndex = Math.min(active, Math.max(0, candidates.length - 1));
    useEffect(() => {
      if (open)
        menu.current
          ?.querySelector('[aria-selected="true"]')
          ?.scrollIntoView?.({ block: "nearest" });
    }, [open, activeIndex]);

    const focusAt = (cursor: number) => {
      requestAnimationFrame(() => {
        const element = input.current?.resizableTextArea?.textArea;
        element?.focus();
        element?.setSelectionRange(cursor, cursor);
      });
    };
    const updateQuery = (next: MentionDraft, cursor: number) => {
      const nextQuery =
        groupId && !disabled ? mentionAtCursor(next, cursor) : null;
      if (
        nextQuery?.start !== query?.start ||
        nextQuery?.end !== query?.end ||
        nextQuery?.query !== query?.query
      )
        setActive(0);
      setQuery(nextQuery);
    };
    useImperativeHandle(ref, () => ({
      mentionMember(member) {
        if (!groupId || disabled || member.user_id === userId) return;
        const element = input.current?.resizableTextArea?.textArea;
        const start = element?.selectionStart ?? draft.text.length;
        const end = element?.selectionEnd ?? start;
        const existing = start === end ? mentionAtCursor(draft, start) : null;
        insertMember(
          member,
          existing ?? { start, end },
          !existing && start > 0 && !/\s/.test(draft.text[start - 1])
            ? " "
            : "",
        );
      },
      mention() {
        if (!groupId || disabled) return;
        const element = input.current?.resizableTextArea?.textArea;
        const start = element?.selectionStart ?? draft.text.length;
        const end = element?.selectionEnd ?? start;
        const existing = mentionAtCursor(draft, start);
        if (existing && start === end) {
          setQuery(existing);
          setActive(0);
          focusAt(start);
          return;
        }
        const prefix = start && !/\s/.test(draft.text[start - 1]) ? " @" : "@";
        const text =
          draft.text.slice(0, start) + prefix + draft.text.slice(end);
        if (text.length > maxLength) return;
        const next = updateMentionDraft(draft, text, start + prefix.length);
        onChange(next);
        updateQuery(next, start + prefix.length);
        focusAt(start + prefix.length);
      },
    }));

    const insertMember = (
      member: GroupMember,
      range: { start: number; end: number },
      prefix = "",
    ) => {
      const label = `@${member.my_nickname || member.nickname}`;
      const start = range.start + prefix.length;
      const text =
        draft.text.slice(0, range.start) +
        prefix +
        label +
        " " +
        draft.text.slice(range.end);
      if (text.length > maxLength) return;
      const next = updateMentionDraft(draft, text, start + label.length + 1);
      next.mentions.push({
        userId: member.user_id,
        start,
        end: start + label.length,
      });
      onChange(next);
      setQuery(null);
      focusAt(start + label.length + 1);
    };
    const choose = (member: GroupMember) => {
      if (query) insertMember(member, query);
    };

    return (
      <div className={styles.mentionComposer}>
        {open && (
          <div
            className={styles.mentionPicker}
            onMouseDown={(event) => event.preventDefault()}
          >
            <div className={styles.mentionHeading}>
              {t("communication.mentionColleague")}
            </div>
            {loading ? (
              <div className={styles.mentionHint}>
                <Spin size="small" /> {t("communication.loadingMembers")}
              </div>
            ) : error ? (
              <div className={styles.mentionHint} role="alert">
                {communicationErrorText(error, t)}
                <Button
                  size="small"
                  onClick={() => setRevision((value) => value + 1)}
                >
                  {t("communication.retry")}
                </Button>
              </div>
            ) : (
              <div
                ref={menu}
                id={menuId}
                role="listbox"
                aria-label={t("communication.mentionColleague")}
                className={styles.mentionOptions}
              >
                {candidates.length ? (
                  candidates.map((member, index) => (
                    <button
                      key={member.user_id}
                      id={`${menuId}-${index}`}
                      type="button"
                      role="option"
                      aria-selected={index === activeIndex}
                      className={styles.mentionOption}
                      onMouseEnter={() => setActive(index)}
                      onClick={() => choose(member)}
                      tabIndex={-1}
                    >
                      <PersonAvatar person={member} size={28} />
                      <span>
                        {member.my_nickname || member.nickname}
                        {member.my_nickname &&
                          member.my_nickname !== member.nickname && (
                            <small>{member.nickname}</small>
                          )}
                      </span>
                      <PersonJobTitle person={member} />
                    </button>
                  ))
                ) : (
                  <div className={styles.mentionHint}>
                    {t("communication.noMatchingMembers")}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
        <Input.TextArea
          ref={input}
          className={chatStyles.textarea}
          variant="borderless"
          value={draft.text}
          disabled={disabled}
          maxLength={maxLength}
          autoSize={{ minRows: 2, maxRows: 6 }}
          placeholder={t("communication.compose")}
          role={groupId ? "combobox" : undefined}
          aria-expanded={groupId ? open : undefined}
          aria-autocomplete={groupId ? "list" : undefined}
          aria-controls={open && !loading && !error ? menuId : undefined}
          aria-activedescendant={
            open && !loading && !error && candidates.length
              ? `${menuId}-${activeIndex}`
              : undefined
          }
          onChange={(event) => {
            const next = updateMentionDraft(
              draft,
              event.target.value,
              event.target.selectionStart,
            );
            onChange(next);
            updateQuery(next, event.target.selectionStart);
          }}
          onSelect={(event) => {
            const element = event.currentTarget;
            if (element.selectionStart === element.selectionEnd)
              updateQuery(draft, element.selectionStart);
            else setQuery(null);
          }}
          onBlur={() => setQuery(null)}
          onKeyDown={(event) => {
            if (event.nativeEvent.isComposing || event.keyCode === 229) return;
            if (open && !event.shiftKey) {
              if (event.key === "Escape") {
                event.preventDefault();
                setQuery(null);
                return;
              }
              if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                if (candidates.length)
                  setActive(
                    (activeIndex +
                      (event.key === "ArrowDown" ? 1 : -1) +
                      candidates.length) %
                      candidates.length,
                  );
                return;
              }
              if (event.key === "Enter" || event.key === "Tab") {
                event.preventDefault();
                if (!loading && !error && candidates[activeIndex])
                  choose(candidates[activeIndex]);
                return;
              }
            }
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              onSend();
            }
          }}
        />
      </div>
    );
  },
);

export default ColleagueMentionInput;
