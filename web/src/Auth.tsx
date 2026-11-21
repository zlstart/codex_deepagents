import { useEffect, useState } from "react";
import {
  ArrowRight,
  Eye,
  EyeOff,
  LoaderCircle,
  LockKeyhole,
  UserRound,
} from "lucide-react";
import { Brand } from "./Brand";
import { HttpError, messageOf, post, request } from "./api";
import type { Identity } from "./api";

export function Auth({ onLogin }: { onLogin: (u: Identity) => void }) {
  const [register, setRegister] = useState(false),
    [allowSignup, setAllowSignup] = useState(false);
  const [name, setName] = useState(""),
    [password, setPassword] = useState(""),
    [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [configAttempt, setConfigAttempt] = useState(0),
    [configError, setConfigError] = useState(false);
  useEffect(() => {
    const c = new AbortController();
    request<{ allow_signup: boolean }>("/api/auth/config", { signal: c.signal })
      .then((x) => {
        setAllowSignup(x.allow_signup);
        setConfigError(false);
      })
      .catch(() => {
        if (!c.signal.aborted) setConfigError(true);
      });
    return () => c.abort();
  }, [configAttempt]);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      onLogin(
        await post<Identity>("/api/auth/" + (register ? "register" : "login"), {
          username: name.trim(),
          password,
        }),
      );
    } catch (e) {
      setError(
        e instanceof HttpError && e.status === 401
          ? "用户名或密码有误，请重试。"
          : messageOf(e),
      );
    } finally {
      setBusy(false);
    }
  };
  return (
    <main className="auth-shell">
      <header className="auth-masthead">
        <Brand />
      </header>
      <div className="auth-scene" aria-hidden="true">
        <img src="/images/portal.webp" alt="" fetchPriority="high" />
      </div>
      <section className="auth-form-area" aria-labelledby="auth-title">
        <div className="auth-inner">
          <h1 id="auth-title">{register ? "创建工作台账户" : "登录工作台"}</h1>
          <p>
            {register
              ? "创建账户，保存你的会话与工作文件。"
              : "使用你的账户登录 FAMSUN Agent。"}
          </p>
          <form onSubmit={submit}>
            <label>
              用户名
              <div className="auth-field">
                <UserRound size={17} aria-hidden="true" />
                <input
                  placeholder="输入用户名"
                  autoComplete="username"
                  required
                  minLength={register ? 3 : 1}
                  maxLength={64}
                  pattern={
                    register ? "[A-Za-z0-9][A-Za-z0-9._\\-]{2,63}" : undefined
                  }
                  title={
                    register
                      ? "3–64 个字母、数字、点、下划线或连字符，以字母或数字开头"
                      : undefined
                  }
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </div>
            </label>
            <label>
              密码
              <div className="auth-field password-field">
                <LockKeyhole size={17} aria-hidden="true" />
                <input
                  placeholder={register ? "设置至少 12 位密码" : "输入密码"}
                  type={show ? "text" : "password"}
                  autoComplete={register ? "new-password" : "current-password"}
                  required
                  minLength={register ? 12 : 1}
                  maxLength={128}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
                <button
                  type="button"
                  className="icon-button"
                  aria-label={show ? "隐藏密码" : "显示密码"}
                  onClick={() => setShow(!show)}
                >
                  {show ? <EyeOff size={17} /> : <Eye size={17} />}
                </button>
              </div>
            </label>
            {register && (
              <small className="form-help">
                密码至少 12 位。用户名可使用英文字母和数字。
              </small>
            )}
            {error && (
              <div className="error" role="alert">
                {error}
              </div>
            )}
            <button className="primary auth-submit" disabled={busy}>
              {register ? "创建账户" : "登录"}
              {busy ? (
                <LoaderCircle className="spin" size={16} />
              ) : (
                <ArrowRight size={17} />
              )}
            </button>
          </form>
          {allowSignup && (
            <button
              className="auth-switch"
              onClick={() => {
                setRegister(!register);
                setError("");
                setPassword("");
              }}
            >
              {register ? "已有账户？返回登录" : "还没有账户？创建账户"}
            </button>
          )}
          {configError && (
            <button
              className="text-button"
              onClick={() => setConfigAttempt((i) => i + 1)}
            >
              重新获取注册设置
            </button>
          )}
        </div>
      </section>
    </main>
  );
}
