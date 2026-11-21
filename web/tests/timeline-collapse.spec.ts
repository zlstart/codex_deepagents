import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import type { Run, Trace } from "../src/api";

const stamp = "2026-11-21T12:00:00Z";
function run(id: string, status: Run["status"], answer: string | null): Run {
  return {
    id,
    session_id: "review",
    prompt: id,
    status,
    answer,
    error: status === "failed" ? "已达到运行预算" : null,
    created_at: stamp,
    updated_at: stamp,
  };
}
function event(
  id: string,
  kind: string,
  label: string,
  status: string,
  payload: Trace["payload"],
  parent_id: string | null = null,
): Omit<Trace, "seq"> {
  return { id, kind, label, status, payload, parent_id, created_at: stamp };
}
const delta = (id: string, text: string, parent: string | null = null) =>
  event(
    id,
    "model",
    "模型响应",
    "running",
    { type: "model_text", delta: text },
    parent,
  );
const modelEnd = (id: string, tools: boolean, parent: string | null = null) =>
  event(
    id,
    "model",
    "模型响应",
    "completed",
    { type: "model_end", has_tool_calls: tools },
    parent,
  );
type Snapshot = { run: Run; rows: Omit<Trace, "seq">[] };
async function history(page: Page, snapshots: Snapshot[]) {
  const session = {
    id: "review",
    title: "工作记录",
    created_at: stamp,
    updated_at: stamp,
  };
  await page.route("**/api/auth/me", (r) =>
    r.fulfill({ json: { id: "owner", username: "qa" } }),
  );
  await page.route("**/api/sessions", (r) => r.fulfill({ json: [session] }));
  await page.route("**/runs", (r) =>
    r.fulfill({ json: snapshots.map((s) => s.run) }),
  );
  await page.route("**/stream?after=*", (r) => r.abort("connectionreset"));
  await page.route("**/events?after=*", (r) => {
    const url = new URL(r.request().url());
    const id = decodeURIComponent(url.pathname.split("/").at(-2)!);
    const data = snapshots.find((s) => s.run.id === id)!;
    const after = Number(url.searchParams.get("after"));
    const rows = data.rows
      .map((e, i) => ({ ...e, seq: i + 1 }))
      .filter((e) => e.seq > after);
    return r.fulfill({
      json: {
        events: rows,
        cursor: rows.at(-1)?.seq ?? after,
        status: data.run.status,
        has_more: false,
      },
    });
  });
  await page.goto("/");
}
test("collapsed history shows only the authoritative answer, including legacy and budget runs", async ({
  page,
}) => {
  const snapshots: (Snapshot & { expandedText: string[] })[] = [
    {
      expandedText: ["我先检查数据。", "最终交付了报告。"],
      run: run("自然结束", "completed", "最终交付了报告。"),
      rows: [
        delta("m1", "我先检查数据。"),
        modelEnd("m1", true),
        event("t", "tool", "read_file", "completed", {
          type: "tool",
          arguments: { file_path: "input.csv" },
          result: "ok",
        }),
        delta("m2", "最终交付了报"),
        modelEnd("m2", false),
      ],
    },
    {
      expandedText: ["已完成部分工作", "已完成部分工作，已达到预算。"],
      run: run("预算结束", "failed", "已完成部分工作，已达到预算。"),
      rows: [delta("b", "已完成部分工作"), modelEnd("b", true)],
    },
    {
      expandedText: ["草稿还在生成。"],
      run: run("手动取消", "cancelled", null),
      rows: [
        delta("c", "草稿还在生成。"),
        event("c", "model", "模型响应", "cancelled", null),
      ],
    },
    {
      expandedText: ["还在读取资料。"],
      run: run("故障", "failed", null),
      rows: [
        delta("f", "还在读取资料。"),
        event("f", "model", "模型响应", "failed", null),
      ],
    },
    {
      run: run("旧会话", "completed", "旧版保存的答复"),
      rows: [],
      expandedText: ["旧版保存的答复"],
    },
    {
      run: run("子任务终止", "failed", "部分模型已完成。"),
      rows: [
        event("sub", "subagent", "工程建模", "failed", null),
        delta("nested", "已生成一个部件。", "sub"),
        modelEnd("nested", false, "sub"),
      ],
      expandedText: ["已生成一个部件。", "部分模型已完成。"],
    },
  ];
  await history(page, snapshots);
  const entries = page.locator(".run-entry");
  await expect(entries).toHaveCount(snapshots.length);
  for (const [i, data] of snapshots.entries()) {
    const entry = entries.nth(i),
      toggle = entry.locator(".trace-summary");
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(toggle.locator(".trace-count, .trace-failed-count")).toHaveCount(0);
    await expect(entry.locator(".timeline-message")).toHaveCount(
      data.run.answer ? 1 : 0,
    );
    if (data.run.answer)
      await expect(entry.locator(".timeline-message")).toHaveText(
        data.run.answer,
      );
    await toggle.click();
    await expect(entry.locator(".timeline-message")).toHaveText(
      data.expandedText,
    );
    await toggle.click();
    await expect(entry.locator(".activity-list")).toHaveCount(0);
  }
});

test("manual collapse survives reload and a terminal SSE event arriving before the answer record", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "还没有账户？创建账户" }).click();
  await page
    .getByLabel("用户名", { exact: true })
    .fill("ui-" + crypto.randomUUID().slice(0, 12));
  await page.getByLabel("密码", { exact: true }).fill(crypto.randomUUID());
  await page.getByRole("button", { name: "创建账户", exact: true }).click();
  const submitted = page.waitForResponse(
    (r) => r.request().method() === "POST" && r.url().endsWith("/runs"),
  );
  await page.getByLabel("任务描述").fill("分步汇总渠道订单");
  await page.getByRole("button", { name: "发送任务" }).click();
  const { id } = await (await submitted).json();
  const phase = async (k: string) =>
    expect(
      (await page.request.post("/fixture/" + id + "/" + k)).ok(),
    ).toBeTruthy();
  const summary = page.locator(".trace-summary");
  await phase("delta");
  await expect(page.locator(".timeline-message")).toHaveText(
    "先核对订单明细，",
  );
  await summary.click();
  await phase("tool");
  await expect(page.locator(".timeline-message")).toHaveCount(0);
  await page.reload();
  await expect(summary).toHaveAttribute("aria-expanded", "false");
  await expect(page.locator(".timeline-message")).toHaveCount(0);
  let hold = true;
  await page.route("**/runs", async (route) => {
    const response = await route.fetch();
    const rows = await response.json();
    return route.fulfill({
      json: hold
        ? rows.map((row: Run) => ({ ...row, status: "running", answer: null }))
        : rows,
    });
  });
  await phase("finish");
  await expect(summary).toContainText("已完成");
  await expect(page.locator(".timeline-message")).toHaveCount(0);
  await expect(summary).toHaveAttribute("aria-expanded", "false");
  hold = false;
  const final = "三个渠道销售合计 150 元。";
  await expect(page.locator(".timeline-message")).toHaveText(final);
  await summary.click();
  await expect(page.locator(".timeline-message")).toHaveText([
    "先核对订单明细，再汇总每个渠道的销售额。",
    final,
  ]);
  await page.reload();
  await expect(summary).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator(".timeline-message")).toHaveText([
    "先核对订单明细，再汇总每个渠道的销售额。",
    final,
  ]);
});

test("parallel summary follows the latest active child and keeps full commands and failure details on mobile", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const cmd = "python verify.py " + "--records " + "A".repeat(4000);
  const plan: Snapshot = {
    run: run("并行复核", "running", null),
    rows: [
      event("a", "subagent", "工程", "running", null),
      event("b", "subagent", "复核", "running", null),
      event(
        "shell",
        "tool",
        "execute",
        "running",
        { type: "tool", arguments: { command: cmd } },
        "a",
      ),
      event(
        "read",
        "tool",
        "read_file",
        "running",
        { type: "tool", arguments: { file_path: "records.csv" } },
        "b",
      ),
      event(
        "shell",
        "tool",
        "execute",
        "running",
        { type: "tool", arguments: { command: cmd } },
        "a",
      ),
    ],
  };
  await history(page, [plan]);
  const toggle = page.locator(".trace-summary");
  await expect(toggle).toContainText("2 项并行");
  await expect(toggle).toContainText("运行命令");
  const shell = page
    .locator(".node-toggle")
    .filter({ hasText: /运行命令.*执行中/ });
  await expect(shell.locator(".trace-caption code")).toHaveText(
    cmd.slice(0, 100) + "…",
  );
  await shell.click();
  expect(await page.getByLabel("输入参数 · command").textContent()).toBe(cmd);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(
    390,
  );
  plan.rows.push(
    event(
      "shell",
      "tool",
      "execute",
      "failed",
      {
        type: "tool",
        arguments: { command: cmd },
        result: "exit failure",
        error: "exit failure",
        result_truncated: true,
        exit_code: 2,
      },
      "a",
    ),
    event("a", "subagent", "工程", "failed", null),
  );
  await expect(toggle).toContainText("读取文件");
  await expect(toggle).not.toContainText("2 项并行");
  await expect(page.getByLabel("错误信息")).toHaveText("exit failure");
  await expect(
    page.locator(".trace-field").filter({ has: page.getByLabel("错误信息") }),
  ).toContainText("已截断");
  await expect(page.locator(".trace-exit")).toHaveText("退出码 2");
  await toggle.click();
  await toggle.click();
  await expect(page.getByLabel("错误信息")).toBeVisible();
});

test("a saved answer replaces its live prefix while terminal event replay catches up", async ({
  page,
}) => {
  const result = "已生成材料清单。";
  const plan: Snapshot = {
    run: run("完成竞态", "running", null),
    rows: [delta("last", "已生成")],
  };
  await history(page, [plan]);
  const messages = page.locator(".timeline-message");
  await expect(messages).toHaveText("已生成");
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/events?after=*", async (route) => {
    await gate;
    await route.fallback();
  });
  try {
    plan.run = run("完成竞态", "completed", result);
    await expect(page.locator(".trace-summary")).toContainText("已完成");
    await expect(messages).toHaveText([result]);
    await expect(page.locator(".timeline-message.is-streaming")).toHaveCount(0);
    plan.rows.push(delta("last", "材料清单。"), modelEnd("last", false));
    const terminalPage = page.waitForResponse((response) =>
      response.url().includes("/events?after="),
    );
    release();
    await terminalPage;
    await expect(messages).toHaveText([result]);
  } finally {
    release();
  }
});
