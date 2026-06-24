import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

type MarkdownTextVariant = "message" | "compact" | "report";

interface MarkdownTextProps {
  content: string;
  variant?: MarkdownTextVariant;
  className?: string;
}

const markdownComponents: Components = {
  a({ node: _node, ...props }) {
    return (
      <a {...props} rel="noreferrer noopener" target="_blank" />
    );
  },
};

export function MarkdownText({
  content,
  variant = "message",
  className,
}: MarkdownTextProps) {
  const trimmed = content.trim();
  if (!trimmed) {
    return null;
  }

  const classes = [
    "markdown-text",
    `markdown-text-${variant}`,
    className,
  ].filter(Boolean).join(" ");

  return (
    <div className={classes}>
      <ReactMarkdown
        components={markdownComponents}
        remarkPlugins={[remarkGfm]}
        skipHtml
      >
        {trimmed}
      </ReactMarkdown>
    </div>
  );
}
