import { useCallback, useEffect, useRef, useState } from "react";
import {
  FilePenLine,
  ListChecks,
  Lightbulb,
  ArrowUp,
  ChevronRight,
  FolderClosed,
  LogOut,
  Menu,
  MessageSquare,
  Plus,
  Square,
  UserRound,
  X,
} from "lucide-react";
import { Auth } from "./Auth";
import { AgentMark, Brand } from "./Brand";
import { Artifacts } from "./Artifacts";
import TraceTree, { statusText } from "./TraceTree";
import { HttpError, messageOf, post, request, running, scope } from "./api";
import type { Identity, Run, Session } from "./api";

const starters = [
  {
    title: "梳理一个想法",
    icon: Lightbulb,
    prompt: "帮我梳理一个想法。先问我几个关键问题，再整理成清晰的提纲。",
  },
  {
    title: "制定工作计划",
    icon: ListChecks,
    prompt:
      "我想制定一份工作计划。请先了解目标、时间和资源，再帮我拆解行动步骤。",
  },
  {
    title: "整理一份文档",
    icon: FilePenLine,
    prompt: "请帮我把接下来的材料整理成一份结构清晰、方便分享的文档。",
  },
];

function sessionTitle(text: string) {
  const sentence =
    text
      .trim()
      .split(/[。！？：!?\r\n]|\.(?=\s|$)|:(?=\s)/u)
      .find((part) => part.trim())
      ?.trim() || "新会话";
  const chars = Array.from(sentence);
  return chars.length > 28 ? chars.slice(0, 27).join("") + "…" : sentence;
}

function Welcome() {
  return (
    <div className="welcome">
      <AgentMark large />
      <h1>有什么可以帮你？</h1>
      <p>从一个问题、一个想法，或一项待完成的工作开始。</p>
    </div>
  );
}
function Starters({ choose }: { choose: (s: string) => void }) {
  return (
    <div className="starters" aria-label="任务示例">
      {starters.map(({ title, icon: Icon, prompt }) => (
        <button
          type="button"
          key={title}
          className="starter"
          onClick={() => choose(prompt)}
        >
          <Icon size={16} />
          {title}
        </button>
      ))}
    </div>
  );
}
function RunEntry({
  run,
  openFiles,
}: {
  run: Run;
  openFiles: (path: string) => void;
}) {
  return (
    <article className="run-entry">
      <div className="prompt">
        <div className="speaker">你</div>
        <p>{run.prompt}</p>
      </div>
      <div className="speaker assistant-speaker">
        <AgentMark /> FAMSUN Agent
      </div>
      <TraceTree run={run} openFiles={openFiles} />
      {run.error && (
        <p role="alert" className="error">
          {run.error}
        </p>
      )}
      {run.status === "cancelled" && (
        <small className="muted">已停止本次执行。</small>
      )}
    </article>
  );
}
function Workspace({
  user,
  onLogout,
}: {
  user: Identity;
  onLogout: () => void;
}) {
  const [sessions, setSessions] = useState<Session[]>([]),
    [sid, setSid] = useState<string | null>(null),
    [runs, setRuns] = useState<Run[]>([]),
    [draft, setDraft] = useState("");
  const [preview, setPreview] = useState<{ path: string } | null>(null);
  const [files, setFiles] = useState(false),
    [nav, setNav] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [historyError, setHistoryError] = useState("");
  const [sessionsLoaded, setSessionsLoaded] = useState(false),
    [historyLoaded, setHistoryLoaded] = useState(false),
    [attempt, setAttempt] = useState(0),
    [reloading, setReloading] = useState(0);
  const input = useRef<HTMLTextAreaElement>(null),
    scroll = useRef<HTMLDivElement>(null),
    content = useRef<HTMLDivElement>(null),
    nearEnd = useRef(true),
    activeSid = useRef<string | null>(null),
    submitLock = useRef(false);
  const pendingSends = useRef(
    new Map<string, { prompt: string; key: string }>(),
  );
  const drafts = useRef<Record<string, string>>({}),
    storageKey = "famsun.session." + user.id;
  const select = useCallback(
    (next: string | null) => {
      activeSid.current = next;
      setSid(next);
      setRuns([]);
      setHistoryError("");
      setHistoryLoaded(!next);
      setDraft(drafts.current[next || ""] || "");
      setNav(false);
      setFiles(false);
      setPreview(null);
      setError("");
      nearEnd.current = true;
      try {
        if (next) sessionStorage.setItem(storageKey, next);
        else sessionStorage.removeItem(storageKey);
      } catch {
        /* Storage disabled. */
      }
    },
    [storageKey],
  );
  useEffect(() => {
    const c = new AbortController();
    request<Session[]>("/api/sessions", { signal: c.signal })
      .then((rows) => {
        setSessions(rows);
        setSessionsLoaded(true);
        let saved: string | null = null;
        try {
          saved = sessionStorage.getItem(storageKey);
        } catch {
          /* Storage disabled. */
        }
        select(rows.find((s) => s.id === saved)?.id || rows[0]?.id || null);
      })
      .catch((e) => {
        if (!c.signal.aborted) setError(messageOf(e));
      });
    return () => c.abort();
  }, [storageKey, select, attempt]);
  useEffect(() => {
    if (!sid) return;
    const c = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const sync = async () => {
      try {
        const rows = await request<Run[]>(scope(sid) + "/runs", {
          signal: c.signal,
        });
        if (c.signal.aborted) return;
        setRuns(rows);
        setHistoryError("");
        setHistoryLoaded(true);
        timer = setTimeout(
          sync,
          rows.some((r) => running(r.status)) ? 1100 : 7500,
        );
      } catch (e) {
        if (!c.signal.aborted) {
          setHistoryError(messageOf(e));
          timer = setTimeout(sync, 3200);
        }
      }
    };
    sync();
    return () => {
      c.abort();
      clearTimeout(timer);
    };
  }, [sid, reloading]);
  const runSignature = runs.map((r) => r.id + ":" + r.status).join(",");
  useEffect(() => {
    const viewport = scroll.current;
    const conversation = content.current;
    if (!viewport || !conversation) return;
    const follow = () => {
      if (nearEnd.current) viewport.scrollTop = viewport.scrollHeight;
    };
    const observer = new ResizeObserver(follow);
    observer.observe(conversation);
    follow();
    return () => observer.disconnect();
  }, [sid]);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setFiles(false);
        setNav(false);
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);
  function edit(value: string) {
    setDraft(value);
    drafts.current[sid || ""] = value;
  }
  const createSession = async (title?: string) => {
    const s = await post<Session>("/api/sessions", title ? { title } : {});
    setSessions((old) => [s, ...old]);
    select(s.id);
    return s;
  };
  const newSession = async () => {
    if (submitLock.current || !sessionsLoaded) return;
    setNav(false);
    if (!sid || (historyLoaded && !runs.length && !draft.trim())) {
      input.current?.focus();
      return;
    }
    select(null);
    requestAnimationFrame(() => input.current?.focus());
  };
  const submit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    const text = draft.trim();
    if (
      !text ||
      submitLock.current ||
      !sessionsLoaded ||
      (sid && !historyLoaded) ||
      runs.some((r) => running(r.status))
    )
      return;
    submitLock.current = true;
    setBusy(true);
    setError("");
    let target = sid;
    try {
      if (!target) target = (await createSession(sessionTitle(text))).id;
      let pending = pendingSends.current.get(target);
      if (!pending || pending.prompt !== text) {
        const key = Array.from(
          crypto.getRandomValues(new Uint8Array(16)),
          (b) => b.toString(16).padStart(2, "0"),
        ).join("");
        pending = { prompt: text, key };
        pendingSends.current.set(target, pending);
      }
      const run = await request<Run>(scope(target) + "/runs", {
        method: "POST",
        headers: { "Idempotency-Key": pending.key },
        body: JSON.stringify({ prompt: text }),
      });
      pendingSends.current.delete(target);
      drafts.current[target] = "";
      delete drafts.current[""];
      if (target === activeSid.current) {
        setDraft("");
        setRuns((old) => [...old.filter((r) => r.id !== run.id), run]);
        setReloading((i) => i + 1);
        nearEnd.current = true;
      }
      setSessions((old) => {
        const s = old.find((s) => s.id === target);
        return s ? [s, ...old.filter((s) => s.id !== target)] : old;
      });
    } catch (e) {
      if (target === activeSid.current) {
        setDraft(text);
        drafts.current[target || ""] = text;
        setError(messageOf(e));
        setReloading((i) => i + 1);
      }
    } finally {
      submitLock.current = false;
      setBusy(false);
    }
  };
  const cancel = async (run: Run) => {
    if (submitLock.current) return;
    submitLock.current = true;
    setBusy(true);
    setError("");
    try {
      await post(
        scope(run.session_id) +
          "/runs/" +
          encodeURIComponent(run.id) +
          "/cancel",
      );
      if (run.session_id === activeSid.current) setReloading((i) => i + 1);
    } catch (e) {
      if (run.session_id === activeSid.current) setError(messageOf(e));
    } finally {
      submitLock.current = false;
      setBusy(false);
    }
  };
  const current = sessions.find((s) => s.id === sid),
    active = runs.find((r) => running(r.status));
  const logout = async () => {
    if (submitLock.current) return;
    setBusy(true);
    try {
      await post("/api/auth/logout");
      onLogout();
    } catch (e) {
      setBusy(false);
      setError(messageOf(e));
    }
  };
  const empty = !runs.length && (!sid || historyLoaded);
  const blocked = busy || !sessionsLoaded || Boolean(sid && !historyLoaded);
  return (
    <div className={"studio" + (files ? " with-files" : "")}>
      {nav && (
        <button
          className="nav-shade"
          aria-label="关闭会话菜单"
          onClick={() => setNav(false)}
        />
      )}
      <aside
        className={"sidebar" + (nav ? " shown" : "")}
        aria-label="会话导航"
      >
        <div className="brand-row">
          <Brand />
          <button
            className="icon-button mobile-close"
            onClick={() => setNav(false)}
            aria-label="关闭导航"
          >
            <X size={18} />
          </button>
        </div>
        <button
          className="primary new-session"
          onClick={newSession}
          disabled={busy || !sessionsLoaded}
        >
          <Plus size={18} />
          新建会话
        </button>
        <div className="nav-heading">会话记录</div>
        <nav>
          {sessions.map((s) => (
            <button
              className={"session-item" + (s.id === sid ? " selected" : "")}
              key={s.id}
              title={sessionTitle(s.title)}
              aria-current={s.id === sid ? "page" : undefined}
              disabled={busy}
              onClick={() => {
                if (s.id !== sid) select(s.id);
                else setNav(false);
              }}
            >
              <MessageSquare size={15} />
              <span>{sessionTitle(s.title)}</span>
            </button>
          ))}
        </nav>
        {!sessions.length && (
          <div className="nav-empty">
            <MessageSquare size={20} />
            <span>{sessionsLoaded ? "暂无会话记录" : "正在读取会话…"}</span>
          </div>
        )}
        <div className="account">
          <div className="account-avatar">
            <UserRound size={16} />
          </div>
          <b>{user.username}</b>
          <button
            className="icon-button"
            title="退出登录"
            aria-label="退出登录"
            onClick={logout}
            disabled={busy}
          >
            <LogOut size={16} />
          </button>
        </div>
      </aside>
      <div className="desk">
        <header className="topbar">
          <button
            className="icon-button hamburger"
            aria-label="打开会话菜单"
            onClick={() => setNav(true)}
          >
            <Menu size={19} />
          </button>
          <AgentMark />
          <strong>FAMSUN Agent</strong>
          {current && (
            <>
              <ChevronRight size={14} className="crumb-arrow" />
              <span className="crumb" title={sessionTitle(current.title)}>
                {sessionTitle(current.title)}
              </span>
            </>
          )}
          <button
            className={"file-toggle" + (files ? " selected" : "")}
            aria-pressed={files}
            onClick={() => {
              setPreview(null);
              setFiles((v) => !v);
            }}
          >
            <FolderClosed size={16} />
            文件
          </button>
        </header>
        <main className={"desk-main" + (empty ? " empty" : "")}>
          <div
            className="conversation-scroll"
            ref={scroll}
            onScroll={() => {
              if (scroll.current)
                nearEnd.current =
                  scroll.current.scrollHeight -
                    scroll.current.scrollTop -
                    scroll.current.clientHeight <
                  100;
            }}
          >
            <div ref={content} className="conversation">
              <div aria-live="polite" className="sr-only">
                {active
                  ? "任务执行中"
                  : runs.length
                    ? "任务" + statusText[runs.at(-1)!.status]
                    : ""}
              </div>
              {historyError && (
                <div className="reconnect" role="status">
                  {historyError} 正在重连。
                  <button
                    className="text-button"
                    onClick={() => setReloading((i) => i + 1)}
                  >
                    重试
                  </button>
                </div>
              )}
              {runs.length ? (
                runs.map((r) => (
                  <RunEntry
                    key={r.id}
                    run={r}
                    openFiles={(path) => {
                      setPreview({ path });
                      setFiles(true);
                    }}
                  />
                ))
              ) : sid && !historyLoaded ? (
                <div className="loading-note">正在读取会话…</div>
              ) : (
                <Welcome />
              )}
            </div>
          </div>
          <div className="compose-area">
            <div className="compose-wrap">
              {error && (
                <div className="error" role="alert">
                  {error}
                  {!sessionsLoaded && (
                    <button
                      className="text-button"
                      onClick={() => {
                        setError("");
                        setAttempt((i) => i + 1);
                      }}
                    >
                      重试
                    </button>
                  )}
                </div>
              )}
              <form className="composer" onSubmit={submit}>
                <textarea
                  rows={2}
                  ref={input}
                  aria-label="任务描述"
                  maxLength={16000}
                  value={draft}
                  onChange={(e) => edit(e.target.value)}
                  placeholder="描述你想完成的事…"
                  onKeyDown={(e) => {
                    if (
                      e.key === "Enter" &&
                      (e.metaKey || e.ctrlKey) &&
                      !e.nativeEvent.isComposing
                    ) {
                      e.preventDefault();
                      submit();
                    }
                  }}
                />
                <div className="composer-actions">
                  <span className="shortcut">Ctrl / ⌘ + Enter</span>
                  {active ? (
                    <button
                      type="button"
                      className="send stop"
                      title="停止执行"
                      aria-label="停止执行"
                      disabled={busy}
                      onClick={() => cancel(active)}
                    >
                      <Square size={15} />
                    </button>
                  ) : (
                    <button
                      className="send"
                      title="发送任务"
                      aria-label="发送任务"
                      disabled={blocked || !draft.trim()}
                    >
                      <ArrowUp size={19} />
                    </button>
                  )}
                </div>
              </form>
              {empty && (
                <Starters
                  choose={(value) => {
                    edit(value);
                    input.current?.focus();
                  }}
                />
              )}
            </div>
          </div>
        </main>
      </div>
      {files && (
        <Artifacts
          key={sid || "draft"}
          sid={sid}
          requested={preview}
          version={runSignature}
          onClose={() => setFiles(false)}
        />
      )}
    </div>
  );
}
export default function App() {
  const [user, setUser] = useState<Identity | null>(null),
    [ready, setReady] = useState(false),
    [error, setError] = useState(""),
    [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const c = new AbortController();
    const leave = () => setUser(null);
    window.addEventListener("famsun:signed-out", leave);
    request<Identity>("/api/auth/me", { signal: c.signal })
      .then((u) => {
        setUser(u);
        setReady(true);
      })
      .catch((e) => {
        if (c.signal.aborted) return;
        if (e instanceof HttpError && e.status === 401) setReady(true);
        else setError(messageOf(e));
      });
    return () => {
      c.abort();
      window.removeEventListener("famsun:signed-out", leave);
    };
  }, [attempt]);
  if (!ready)
    return (
      <main className="initial">
        <Brand />
        {error ? (
          <>
            <p role="alert">{error}</p>
            <button
              className="primary"
              onClick={() => {
                setError("");
                setAttempt((n) => n + 1);
              }}
            >
              重新连接
            </button>
          </>
        ) : (
          <p>正在连接工作台…</p>
        )}
      </main>
    );
  return user ? (
    <Workspace key={user.id} user={user} onLogout={() => setUser(null)} />
  ) : (
    <Auth onLogin={setUser} />
  );
}
