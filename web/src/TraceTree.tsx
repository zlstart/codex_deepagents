import { useEffect, useState } from "react";
import {
  Activity,
  Bot,
  Check,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  Clock3,
  LoaderCircle,
  Terminal,
} from "lucide-react";
import { messageOf, request, running, scope } from "./api";
import type { EventPage, Run, Trace } from "./api";
export const statusText: Record<string, string> = {
  running: "执行中",
  queued: "排队中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已停止",
  skipped: "已跳过",
  warning: "提醒",
};
const StatusIcon = ({ status }: { status: string }) =>
  status === "completed" ? (
    <Check size={12} />
  ) : status === "queued" ? (
    <Clock3 size={12} />
  ) : running(status) ? (
    <LoaderCircle size={12} className="spin" />
  ) : status === "failed" ? (
    <CircleAlert size={12} />
  ) : null;
function Node({
  node,
  childrenOf,
  level,
}: {
  node: Trace;
  childrenOf: Map<string, Trace[]>;
  level: number;
}) {
  const [expanded, setExpanded] = useState(level < 2),
    children = childrenOf.get(node.id) || [];
  const Icon =
    node.kind === "tool"
      ? Terminal
      : node.kind === "subagent" || node.kind === "agent"
        ? Bot
        : Activity;
  const hasChildren = children.length > 0 || !!node.detail;
  return (
    <li>
      <button
        className="node-toggle"
        disabled={!hasChildren}
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={hasChildren ? expanded : undefined}
      >
        {hasChildren ? (
          expanded ? (
            <ChevronDown size={13} />
          ) : (
            <ChevronRight size={13} />
          )
        ) : (
          <span className="tree-spacer" />
        )}
        <Icon size={15} />
        <span className="node-title">{node.label}</span>
        <span className={"node-status status-" + node.status}>
          <StatusIcon status={node.status} />
          {statusText[node.status] || node.status}
        </span>
      </button>
      {expanded && hasChildren && (
        <div className="node-children">
          {node.detail && <pre>{node.detail}</pre>}
          {children.length > 0 && level < 32 && (
            <ul>
              {children.map((child) => (
                <Node
                  key={child.id}
                  node={child}
                  childrenOf={childrenOf}
                  level={level + 1}
                />
              ))}
            </ul>
          )}
        </div>
      )}
    </li>
  );
}
export default function TraceTree({ run }: { run: Run }) {
  const [open, setOpen] = useState(running(run.status)),
    [events, setEvents] = useState<Trace[]>([]),
    [loaded, setLoaded] = useState(false),
    [retry, setRetry] = useState(0),
    [error, setError] = useState("");
  useEffect(() => {
    if (!open && !running(run.status)) return;
    const c = new AbortController();
    let timer: ReturnType<typeof setTimeout>,
      cursor = 0;
    const byId = new Map<string, Trace>();
    async function poll() {
      try {
        let page: EventPage;
        do {
          page = await request<EventPage>(
            scope(run.session_id) +
              "/runs/" +
              encodeURIComponent(run.id) +
              "/events?after=" +
              cursor,
            { signal: c.signal },
          );
          page.events.forEach((evt) => byId.set(evt.id, evt));
          const next = page.cursor;
          if (next <= cursor) break;
          cursor = next;
        } while (
          (page.has_more ?? page.events.length === 500) &&
          !c.signal.aborted
        );
        if (c.signal.aborted) return;
        setEvents([...byId.values()]);
        setLoaded(true);
        setError("");
        if (running(page.status)) timer = setTimeout(poll, 1100);
      } catch (e) {
        if (!c.signal.aborted) {
          setError(messageOf(e));
          timer = setTimeout(poll, 3000);
        }
      }
    }
    poll();
    return () => {
      c.abort();
      clearTimeout(timer);
    };
  }, [run.id, run.session_id, run.status, open, retry]);
  const ids = new Set(events.map((e) => e.id)),
    roots: Trace[] = [],
    childrenOf = new Map<string, Trace[]>();
  events.forEach((e) => {
    if (!e.parent_id || !ids.has(e.parent_id)) roots.push(e);
    else {
      const children = childrenOf.get(e.parent_id) || [];
      children.push(e);
      childrenOf.set(e.parent_id, children);
    }
  });
  const current = running(run.status)
    ? events
        .filter((event) => event.kind !== "run" && running(event.status))
        .at(-1)
    : undefined;
  return (
    <section className="trace-tree">
      <button
        className="trace-summary"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <Activity size={16} />
        <span>执行过程</span>
        {current && (
          <span className="trace-current" title={current.label}>
            {current.label}
          </span>
        )}
        <small className={"status-" + run.status}>
          <StatusIcon status={run.status} />
          {statusText[run.status]}
        </small>
        {open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
      </button>
      {open && (
        <div className="trace-content">
          {error && (
            <div className="reconnect" role="status">
              {error} 正在重连。
              <button
                className="text-button"
                onClick={() => setRetry((i) => i + 1)}
              >
                重试
              </button>
            </div>
          )}
          {roots.length > 0 ? (
            <ul>
              {roots.map((e) => (
                <Node key={e.id} node={e} childrenOf={childrenOf} level={0} />
              ))}
            </ul>
          ) : (
            <p>
              {!loaded
                ? "正在读取执行记录…"
                : running(run.status)
                  ? "等待执行事件…"
                  : "没有记录到执行事件。"}
            </p>
          )}
        </div>
      )}
    </section>
  );
}
