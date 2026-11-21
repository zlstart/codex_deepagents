import Markdown, { defaultUrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";

function workspaceTarget(href: string, documentPath: string) {
  if (/^(?:[a-z][a-z0-9+.-]*:|\/\/|#)/i.test(href)) return null;
  let path: string;
  try {
    path = decodeURIComponent(href.split("#", 1)[0]);
  } catch {
    return null;
  }
  const absolute = /^(?:\/|workspace\/|mnt\/data\/)/.test(path);
  const parts = absolute ? [] : documentPath.split("/").slice(0, -1);
  for (const part of path
    .replace(/^\/?(?:workspace\/|mnt\/data\/)/, "")
    .split("/")) {
    if (!part || part === ".") continue;
    if (part === "..") {
      if (!parts.length) return null;
      parts.pop();
    } else {
      parts.push(part);
    }
  }
  return parts.join("/") || null;
}

export function SafeMarkdown({
  text,
  openFiles,
  documentPath = "",
}: {
  text: string;
  openFiles: (path: string) => void;
  documentPath?: string;
}) {
  return (
    <Markdown
      skipHtml
      remarkPlugins={[remarkGfm]}
      urlTransform={(url) =>
        url.startsWith("sandbox:/workspace/")
          ? url.slice("sandbox:".length)
          : defaultUrlTransform(url)
      }
      components={{
        img: ({ alt }) => (
          <span className="markdown-image-label">{alt || "图片"}</span>
        ),
        a: ({ href, children }) => {
          if (!href) return <span>{children}</span>;
          if (/^https?:\/\//i.test(href))
            return (
              <a href={href} target="_blank" rel="noreferrer noopener">
                {children}
              </a>
            );
          const path = workspaceTarget(href, documentPath);
          return path ? (
            <button
              type="button"
              className="inline-file"
              onClick={() => openFiles(path)}
            >
              {children}
            </button>
          ) : (
            <span>{children}</span>
          );
        },
      }}
    >
      {text}
    </Markdown>
  );
}
