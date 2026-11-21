import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { messageOf, request } from "./api";

type SheetPage = {
  sheets: string[];
  sheet: number;
  offset: number;
  totalRows: number;
  totalColumns: number;
  rows: string[][];
  pageSize: number;
  columnLimit: number;
  textClipped: boolean;
};
function columnName(index: number): string {
  let letters = "";
  for (let i = index + 1; i > 0; i = Math.floor((i - 1) / 26))
    letters = String.fromCharCode(65 + ((i - 1) % 26)) + letters;
  return letters;
}
export default function SheetPreview({ url }: { url: string }) {
  const [sheet, setSheet] = useState(0),
    [offset, setOffset] = useState(0);
  const [data, setData] = useState<SheetPage | null>(null),
    [error, setError] = useState(""),
    [attempt, setAttempt] = useState(0);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const ctrl = new AbortController();
    setError("");
    setLoading(true);
    request<SheetPage>(
      url + "&view=sheet&sheet=" + sheet + "&offset=" + offset,
      { signal: ctrl.signal },
    )
      .then((value) => {
        if (!ctrl.signal.aborted) setData(value);
      })
      .catch((e) => {
        if (!ctrl.signal.aborted) setError(messageOf(e));
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setLoading(false);
      });
    return () => ctrl.abort();
  }, [url, sheet, offset, attempt]);
  return (
    <section
      className="document-preview sheet-preview"
      aria-label="Excel 工作表"
    >
      {data && (
        <div className="sheet-tools">
          <label>
            工作表
            <select
              aria-label="选择工作表"
              value={sheet}
              onChange={(e) => {
                setSheet(Number(e.target.value));
                setOffset(0);
              }}
            >
              {data.sheets.map((name, i) => (
                <option key={name} value={i}>
                  {name}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}
      {loading ? (
        <p role="status">正在读取工作表…</p>
      ) : error ? (
        <div className="file-notice" role="alert">
          {error}
          <button
            className="text-button"
            onClick={() => setAttempt((i) => i + 1)}
          >
            重新读取
          </button>
        </div>
      ) : (
        data && (
          <>
            <div
              className="sheet-scroll"
              tabIndex={0}
              aria-label="可滚动的工作表"
            >
              <table aria-label={data.sheets[sheet]}>
                <thead>
                  <tr>
                    <th scope="col" aria-label="行号">
                      #
                    </th>
                    {Array.from(
                      { length: Math.min(data.totalColumns, data.columnLimit) },
                      (_, i) => (
                        <th key={i} scope="col">
                          {columnName(i)}
                        </th>
                      ),
                    )}
                  </tr>
                </thead>
                <tbody>
                  {data.rows.map((row, i) => (
                    <tr key={i}>
                      <th scope="row">{data.offset + i + 1}</th>
                      {row.map((cell, j) => (
                        <td key={j}>{cell}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="document-toolbar">
              <button
                className="icon-button"
                aria-label="上一组行"
                disabled={!offset}
                onClick={() => setOffset(Math.max(0, offset - data.pageSize))}
              >
                <ChevronLeft size={16} />
              </button>
              <span>
                {data.totalRows
                  ? offset + 1 + "–" + (offset + data.rows.length)
                  : "0"}{" "}
                / {data.totalRows} 行
              </span>
              <button
                className="icon-button"
                aria-label="下一组行"
                disabled={offset + data.pageSize >= data.totalRows}
                onClick={() => setOffset(offset + data.pageSize)}
              >
                <ChevronRight size={16} />
              </button>
            </div>
            {(data.totalColumns > data.columnLimit || data.textClipped) && (
              <small className="preview-footnote">
                {data.totalColumns > data.columnLimit &&
                  "网格显示前 " + data.columnLimit + " 列。"}
                {data.textClipped && "部分长文本已截断。"}
                可切换版式或下载原文件。
              </small>
            )}
          </>
        )
      )}
    </section>
  );
}
