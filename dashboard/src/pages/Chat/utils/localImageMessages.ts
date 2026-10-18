import type { ChatMessage } from "../hooks/useChat";

/** The delivered image replaces redundant file-path labels in the reply. */
export function imageMessages(messages: ChatMessage[]): ChatMessage[] {
  const paths = messages.flatMap((message) =>
    (message.localArtifacts ?? [])
      .filter((file) => /\.(png|jpe?g|gif|webp)$/i.test(file.path))
      .map((file) => file.path.replaceAll("\\", "/")),
  );
  if (!paths.length) return messages;
  const delivered = (label: string) => {
    const path = label.replaceAll("\\", "/").replace(/^\.\//, "");
    return paths.some((full) => full === path || full.endsWith("/" + path));
  };
  const labels = paths
    .flatMap((path) => {
      const parts = path.split("/");
      return parts.map((_, index) => parts.slice(index).join("/"));
    })
    .filter(Boolean)
    .sort((a, b) => b.length - a.length)
    .map((path) => path.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const pathLabels = new RegExp(
    "(?<![\\w./\\\\-])(?:[:：]\\s*)?`?(?:" +
      labels.join("|") +
      ")`?(?![\\w./\\\\-])",
    "g",
  );
  return messages.map((message) => {
    if (message.role !== "assistant" || message.toolData) return message;
    const receipt =
      message.localArtifacts?.length &&
      message.content === "本次任务生成的本地文件";
    const displayText = (text: string) =>
      receipt
        ? ""
        : text
            .replace(/!?\[[^\]\n]*\]\(([^)\n]+)\)/g, (match, label: string) =>
              delivered(label) ? "" : match,
            )
            .replace(pathLabels, "");
    const content = displayText(message.content);
    const contentBlocks = message.contentBlocks?.map((block) => {
      if (block.type !== "text") return block;
      const text = displayText(block.content);
      return text === block.content ? block : { ...block, content: text };
    });
    return content === message.content &&
      !contentBlocks?.some(
        (block, index) => block !== message.contentBlocks?.[index],
      )
      ? message
      : { ...message, content, contentBlocks };
  });
}
