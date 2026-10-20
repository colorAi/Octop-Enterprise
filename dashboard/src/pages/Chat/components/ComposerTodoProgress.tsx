import { useId, useMemo, useState } from "react";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  ListTodo,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { TodoListView } from "../../../components/TodoProgressPanel";
import { countCompletedTodos } from "../../../utils/parseWriteTodos";
import type { ChatMessage } from "../hooks/useChat";
import { currentTodoProgress } from "../utils/currentTodoProgress";
import styles from "./ComposerTodoProgress.module.less";

export default function ComposerTodoProgress({
  messages,
  isStreaming,
  hostAgentId,
}: {
  messages: readonly ChatMessage[];
  isStreaming: boolean;
  hostAgentId?: string | null;
}) {
  const { t } = useTranslation();
  const detailsId = useId();
  const progress = useMemo(
    () => currentTodoProgress(messages, hostAgentId),
    [messages, hostAgentId],
  );
  const [expandedTurn, setExpandedTurn] = useState<string | null>(null);
  if (!progress) return null;

  const { items, turnId } = progress;
  const expanded = expandedTurn === turnId;
  const completed = countCompletedTodos(items);
  const allCompleted = completed === items.length;
  const current = items.find((item) => item.status === "in_progress");
  const Icon = allCompleted ? CheckCircle2 : ListTodo;
  const Chevron = expanded ? ChevronDown : ChevronRight;

  return (
    <section
      className={styles.panel}
      aria-label={t("chatUsage.todoListTitle", "Task progress")}
    >
      <button
        type="button"
        className={styles.toggle}
        aria-expanded={expanded}
        aria-controls={detailsId}
        onClick={() => setExpandedTurn(expanded ? null : turnId)}
      >
        <Icon
          size={16}
          className={allCompleted ? styles.completed : undefined}
          aria-hidden="true"
        />
        <span className={styles.title}>
          {t("chatUsage.todoListTitle", "Task progress")}
        </span>
        <span className={styles.summary}>
          {t("chatUsage.todoListSummary", {
            completed,
            total: items.length,
            defaultValue: "{{completed}}/{{total}} done",
          })}
        </span>
        <span className={styles.current} title={current?.content}>
          {current
            ? t("chatUsage.todoCurrentStep", {
                step: current.content,
                defaultValue: "Current: {{step}}",
              })
            : null}
        </span>
        <Chevron size={16} aria-hidden="true" />
      </button>
      <div id={detailsId} hidden={!expanded} className={styles.details}>
        <TodoListView
          items={items}
          isStreaming={isStreaming}
          showHeader={false}
          className={styles.list}
        />
      </div>
    </section>
  );
}
