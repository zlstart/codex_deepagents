import { expect, test } from "@playwright/test";
import type { Trace } from "../src/api";

test("replay preserves file body, typed error and truncation, and the authoritative final message", async ({
  page,
}) => {
  const stamp = "2026-11-21T00:00:00Z";
  const session = {
    id: "session-a",
    title: "读取公开执行记录",
    created_at: stamp,
    updated_at: stamp,
  };
  const run = {
    id: "run-a",
    session_id: session.id,
    prompt: "检查文件",
    status: "completed",
    answer: "检查已结束，结果保存在工作区。",
    error: null,
    created_at: stamp,
    updated_at: stamp,
  };
  const verbatim = '{"record":9007199254740993,  "note": "原始排版"}\n';
  const records: Trace[] = [];
  function add(
    id: string,
    kind: string,
    label: string,
    status: string,
    payload: Trace["payload"],
  ) {
    records.push({
      id,
      kind,
      label,
      status,
      payload,
      parent_id: null,
      seq: records.length + 1,
      created_at: stamp,
    });
  }
  add("write", "tool", "write_file", "running", {
    type: "tool",
    arguments: { file_path: "/workspace/log.json", content: verbatim },
    arguments_truncated: true,
  });
  // Recovery may close an interrupted item without repeating its start payload.
  add("write", "tool", "write_file", "cancelled", null);
  add("parse", "tool", "execute", "failed", {
    type: "tool",
    arguments: { command: "python inspect.py" },
    result: { type: "ValueError", line: 7 },
    exit_code: 1,
    result_truncated: true,
    error: "第 7 行格式无效",
  });
  add("answer", "model", "模型响应", "running", {
    type: "model_text",
    delta: "检查",
    truncated: true,
  });
  add("answer", "model", "模型响应", "completed", {
    type: "model_end",
    has_tool_calls: false,
  });
  await page.route("**/api/auth/me", (r) =>
    r.fulfill({ json: { id: "scope", username: "qa" } }),
  );
  await page.route("**/api/sessions", (r) => r.fulfill({ json: [session] }));
  await page.route("**/runs", (r) => r.fulfill({ json: [run] }));
  await page.route("**/events?after=*", (r) =>
    r.fulfill({
      json: {
        events: records,
        cursor: records.length,
        status: "completed",
        has_more: false,
      },
    }),
  );
  await page.goto("/");
  await page.locator(".trace-summary").click();
  await page.getByRole("button", { name: /写入文件.*已停止/ }).click();
  await expect(page.getByLabel("文件内容 · content")).toHaveText(verbatim, {
    useInnerText: false,
  });
  expect(await page.getByLabel("文件内容 · content").textContent()).toBe(
    verbatim,
  );
  await expect(page.getByLabel("输入参数", { exact: true })).toContainText(
    "/workspace/log.json",
  );
  const write = page
    .locator("li.activity-item")
    .filter({ has: page.getByRole("button", { name: /写入文件/ }) });
  await expect(write.getByText("已截断").first()).toBeVisible();
  await expect(write.getByLabel("返回结果")).toHaveCount(0);
  await page.getByRole("button", { name: /运行命令.*失败/ }).click();
  await expect(page.getByLabel("失败输出")).toContainText('"line": 7');
  await expect(page.getByLabel("错误信息")).toHaveText("第 7 行格式无效");
  await expect(page.locator(".trace-exit")).toHaveText("退出码 1");
  await expect(page.locator(".trace-failed-count")).toHaveText("1 项失败");
  await expect(page.locator(".timeline-message")).toHaveCount(1);
  await expect(page.locator(".timeline-message")).toHaveText(run.answer);
});
