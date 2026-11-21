import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import {
  ArrowDownToLine,
  ArrowLeft,
  Box,
  File,
  FileImage,
  FileText,
  FolderOpen,
  Maximize2,
  Minimize2,
  RefreshCw,
  X,
} from "lucide-react";
import { fileSize, fileURL, messageOf, request, scope } from "./api";
import type { Artifact } from "./api";
import { SafeMarkdown } from "./SafeMarkdown";
const CsvPreview = lazy(() => import("./CsvPreview"));
const ModelViewer = lazy(() => import("./ModelViewer"));
const ext = (p: string) => p.toLowerCase().split(".").pop() || "";
function formatOf(f: Artifact) {
  const e = ext(f.name);
  if (e === "glb") return "model";
  if (e === "csv") return "csv";
  if (
    ["md", "markdown", "mdown"].includes(e) ||
    /^(?:text\/(?:markdown|x-markdown))(?:;|$)/i.test(f.mime_type)
  )
    return "markdown";
  if (["png", "jpg", "jpeg", "webp", "gif", "avif"].includes(e)) return "image";
  if (
    [
      "txt",
      "json",
      "py",
      "js",
      "ts",
      "toml",
      "yaml",
      "yml",
      "log",
      "obj",
      "html",
      "xml",
      "svg",
      "scad",
      "sh",
      "css",
      "ini",
    ].includes(e) ||
    f.mime_type.startsWith("text/")
  )
    return "text";
  return "other";
}
function ArtifactPreview({
  sid,
  file,
  openFiles,
}: {
  sid: string;
  file: Artifact;
  openFiles: (path: string) => void;
}) {
  const [raw, setRaw] = useState(false);
  const [bytes, setBytes] = useState<ArrayBuffer | null>(null),
    [error, setError] = useState(""),
    [src, setSrc] = useState(""),
    [attempt, setAttempt] = useState(0);
  const kind = formatOf(file);
  useEffect(() => {
    const ctrl = new AbortController();
    let objectURL = "";
    setBytes(null);
    setError("");
    setSrc("");
    async function load() {
      if (kind === "other") return;
      const limit =
        kind === "text" || kind === "markdown"
          ? 512 * 1024
          : kind === "csv"
            ? 8 * 1024 * 1024
            : 32 * 1024 * 1024;
      if (file.size > limit)
        throw new Error("文件超过预览大小上限，可下载后查看。");
      const r = await fetch(fileURL(sid, file.path), {
        signal: ctrl.signal,
        credentials: "same-origin",
      });
      if (!r.ok) throw new Error("读取文件失败（" + r.status + "）。");
      const b = await r.arrayBuffer();
      if (ctrl.signal.aborted) return;
      if (b.byteLength > limit)
        throw new Error("文件超过预览大小上限，可下载后查看。");
      if (kind === "image") {
        const mime = ext(file.name) === "jpg" ? "jpeg" : ext(file.name);
        objectURL = URL.createObjectURL(
          new Blob([b], { type: "image/" + mime }),
        );
        setSrc(objectURL);
      } else setBytes(b);
    }
    load().catch((e) => {
      if (!ctrl.signal.aborted) setError(messageOf(e));
    });
    return () => {
      ctrl.abort();
      if (objectURL) URL.revokeObjectURL(objectURL);
    };
  }, [file.path, file.size, file.name, file.modified_at, kind, sid, attempt]);
  if (error)
    return (
      <div className="file-notice" role="alert">
        {error}
        <button
          className="text-button"
          onClick={() => setAttempt((i) => i + 1)}
        >
          重新读取
        </button>
      </div>
    );
  if (kind === "other")
    return (
      <div className="file-notice">
        <File size={28} />
        <p>此格式可下载后查看。</p>
      </div>
    );
  if (kind === "image" && src)
    return (
      <img
        className="artifact-image"
        src={src}
        alt={file.name}
        onError={() => setError("浏览器无法解码此图片。")}
      />
    );
  if (!bytes) return <div className="file-notice">正在读取文件…</div>;
  if (kind === "model")
    return (
      <Suspense fallback={<p>正在加载三维预览…</p>}>
        <ModelViewer buffer={bytes} />
      </Suspense>
    );
  if (kind === "csv")
    return (
      <Suspense fallback={<p>正在读取表格…</p>}>
        <CsvPreview buffer={bytes} />
      </Suspense>
    );
  const text = new TextDecoder().decode(bytes);
  if (kind === "markdown")
    return (
      <section className="markdown-preview">
        <div
          className="preview-switch"
          role="group"
          aria-label="Markdown 查看方式"
        >
          <button
            type="button"
            aria-pressed={!raw}
            onClick={() => setRaw(false)}
          >
            正文
          </button>
          <button type="button" aria-pressed={raw} onClick={() => setRaw(true)}>
            原文
          </button>
        </div>
        {raw ? (
          <pre className="text-preview">{text}</pre>
        ) : (
          <article className="answer markdown-document">
            <SafeMarkdown
              text={text}
              openFiles={openFiles}
              documentPath={file.path}
            />
          </article>
        )}
      </section>
    );
  return <pre className="text-preview">{text}</pre>;
}
export function Artifacts({
  sid,
  version,
  requested,
  onClose,
}: {
  sid: string | null;
  requested: { path: string } | null;
  version: string;
  onClose: () => void;
}) {
  const [files, setFiles] = useState<Artifact[]>([]),
    [selected, select] = useState<string | null>(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(false),
    [expanded, setExpanded] = useState(false),
    [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (requested) select(requested.path);
  }, [requested]);
  const retry = useCallback(() => setAttempt((i) => i + 1), []);
  useEffect(() => {
    if (!sid) {
      setFiles([]);
      return;
    }
    const ctrl = new AbortController();
    setLoading(true);
    setError("");
    request<Artifact[]>(scope(sid) + "/files", { signal: ctrl.signal })
      .then(setFiles)
      .catch((e) => {
        if (!ctrl.signal.aborted) setError(messageOf(e));
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setLoading(false);
      });
    return () => ctrl.abort();
  }, [sid, version, attempt]);
  const file = files.find((f) => f.path === selected);
  return (
    <aside
      className={"artifacts" + (expanded && file ? " expanded" : "")}
      aria-label="工作区文件"
    >
      <div className="panel-heading">
        <span>
          <FolderOpen size={17} />
          工作区文件
        </span>
        {file && (
          <button
            className="icon-button expand-preview"
            aria-label={expanded ? "缩小预览" : "放大预览"}
            aria-pressed={expanded}
            onClick={() => setExpanded((v) => !v)}
          >
            {expanded ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
          </button>
        )}
        <button className="icon-button" aria-label="关闭文件" onClick={onClose}>
          <X size={18} />
        </button>
      </div>
      <div className="file-toolbar">
        {file ? (
          <button className="text-button" onClick={() => select(null)}>
            <ArrowLeft size={15} />
            全部文件
          </button>
        ) : (
          <span>{!sid || !loading ? files.length + " 项" : "加载中…"}</span>
        )}
        {sid && (
          <button className="icon-button" onClick={retry} aria-label="刷新文件">
            <RefreshCw size={15} className={loading ? "spin" : ""} />
          </button>
        )}
      </div>
      {error ? (
        <div className="error" role="alert">
          {error}
        </div>
      ) : file ? (
        <div className="preview-wrap">
          <div className="preview-meta">
            <div>
              <h3>{file.name}</h3>
              <small>
                {file.path} · {fileSize(file.size)}
              </small>
            </div>
            <a
              className="icon-button"
              aria-label="下载文件"
              href={fileURL(sid!, file.path, true)}
              download
            >
              <ArrowDownToLine size={18} />
            </a>
          </div>
          <ArtifactPreview
            key={file.path}
            sid={sid!}
            file={file}
            openFiles={select}
          />
        </div>
      ) : files.length ? (
        <div className="file-list">
          {files.map((f) => {
            const Icon = {
              model: Box,
              image: FileImage,
              text: FileText,
              markdown: FileText,
              csv: FileText,
              other: File,
            }[formatOf(f)];
            return (
              <button
                key={f.path}
                onClick={() => select(f.path)}
                className="file-row"
              >
                <Icon size={19} />
                <span>
                  <b>{f.name}</b>
                  <small>
                    {f.path.includes("/") ? f.path + " · " : ""}
                    {fileSize(f.size)}
                  </small>
                </span>
              </button>
            );
          })}
        </div>
      ) : (
        !loading && (
          <div className="file-notice empty-files">
            <FolderOpen size={30} />
            <h3>暂无文件</h3>
            <p>执行中生成的文件会显示在这里。</p>
          </div>
        )
      )}
    </aside>
  );
}
