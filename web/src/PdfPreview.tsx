import { useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import {
  getDocument,
  GlobalWorkerOptions,
  type PDFDocumentProxy,
  type RenderTask,
} from "pdfjs-dist";
import workerURL from "pdfjs-dist/build/pdf.worker.min.mjs?url";

GlobalWorkerOptions.workerSrc = workerURL;
const support = import.meta.env.BASE_URL + "pdf-support/";

export default function PdfPreview({
  buffer,
  slides = false,
}: {
  buffer: ArrayBuffer;
  slides?: boolean;
}) {
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null);
  const [error, setError] = useState("");
  const [page, setPage] = useState(1);
  const [zoom, setZoom] = useState("fit");
  const [width, setWidth] = useState(0);
  const [painting, setPainting] = useState(true);
  const [readText, setReadText] = useState(false);
  const [transcript, setTranscript] = useState("");
  const host = useRef<HTMLDivElement>(null);
  const paper = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let active = true;
    const loading = getDocument({
      data: buffer.slice(0),
      cMapUrl: support + "cmaps/",
      cMapPacked: true,
      standardFontDataUrl: support + "standard_fonts/",
      wasmUrl: support + "wasm/",
      iccUrl: support + "iccs/",
      enableXfa: false,
      maxImageSize: 25_000_000,
      canvasMaxAreaInBytes: 32_000_000,
    });
    loading.promise
      .then((d) => {
        if (active) setDoc(d);
      })
      .catch((e) => {
        if (active)
          setError(
            e?.name === "PasswordException"
              ? "文件有密码保护，请下载后打开。"
              : "PDF 无法读取，请下载原文件核对。",
          );
      });
    return () => {
      active = false;
      void loading.destroy().catch(() => {});
    };
  }, [buffer]);
  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const resize = new ResizeObserver(() => setWidth(el.clientWidth));
    resize.observe(el);
    setWidth(el.clientWidth);
    return () => resize.disconnect();
  }, []);

  useEffect(() => {
    if (!doc || !width || !paper.current) return;
    const container = paper.current;
    let active = true;
    let task: RenderTask | undefined;
    setPainting(true);
    setError("");
    container.replaceChildren();
    async function draw(document: PDFDocumentProxy) {
      const sheet = await document.getPage(page);
      if (!active) return;
      const original = sheet.getViewport({ scale: 1 });
      const scale =
        zoom === "fit"
          ? Math.max(0.05, (width - 18) / original.width)
          : Number(zoom);
      const view = sheet.getViewport({
        scale: Math.min(
          scale,
          5000 / Math.max(original.width, original.height),
        ),
      });
      const ratio = Math.min(
        window.devicePixelRatio || 1,
        2,
        Math.sqrt(6_000_000 / (view.width * view.height)),
      );
      const canvas = documentOwner.createElement("canvas");
      canvas.width = Math.max(1, Math.round(view.width * ratio));
      canvas.height = Math.max(1, Math.round(view.height * ratio));
      canvas.style.width = view.width + "px";
      canvas.style.height = view.height + "px";
      canvas.setAttribute("role", "img");
      canvas.setAttribute(
        "aria-label",
        (slides ? "幻灯片" : "文档") + " 第 " + page + " 页",
      );
      task = sheet.render({
        canvas,
        viewport: view,
        transform: [ratio, 0, 0, ratio, 0, 0],
        background: "white",
      });
      await task.promise;
      if (active) {
        container.replaceChildren(canvas);
        setPainting(false);
      }
    }
    const documentOwner = container.ownerDocument;
    draw(doc).catch(() => {
      if (active) {
        setPainting(false);
        setError("此页无法渲染。可尝试其他页面或下载原文件。");
      }
    });
    return () => {
      active = false;
      task?.cancel();
    };
  }, [doc, page, width, zoom, slides]);

  useEffect(() => {
    if (!doc || !readText) return;
    let active = true;
    setTranscript("正在读取…");
    doc
      .getPage(page)
      .then((p) => p.getTextContent())
      .then((content) => {
        if (!active) return;
        const text = content.items
          .flatMap((item) =>
            "str" in item ? [item.str + (item.hasEOL ? "\n" : "")] : [],
          )
          .join("");
        setTranscript(
          text.length > 64_000
            ? text.slice(0, 64_000) + "\n…（本页文字已截断）"
            : text.trim() || "本页没有可提取的文字。",
        );
      })
      .catch(() => {
        if (active) setTranscript("本页文字无法提取。");
      });
    return () => {
      active = false;
    };
  }, [doc, page, readText]);

  return (
    <section
      className="document-preview"
      aria-label={slides ? "幻灯片预览" : "分页文档预览"}
    >
      {doc && (
        <div className="document-toolbar">
          <button
            className="icon-button"
            aria-label="上一页"
            disabled={page <= 1}
            onClick={() => setPage(page - 1)}
          >
            <ChevronLeft size={17} />
          </button>
          <label className="page-number">
            <input
              key={page}
              type="number"
              aria-label="跳转到页码"
              min={1}
              max={doc.numPages}
              defaultValue={page}
              onBlur={(e) => {
                const n = Math.min(
                  doc.numPages,
                  Math.max(1, Number(e.currentTarget.value) || page),
                );
                e.currentTarget.value = String(Math.floor(n));
                setPage(Math.floor(n));
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") e.currentTarget.blur();
              }}
            />{" "}
            / {doc.numPages}
          </label>
          <button
            className="icon-button"
            aria-label="下一页"
            disabled={page >= doc.numPages}
            onClick={() => setPage(page + 1)}
          >
            <ChevronRight size={17} />
          </button>
          <select
            aria-label="预览缩放"
            value={zoom}
            onChange={(e) => setZoom(e.target.value)}
          >
            <option value="fit">适合宽度</option>
            <option value="1">100%</option>
            <option value="1.5">150%</option>
            <option value="2">200%</option>
          </select>
        </div>
      )}
      {error && (
        <p className="file-notice" role="alert">
          {error}
        </p>
      )}
      <div ref={host} className="pdf-viewport" aria-busy={painting && !error}>
        {!error && painting && (
          <span className="pdf-loading" role="status">
            正在渲染页面…
          </span>
        )}
        <div ref={paper} className="pdf-paper" />
      </div>
      {doc && (
        <details onToggle={(e) => setReadText(e.currentTarget.open)}>
          <summary>本页文字</summary>
          <pre className="text-preview">{transcript}</pre>
        </details>
      )}
    </section>
  );
}
