import { useEffect, useRef } from "react";
import { Trash2 } from "lucide-react";

export function DeleteSessionDialog({
  title,
  busy,
  error,
  onCancel,
  onConfirm,
}: {
  title: string;
  busy: boolean;
  error: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const element = dialog.current!;
    element.showModal();
    cancel.current?.focus();
    return () => element.close();
  }, []);
  return (
    <dialog
      ref={dialog}
      className="delete-dialog"
      aria-labelledby="delete-title"
      aria-describedby="delete-description"
      aria-busy={busy}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onCancel();
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape") event.stopPropagation();
      }}
    >
      <span className="delete-icon" aria-hidden>
        <Trash2 size={22} />
      </span>
      <h2 id="delete-title">删除会话？</h2>
      <p id="delete-description">
        「{title}」的对话记录和工作区文件将被永久删除，无法恢复。
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="delete-actions">
        <button
          className="secondary"
          ref={cancel}
          type="button"
          disabled={busy}
          onClick={onCancel}
        >
          取消
        </button>
        <button
          className="destructive"
          type="button"
          disabled={busy}
          onClick={onConfirm}
        >
          {busy ? "正在删除…" : "确认删除"}
        </button>
      </div>
    </dialog>
  );
}
