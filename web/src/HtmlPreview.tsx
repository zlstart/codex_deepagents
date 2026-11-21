import { useMemo, useState } from "react";
import { parse, type DefaultTreeAdapterTypes as Tree } from "parse5";

// parse5 builds an inert syntax tree. Never attach the original document or its
// resource URLs to a browser DOM, even temporarily during sanitization.
const elements = new Set(
  "html head body style main article section header footer aside nav div span p h1 h2 h3 h4 h5 h6 blockquote pre code kbd samp var strong b em i u s del ins small sub sup abbr cite time mark figure figcaption picture img hr br wbr ul ol li dl dt dd details summary table caption colgroup col thead tbody tfoot tr th td a address".split(
    " ",
  ),
);
const shapes = new Set(
  "svg g defs desc title path rect circle ellipse line polyline polygon text tspan linearGradient radialGradient stop clipPath".split(
    " ",
  ),
);
const discard = new Set(
  "script iframe object embed frame frameset noscript template form input textarea button link base meta audio video source track foreignObject".split(
    " ",
  ),
);
const attributes = new Set(
  "id class style dir lang title width height colspan rowspan span scope start reversed value open align valign border cellpadding cellspacing aria-label role alt".split(
    " ",
  ),
);
const geometry = new Set(
  "viewBox preserveAspectRatio x y x1 y1 x2 y2 cx cy r rx ry d points fill fill-opacity fill-rule stroke stroke-width stroke-linecap stroke-linejoin stroke-dasharray opacity transform font-family font-size text-anchor offset stop-color stop-opacity gradientUnits gradientTransform clip-path".split(
    " ",
  ),
);

function escape(value: string) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll('"', "&quot;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function staticDocument(source: string) {
  const tree = parse(source);
  let visited = 0;
  function render(node: Tree.Node, depth: number): string {
    if (++visited > 60_000 || depth > 160)
      throw new Error("页面结构过于复杂，请切换源码查看。");
    if (node.nodeName === "#text" && "value" in node) return escape(node.value);
    if (!("tagName" in node)) return "";
    const tag = node.tagName;
    if (discard.has(tag)) return "";
    // Preserve authored CSS in the isolated frame. CSP blocks all stylesheet,
    // font and image network loads; style end tags never enter this text node.
    if (tag === "style")
      return (
        "<style>" +
        node.childNodes
          .map((n) =>
            "value" in n ? n.value.replace(/<\/style/gi, "< /style") : "",
          )
          .join("") +
        "</style>"
      );
    const children = node.childNodes.map((n) => render(n, depth + 1)).join("");
    if (tag === "html" || tag === "head" || tag === "body") {
      // Rebuild the shell so the trusted policy always precedes author content.
      if (tag !== "body") return children;
    }
    if (!elements.has(tag) && !shapes.has(tag)) return children;
    const attrs = node.attrs
      .filter((a) => {
        if (a.namespace || a.prefix) return false;
        if (tag === "img" && a.name === "src")
          return /^data:image\/(?:png|jpeg|gif|webp|avif);base64,[a-z0-9+/=\s]+$/i.test(
            a.value,
          );
        return (
          attributes.has(a.name) || (shapes.has(tag) && geometry.has(a.name))
        );
      })
      .map((a) => " " + a.name + '="' + escape(a.value) + '"')
      .join("");
    return (
      "<" +
      tag +
      attrs +
      ">" +
      children +
      (["br", "hr", "img", "wbr", "col"].includes(tag) ? "" : "</" + tag + ">")
    );
  }
  const body = tree.childNodes.map((n) => render(n, 0)).join("");
  return `<!DOCTYPE html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"><meta name="referrer" content="no-referrer"><style>body{font:15px/1.55 system-ui,sans-serif;overflow-wrap:anywhere;margin:16px;color:#27241f;background:#fff}img,svg{max-width:100%}table{border-collapse:collapse}td,th{padding:4px 8px}</style></head>${body}</html>`;
}

export default function HtmlPreview({ text }: { text: string }) {
  const [source, setSource] = useState(false);
  const doc = useMemo(() => {
    try {
      return { html: staticDocument(text) };
    } catch {
      return { error: "此页面超过静态预览限制，可切换到源码。" };
    }
  }, [text]);
  return (
    <section className="document-preview">
      <div className="preview-switch" role="group" aria-label="HTML 查看方式">
        <button aria-pressed={!source} onClick={() => setSource(false)}>
          静态页面
        </button>
        <button aria-pressed={source} onClick={() => setSource(true)}>
          源码
        </button>
      </div>
      {source ? (
        <pre className="text-preview">{text}</pre>
      ) : doc.error ? (
        <p role="status">{doc.error}</p>
      ) : (
        <>
          <iframe
            className="html-document"
            title="HTML 静态页面"
            sandbox=""
            referrerPolicy="no-referrer"
            srcDoc={doc.html}
          />
          <small className="preview-footnote">
            显示页面排版和内嵌图片。脚本与外部资源已停用。
          </small>
        </>
      )}
    </section>
  );
}
