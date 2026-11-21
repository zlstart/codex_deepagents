import { useEffect, useMemo, useRef, useState } from "react";
import { SafeMarkdown } from "./SafeMarkdown";
import {
  Activity as ActivityIcon,
  BookOpen,
  Boxes,
  Check,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  CircleDot,
  CircleStop,
  Clock3,
  FilePenLine,
  FileSearch,
  FileText,
  FolderOpen,
  ListChecks,
  LoaderCircle,
  Search,
  Terminal,
  Wrench,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { running } from "./api";
import type { JsonValue, Run } from "./api";
import { useRunActivity } from "./useRunActivity";
import type { Activity } from "./useRunActivity";
import "./trace.css";

export const statusText: Record<string, string> = {
  running: "执行中",
  queued: "排队中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已停止",
  skipped: "已跳过",
  warning: "提醒",
};
const toolNames: Record<string, [string, LucideIcon]> = {
  execute: ["运行命令", Terminal],
  read_file: ["读取文件", FileText],
  write_file: ["写入文件", FilePenLine],
  edit_file: ["编辑文件", FilePenLine],
  ls: ["列出文件", FolderOpen],
  glob: ["查找文件", FileSearch],
  grep: ["搜索内容", Search],
  write_todos: ["更新任务清单", ListChecks],
  search_docs_by_lang_chain: ["检索 LangChain 文档", BookOpen],
  query_docs_filesystem_docs_by_lang_chain: ["查询文档目录", BookOpen],
};
const toolTitle = (item: Activity) =>
  item.kind === "subagent"
    ? "子智能体 · " + item.label
    : (toolNames[item.label]?.[0] ?? item.label);

function StatusBadge({ status }: { status: string }) {
  const Icon =
    (
      {
        running: LoaderCircle,
        queued: Clock3,
        completed: Check,
        failed: CircleAlert,
        cancelled: CircleStop,
      } as Record<string, LucideIcon>
    )[status] ?? CircleDot;
  return (
    <small className={"item-status status-" + status}>
      <Icon
        size={13}
        className={status === "running" ? "spin" : ""}
        aria-hidden
      />
      {statusText[status] ?? status}
    </small>
  );
}
type OpenFile = (path: string) => void;
function Prose({
  text,
  openFiles,
  streaming = false,
}: {
  text: string;
  openFiles: OpenFile;
  streaming?: boolean;
}) {
  return (
    <div
      className={"answer timeline-message" + (streaming ? " is-streaming" : "")}
    >
      <SafeMarkdown text={text} openFiles={openFiles} />
    </div>
  );
}

function primaryArgument(item: Activity) {
  const args = item.tool?.arguments;
  if (!args) return "";
  if (item.label === "write_todos" && Array.isArray(args.todos)) {
    const count = args.todos.filter(
      (t) =>
        typeof t === "object" &&
        t !== null &&
        !Array.isArray(t) &&
        t.status === "completed",
    ).length;
    return count + " / " + args.todos.length + " 项完成";
  }
  for (const key of [
    "command",
    "file_path",
    "query",
    "pattern",
    "path",
    "subagent_type",
    "description",
  ]) {
    if (typeof args[key] === "string") return args[key].trim().split("\n")[0];
  }
  return "";
}

function present(value: JsonValue) {
  // The API marks structured results with JSON types. Preserve literal source,
  // commands and text outputs, including whitespace and numeric lexemes.
  return typeof value === "string"
    ? { value, json: false }
    : { value: JSON.stringify(value, null, 2), json: true };
}
function JsonCode({ value }: { value: string }) {
  if (value.length > 16384) return <>{value}</>;
  const tokens =
    /("(?:\\.|[^"\\])*")(?=\s*:)|("(?:\\.|[^"\\])*")|\b(true|false|null)\b|(-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)/g;
  const parts = [];
  let end = 0;
  for (const match of value.matchAll(tokens)) {
    parts.push(value.slice(end, match.index));
    const kind = match[1] ? "key" : match[2] ? "string" : "literal";
    parts.push(
      <span key={match.index} className={"json-" + kind}>
        {match[0]}
      </span>,
    );
    end = match.index + match[0].length;
  }
  parts.push(value.slice(end));
  return <>{parts}</>;
}
function Field({
  label,
  value,
  clipped = false,
  command = false,
  syntax,
}: {
  label: string;
  value: JsonValue;
  clipped?: boolean;
  command?: boolean;
  syntax?: string;
}) {
  const data = present(value);
  return (
    <div className="trace-field">
      <div className="trace-field-label">
        <strong>{label}</strong>
        <span>
          {syntax ?? (command ? "Shell" : data.json ? "JSON" : "文本")}
        </span>
        {clipped && <em>已截断</em>}
      </div>
      <pre
        className={command ? "trace-command" : undefined}
        tabIndex={0}
        aria-label={label}
      >
        <code>
          {data.json || syntax === "JSON" ? (
            <JsonCode value={data.value} />
          ) : (
            data.value || "（空字符串）"
          )}
        </code>
      </pre>
    </div>
  );
}
function Details({ item }: { item: Activity }) {
  const p = item.tool;
  if (!p)
    return item.detail ? <Field label="执行记录" value={item.detail} /> : null;
  if (p.truncated && !p.arguments)
    return <p className="trace-note">{p.error ?? "结构化记录已截断"}</p>;
  const args = { ...p.arguments };
  const command =
    item.label === "execute" && typeof args.command === "string"
      ? args.command
      : null;
  if (command !== null) delete args.command;
  const source: { key: string; title: string; text: string }[] = [];
  const fields =
    item.label === "write_file"
      ? [["content", "文件内容"]]
      : item.label === "edit_file"
        ? [
            ["old_string", "替换前"],
            ["new_string", "替换后"],
          ]
        : item.kind === "subagent"
          ? [["description", "委派任务"]]
          : [];
  for (const [key, title] of fields) {
    const value = args[key];
    if (typeof value === "string") {
      source.push({ key, title, text: value });
      delete args[key];
    }
  }
  const extension =
    typeof args.file_path === "string"
      ? args.file_path.match(/\.([a-z0-9]+)$/i)?.[1]
      : undefined;
  const syntax = extension
    ? ((
        {
          py: "Python",
          md: "Markdown",
          js: "JavaScript",
          ts: "TypeScript",
          json: "JSON",
          sh: "Shell",
        } as Record<string, string>
      )[extension] ?? extension.toUpperCase())
    : undefined;
  const error = item.status === "failed";
  return (
    <div className="trace-fields">
      <div className="trace-detail-id">
        <span>{item.kind === "subagent" ? "委派工具" : "工具"}</span>
        <code>{item.kind === "subagent" ? "task" : item.label}</code>
      </div>
      {command !== null && (
        <Field
          label="输入参数 · command"
          value={command}
          command
          clipped={p.arguments_truncated}
        />
      )}
      {(Object.keys(args).length > 0 || command === null) && (
        <Field label="输入参数" value={args} clipped={p.arguments_truncated} />
      )}
      {source.map((part) => (
        <Field
          key={part.key}
          label={part.title + " · " + part.key}
          value={part.text}
          syntax={syntax}
          clipped={p.arguments_truncated}
        />
      ))}
      {p.result !== undefined &&
        !(error && p.error && (p.result === null || p.result === p.error)) && (
          <Field
            label={error ? "失败输出" : "返回结果"}
            value={p.result}
            clipped={p.result_truncated}
          />
        )}
      {p.error && <Field label="错误信息" value={p.error} />}
      {p.exit_code != null && (
        <div className={"trace-exit" + (p.exit_code === 0 ? "" : " is-error")}>
          退出码 <code>{p.exit_code}</code>
        </div>
      )}
    </div>
  );
}

type Groups = Map<string | null, Activity[]>;
function groupActivities(items: Activity[]) {
  const byId = new Map(items.map((item) => [item.id, item]));
  const groups: Groups = new Map();
  for (const item of items) {
    if (item.kind === "run" || item.kind === "agent") continue;
    let parent = byId.get(item.parent_id ?? "");
    const seen = new Set([item.id]);
    while (parent && !seen.has(parent.id) && parent.kind !== "subagent") {
      seen.add(parent.id);
      parent = byId.get(parent.parent_id ?? "");
    }
    const key = parent && !seen.has(parent.id) ? parent.id : null;
    const siblings = groups.get(key);
    if (siblings) siblings.push(item);
    else groups.set(key, [item]);
  }
  return groups;
}
type TimelineProps = {
  groups: Groups;
  openFiles: OpenFile;
  finalId?: string;
  answer?: string | null;
};
function ToolItem({
  item,
  level,
  ...timeline
}: TimelineProps & { item: Activity; level: number }) {
  const delegated = item.kind === "subagent";
  const children = timeline.groups.get(item.id) ?? [];
  const [expanded, setExpanded] = useState(
    delegated && (running(item.status) || !item.tool),
  );
  const [showBrief, setShowBrief] = useState(false);
  const Icon = delegated
    ? Boxes
    : (toolNames[item.label]?.[1] ??
      (item.label.includes("MCP") ? BookOpen : Wrench));
  const title = toolTitle(item);
  const target = delegated ? "" : primaryArgument(item);
  const expandable = Boolean(item.tool || item.detail || children.length);
  return (
    <li
      className={
        "activity-item" +
        (delegated ? " is-subagent" : "") +
        (item.status === "failed" ? " has-error" : "")
      }
      data-kind={item.kind}
    >
      <div className="trace-card">
        <button
          type="button"
          className="node-toggle"
          aria-expanded={expandable ? expanded : undefined}
          disabled={!expandable}
          onClick={() => setExpanded(!expanded)}
        >
          <span className="trace-icon">
            <Icon size={15} aria-hidden />
          </span>
          <span className="trace-caption">
            <strong>{title}</strong>
            {target && <code title={target}>{target}</code>}
          </span>
          {!expanded && item.tool?.exit_code != null && (
            <span className="trace-exit-inline">
              退出 {item.tool.exit_code}
            </span>
          )}
          <StatusBadge status={item.status} />
          {expandable &&
            (expanded ? (
              <ChevronDown size={14} aria-hidden />
            ) : (
              <ChevronRight size={14} aria-hidden />
            ))}
        </button>
        {expanded && (
          <div className="trace-card-body">
            {delegated && (item.tool || item.detail) ? (
              <>
                <button
                  type="button"
                  className="trace-brief-toggle"
                  aria-expanded={showBrief}
                  onClick={() => setShowBrief(!showBrief)}
                >
                  {showBrief ? (
                    <ChevronDown size={13} />
                  ) : (
                    <ChevronRight size={13} />
                  )}
                  委派参数与返回结果
                </button>
                {showBrief && <Details item={item} />}
              </>
            ) : (
              <Details item={item} />
            )}
            {children.length > 0 && level < 32 && (
              <Timeline
                {...timeline}
                items={children}
                level={level + 1}
                expanded
              />
            )}
          </div>
        )}
      </div>
    </li>
  );
}
function Timeline({
  items,
  expanded,
  level = 0,
  ...timeline
}: TimelineProps & { items: Activity[]; expanded: boolean; level?: number }) {
  return (
    <ol className="activity-list">
      {items.map((item) => {
        if (item.kind === "model") {
          const text =
            item.id === timeline.finalId
              ? (timeline.answer ?? item.text)
              : item.text;
          if (text)
            return (
              <li key={item.id} className="activity-speech" data-kind="model">
                <Prose
                  text={text}
                  openFiles={timeline.openFiles}
                  streaming={running(item.status)}
                />
                {item.textTruncated && item.id !== timeline.finalId && (
                  <small className="trace-note">此段文字已截断</small>
                )}
                {["failed", "cancelled"].includes(item.status) && (
                  <StatusBadge status={item.status} />
                )}
              </li>
            );
          if (
            expanded &&
            (running(item.status) ||
              item.status === "failed" ||
              item.status === "cancelled")
          )
            return (
              <li key={item.id} className="trace-model-state">
                <span>
                  {running(item.status) ? "正在生成回复" : "模型响应"}
                </span>
                <StatusBadge status={item.status} />
                {item.detail && <small>{item.detail}</small>}
              </li>
            );
          return null;
        }
        return expanded ? (
          <ToolItem key={item.id} {...timeline} item={item} level={level} />
        ) : null;
      })}
    </ol>
  );
}
export default function TraceTree({
  run,
  openFiles,
}: {
  run: Run;
  openFiles: OpenFile;
}) {
  const [expanded, setExpanded] = useState(running(run.status));
  const container = useRef<HTMLElement>(null);
  const [viewed, setViewed] = useState(false);
  useEffect(() => {
    const node = container.current;
    if (!node || viewed) return;
    if (!("IntersectionObserver" in window)) {
      setViewed(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setViewed(true);
          observer.disconnect();
        }
      },
      { rootMargin: "350px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [viewed]);
  const { items, error, loaded, retry } = useRunActivity(
    run,
    viewed || expanded || running(run.status),
  );
  const groups = useMemo(() => groupActivities(items), [items]);
  const roots = groups.get(null) ?? [];
  const lastModel = roots.filter((i) => i.kind === "model").at(-1);
  const answer = run.answer?.trim();
  const streamed = lastModel?.text.trim();
  const finalId =
    answer &&
    lastModel &&
    ((lastModel === roots.at(-1) &&
      lastModel.hasToolCalls === false &&
      run.status === "completed") ||
      (streamed &&
        (answer === streamed ||
          (lastModel === roots.at(-1) && answer.startsWith(streamed)))))
      ? lastModel.id
      : undefined;
  const current = running(run.status)
    ? items
        .filter((i) => !["run", "agent"].includes(i.kind) && running(i.status))
        .at(-1)
    : undefined;
  const tools = items.filter((i) => i.kind === "tool");
  const failed = tools.filter((i) => i.status === "failed").length;
  return (
    <section ref={container} className="trace-tree" aria-label="执行时间线">
      <button
        type="button"
        className="trace-summary"
        aria-expanded={expanded}
        onClick={() => setExpanded(!expanded)}
      >
        <ActivityIcon size={15} aria-hidden />
        <span>执行过程</span>
        {current ? (
          <span className="trace-current">
            {current.kind === "model" ? "生成回复" : toolTitle(current)}
          </span>
        ) : (
          tools.length > 0 && (
            <span className="trace-count">{tools.length} 次工具调用</span>
          )
        )}
        {failed > 0 && (
          <span className="trace-failed-count">{failed} 项失败</span>
        )}
        <StatusBadge status={run.status} />
        {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
      </button>
      {error && (
        <div className="trace-reconnect" role="status">
          {error}{" "}
          <button type="button" onClick={retry}>
            重新连接
          </button>
        </div>
      )}
      {!loaded && expanded && !error && (
        <small className="trace-note">正在读取执行记录…</small>
      )}
      {loaded && !roots.length && expanded && running(run.status) && (
        <small className="trace-note">等待执行事件…</small>
      )}
      <Timeline
        groups={groups}
        items={roots}
        openFiles={openFiles}
        expanded={expanded}
        finalId={finalId}
        answer={run.answer}
      />
      {!finalId && run.answer && (
        <Prose text={run.answer} openFiles={openFiles} />
      )}
    </section>
  );
}
