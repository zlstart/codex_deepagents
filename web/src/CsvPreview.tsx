import { useMemo, useState } from "react";
import Papa from "papaparse";
const ROWS = 100;
const COLUMNS = 20;
const SOURCE_CHARS = 64_000;

export default function CsvPreview({ buffer }: { buffer: ArrayBuffer }) {
  const [raw, setRaw] = useState(false);
  const text = useMemo(() => new TextDecoder().decode(buffer), [buffer]);
  const parsed = useMemo(
    () =>
      Papa.parse<string[]>(text, { preview: ROWS + 1, skipEmptyLines: true }),
    [text],
  );
  const rows = parsed.data.slice(0, ROWS);
  const truncated =
    parsed.data.length > ROWS || rows.some((row) => row.length > COLUMNS);
  return (
    <section className="csv-preview">
      <div className="preview-switch">
        <button aria-pressed={!raw} onClick={() => setRaw(false)}>
          表格
        </button>
        <button aria-pressed={raw} onClick={() => setRaw(true)}>
          原文
        </button>
      </div>
      {raw ? (
        <>
          <pre className="text-preview">{text.slice(0, SOURCE_CHARS)}</pre>
          {text.length > SOURCE_CHARS && (
            <small>仅显示前 64,000 个字符。</small>
          )}
        </>
      ) : (
        <>
          {parsed.errors.some((e) => e.type === "Quotes") && (
            <small>部分引号格式异常，可切换到原文核对。</small>
          )}
          {rows.length ? (
            <div
              className="csv-scroll"
              tabIndex={0}
              aria-label="CSV 表格，可横向滚动"
            >
              <table aria-label="CSV 数据">
                <tbody>
                  {rows.map((row, i) => (
                    <tr key={i}>
                      {row.slice(0, COLUMNS).map((cell, j) => (
                        <td key={j} title={cell}>
                          {cell}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p>文件没有数据行。</p>
          )}
          {truncated && <small>预览最多显示前 100 行和 20 列。</small>}
        </>
      )}
    </section>
  );
}
