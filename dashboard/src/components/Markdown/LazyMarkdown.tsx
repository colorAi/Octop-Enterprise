import { lazy, Suspense } from "react";
import type { MarkdownProps } from "./index";

const Markdown = lazy(() => import("./index"));

function MarkdownPlainFallback({ content }: { content: string }) {
  return (
    <div style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
      {content}
    </div>
  );
}

export default function LazyMarkdown(props: MarkdownProps) {
  return (
    <Suspense fallback={<MarkdownPlainFallback content={props.content} />}>
      <Markdown {...props} />
    </Suspense>
  );
}
