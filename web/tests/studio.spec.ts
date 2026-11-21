import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import { randomUUID } from "node:crypto";
const pass = "Studio-Test-Password-32";
async function signUp(page: Page) {
  const username = "qa-" + randomUUID().slice(0, 12);
  await page.goto("/");
  await page.getByRole("button", { name: "还没有账户？创建账户" }).click();
  await page.getByLabel("用户名", { exact: true }).fill(username);
  await page.getByLabel("密码", { exact: true }).fill(pass);
  await page.getByRole("button", { name: "创建账户", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "有什么可以帮你？" }),
  ).toBeVisible();
  return username;
}
async function send(page: Page, prompt: string) {
  await page.getByRole("textbox", { name: "任务描述" }).fill(prompt);
  await page.getByRole("button", { name: "发送任务", exact: true }).click();
}
async function startStreamedReport(page: Page) {
  const accepted = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().endsWith("/runs"),
  );
  await send(page, "分步汇总渠道订单");
  const response = await accepted;
  expect(response.status()).toBe(202);
  return (await response.json()).id as string;
}
async function release(page: Page, id: string, phase: string) {
  expect(
    (await page.request.post("/fixture/" + id + "/" + phase)).status(),
  ).toBe(200);
}
async function expand(button: ReturnType<Page["getByRole"]>) {
  if ((await button.getAttribute("aria-expanded")) !== "true")
    await button.click();
}

test("stream public model text before tools and final answer, replay while active, and inspect nested tool results", async ({
  page,
}) => {
  await signUp(page);
  const streamConnected = page.waitForResponse(
    (response) =>
      response.url().includes("/stream?after=") &&
      response.headers()["content-type"]?.startsWith("text/event-stream"),
  );
  const id = await startStreamedReport(page);
  await streamConnected;
  await release(page, id, "delta");
  await expect(page.locator(".timeline-message")).toHaveText(
    "先核对订单明细，",
  );
  await expect(page.getByRole("button", { name: "停止执行" })).toBeVisible();
  await expect(page.getByText("再汇总每个渠道的销售额。")).toHaveCount(0);
  await release(page, id, "tool");
  const sentence = "先核对订单明细，再汇总每个渠道的销售额。";
  await expect(page.locator(".timeline-message")).toHaveText(sentence);
  await page.reload();
  await expect(page.getByText(sentence, { exact: true })).toBeVisible();
  await expand(page.locator(".trace-summary"));
  const delegation = page.getByRole("button", { name: /子智能体.*数据复核/ });
  await expand(delegation);
  const child = delegation
    .locator("xpath=ancestor::li[1]")
    .locator("ol li")
    .getByRole("button", { name: /运行命令/ });
  await expand(child);
  await expect(
    page.locator("pre[aria-label='输入参数 · command']"),
  ).toContainText("python verify_orders.py orders.csv");
  await expect(page.locator("pre[aria-label='返回结果']")).toHaveText(
    "3 orders, total=150",
  );
  await expect(page.getByText(/退出码/)).toBeVisible();
  await delegation.click();
  await expect(child).toBeHidden();
  await release(page, id, "finish");
  await expect(
    page.getByText("三个渠道销售合计 150 元。", { exact: true }),
  ).toHaveCount(1);
  await expect(page.getByRole("button", { name: "停止执行" })).toHaveCount(0);
  await page.reload();
  await expect(page.locator(".trace-summary")).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByText(sentence, { exact: true })).toHaveCount(0);
  await expect(page.locator(".timeline-message")).toHaveText("三个渠道销售合计 150 元。");
  await expand(page.locator(".trace-summary"));
  await expect(page.getByText(sentence, { exact: true })).toBeVisible();
  await expect(
    page.getByText("三个渠道销售合计 150 元。", { exact: true }),
  ).toHaveCount(1);
  await page.getByRole("button", { name: "文件", exact: true }).click();
  await page.getByRole("complementary", { name: "工作区文件" })
    .getByRole("button", { name: /sales-summary\.md/ }).click();
  await expect(
    page.getByText("合计 150 元。", { exact: false }).last(),
  ).toBeVisible();
});

test("a disconnected event stream resumes from a nonzero cursor over the events API", async ({
  page,
}) => {
  await signUp(page);
  let failedStream = 0;
  const resumed: number[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.endsWith("/events"))
      resumed.push(Number(url.searchParams.get("after")));
  });
  await page.route("**/stream?after=*", async (route) => {
    failedStream++;
    await route.abort("connectionreset");
  });
  const id = await startStreamedReport(page);
  await expect.poll(() => failedStream).toBeGreaterThan(0);
  await release(page, id, "delta");
  await expect(page.locator(".timeline-message")).toHaveText(
    "先核对订单明细，",
  );
  await release(page, id, "tool");
  await expect(page.locator(".timeline-message")).toHaveText(
    "先核对订单明细，再汇总每个渠道的销售额。",
  );
  expect(resumed.some((c) => c > 0)).toBeTruthy();
  await release(page, id, "finish");
  await expect(
    page.getByText("三个渠道销售合计 150 元。", { exact: true }),
  ).toBeVisible();
});

test("account, starters, history, three-level traces, safe artifact preview, interactive GLB, logout", async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await expect(page).toHaveTitle(/FAMSUN Agent/);
  await expect(page.getByRole("heading", { name: "登录工作台" })).toBeVisible();
  await page.screenshot({ path: info.outputPath("login.png") });
  const user = await signUp(page);
  await expect(page.getByRole("button", { name: "新建会话" })).toBeEnabled();
  await page.setViewportSize({ width: 1488, height: 1058 });
  await page.screenshot({ path: info.outputPath("workspace.png") });
  await page.getByRole("button", { name: "制定工作计划" }).click();
  await expect(page.getByRole("textbox", { name: "任务描述" })).toHaveValue(
    /工作计划/,
  );
  const legacyTitle =
    "原始长标题。后续内容以前被完整写入数据库，但不应出现在工作台标题。";
  const legacy = await page.request.post("/api/sessions", {
    data: { title: legacyTitle },
  });
  expect(legacy.ok()).toBe(true);
  const oldSession = await legacy.json();
  const oldRun = await page.request.post(
    "/api/sessions/" + oldSession.id + "/runs",
    {
      data: { prompt: "这是一个历史会话中的已完成任务" },
    },
  );
  expect(oldRun.status()).toBe(202);
  await page.reload();
  await expect(page.getByText("生成完成：")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "原始长标题", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "新建会话" }).click();
  const task =
    "生成独立网格。请将这段执行说明保留在对话内容中，而将首句用于会话标题。";
  await send(page, task);
  await expect(page.locator(".topbar .crumb")).toHaveText("生成独立网格");
  await expect(
    page.getByRole("button", { name: "生成独立网格", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("生成完成：")).toBeVisible();
  const toggle = page.getByRole("button", { name: /执行过程/ });
  if ((await toggle.getAttribute("aria-expanded")) !== "true")
    await toggle.click();
  await expect(page.getByText("执行建模工具")).toBeVisible();
  const sub = page.getByRole("button", { name: /建模子智能体/ });
  await sub.click();
  await expect(page.getByText("执行建模工具")).toBeHidden();
  await sub.click();
  await page.getByRole("button", { name: "文件", exact: true }).click();
  await page
    .getByRole("complementary", { name: "工作区文件" })
    .getByRole("button", { name: /assembly.glb/ })
    .click();
  await expect(page.getByText("2 个网格")).toBeVisible();
  await page.getByRole("button", { name: "放大预览" }).click();
  await expect(page.getByRole("button", { name: "缩小预览" })).toBeVisible();
  const slider = page.getByRole("slider", { name: "爆炸视图" });
  await slider.focus();
  await slider.press("End");
  await expect(page.getByText("100%", { exact: true })).toBeVisible();
  const canvas = page.locator(".model-stage canvas");
  const beforeFit = await canvas.screenshot();
  await page.getByRole("button", { name: "重置视角" }).click();
  expect(beforeFit.equals(await canvas.screenshot())).toBe(false);
  const download = page.waitForEvent("download");
  await page.getByRole("link", { name: "下载文件" }).click();
  expect((await download).suggestedFilename()).toBe("assembly.glb");
  await page.getByRole("button", { name: "全部文件" }).click();
  await page.getByRole("button", { name: /results.csv/ }).click();
  await expect(page.getByRole("cell", { name: "torque, Nm" })).toBeVisible();
  await page.getByRole("button", { name: "原文", exact: true }).click();
  await expect(page.locator(".text-preview")).toContainText(
    '"torque, Nm",10.2',
  );
  await page.getByRole("button", { name: "全部文件" }).click();
  await page.getByRole("button", { name: /notes.html/ }).click();
  await expect(page.frameLocator('iframe[title="HTML 静态页面"]').getByRole("heading", {name: "交付说明"})).toBeVisible();
  await page.getByRole("button", {name: "源码", exact:true}).click();
  await expect(page.locator(".text-preview")).toContainText("<script>");
  expect(
    await page.evaluate(() => Reflect.get(window, "untrusted")),
  ).toBeUndefined();
  const outgoing: string[] = [];
  page.on("request", (r) => {
    if (new URL(r.url()).hostname === "external.invalid")
      outgoing.push(r.url());
  });
  await page.getByRole("button", { name: "全部文件" }).click();
  await page.getByRole("button", { name: /summary.md/ }).click();
  const preview = page.getByRole("complementary", { name: "工作区文件" });
  await expect(
    preview.getByRole("heading", { level: 1, name: "周度摘要" }),
  ).toBeVisible();
  await expect(
    preview.getByRole("table").getByRole("cell", { name: "150" }),
  ).toBeVisible();
  await expect(
    preview.locator("img[src^='https://external.invalid']"),
  ).toHaveCount(0);
  await preview.getByRole("button", { name: "原文", exact: true }).click();
  await expect(preview.locator(".text-preview")).toContainText(
    "| 门店 | 150 |",
  );
  await expect(preview.locator(".text-preview")).toContainText("<script>");
  await preview.getByRole("button", { name: "正文", exact: true }).click();
  await expect(
    preview.getByRole("heading", { level: 1, name: "周度摘要" }),
  ).toBeVisible();
  expect(outgoing).toEqual([]);
  expect(
    await page.evaluate(() => Reflect.get(window, "untrusted")),
  ).toBeUndefined();
  await page.getByRole("button", { name: "关闭文件" }).click();
  await page.reload();
  await expect(page.getByText("生成完成：")).toBeVisible();
  await page.getByRole("button", { name: "新建会话" }).click();
  await expect(
    page.getByRole("heading", { name: "有什么可以帮你？" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "生成独立网格", exact: true }).click();
  await expect(page.getByText("生成完成：")).toBeVisible();
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(page.getByRole("heading", { name: "登录工作台" })).toBeVisible();
  await page.getByLabel("用户名", { exact: true }).fill(user);
  await page.getByLabel("密码", { exact: true }).fill(pass);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByText("生成完成：")).toBeVisible();
  expect(errors).toEqual([]);
});
test("cancel an active run and display execution failure", async ({ page }) => {
  await signUp(page);
  await send(page, "等待取消");
  const liveSummary = page.locator(".trace-summary");
  await expect(liveSummary).toContainText("执行建模工具");
  if ((await liveSummary.getAttribute("aria-expanded")) === "true")
    await liveSummary.click();
  await expect(liveSummary).toContainText("执行建模工具");
  await page.getByRole("button", { name: "停止执行" }).click();
  await expect(page.getByText("已停止本次执行。")).toBeVisible();
  await send(page, "测试失败");
  await expect(page.getByRole("alert")).toContainText("任务执行失败");
  await expect(page.getByRole("button", { name: "发送任务" })).toBeDisabled();
  await send(page, "检查失败的工具调用");
  await expect(
    page.getByText("改用 orders.csv 后，确认有四行订单。"),
  ).toBeVisible();
  await expand(page.locator(".run-entry").last().locator(".trace-summary"));
  const failed = page.getByRole("button", {
    name: /运行命令.*missing.csv.*失败/,
  });
  await expand(failed);
  const card = failed.locator("xpath=ancestor::li[1]");
  await expect(card.getByLabel("失败输出")).toHaveText(
    "missing.csv: not found",
  );
  await expect(card.getByText(/退出码/)).toContainText("2");
  await expect(
    page.getByRole("button", { name: /运行命令.*orders.csv.*已完成/ }),
  ).toBeVisible();
});
test("mobile drawer, file panel, keyboard composer, and recoverable read failure", async ({
  page,
}, info) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signUp(page);
  await page.screenshot({ path: info.outputPath("mobile.png") });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(
    390,
  );
  await page.getByRole("button", { name: "打开会话菜单" }).click();
  await expect(page.getByText("会话记录", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "关闭导航" }).click();
  await page.getByRole("button", { name: "文件", exact: true }).click();
  await expect(page.getByRole("heading", { name: "暂无文件" })).toBeVisible();
  await page.getByRole("button", { name: "关闭文件" }).click();
  const box = page.getByRole("textbox", { name: "任务描述" });
  await box.fill("移动端任务");
  await box.press("Control+Enter");
  await expect(page.getByText("生成完成：")).toBeVisible();
  let transient = true;
  await page.route("**/files", async (route) => {
    if (transient) {
      transient = false;
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "工作区暂不可用" }),
      });
    } else await route.continue();
  });
  await page.getByRole("button", { name: "文件", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("工作区暂不可用");
  await page.getByRole("button", { name: "刷新文件" }).click();
  await expect(
    page.getByRole("button", { name: /assembly.glb/ }),
  ).toBeVisible();
});

test("isolate sessions between accounts and reject external model resources", async ({
  page,
  browser,
}) => {
  await signUp(page);
  await send(page, "验证会话文件隔离");
  await expect(page.getByText("生成完成：")).toBeVisible();
  await page.getByRole("button", { name: "文件", exact: true }).click();
  const outgoing: string[] = [];
  page.on("request", (req) => {
    if (req.url().includes("external.invalid")) outgoing.push(req.url());
  });
  await page.getByRole("button", { name: /external.glb/ }).click();
  await expect(page.getByRole("alert")).toContainText("所有资源嵌入");
  const downloadPath = await page
    .getByRole("link", { name: "下载文件" })
    .getAttribute("href");
  expect(outgoing).toEqual([]);
  const other = await browser.newContext({ baseURL: "http://127.0.0.1:8176" });
  try {
    const second = await other.newPage();
    await signUp(second);
    await expect(second.getByText("暂无会话记录")).toBeVisible();
    expect((await other.request.get(downloadPath!)).status()).toBe(404);
    await expect(second.getByText("验证会话文件隔离")).toHaveCount(0);
  } finally {
    await other.close();
  }
});
if (process.env.FAMSUN_E2E_MODEL) {
  test("render the archived engineering acceptance GLB and measure explosion", async ({
    page,
  }, info) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    const external: string[] = [];
    page.on("request", (req) => {
      if (
        /^https?:/.test(req.url()) &&
        !req.url().startsWith("http://127.0.0.1:8176/")
      )
        external.push(req.url());
    });
    await signUp(page);
    await send(page, "预览已归档的制粒机验收模型");
    await page.getByRole("button", { name: "pellet.glb", exact: true }).click();
    const viewer = page.locator(".model-stage");
    const parts = page.getByText(/^\d+ 个网格$/);
    await expect(parts).toBeVisible();
    expect(parseInt((await parts.textContent())!, 10)).toBeGreaterThanOrEqual(
      6,
    );
    const trace = page.getByRole("button", { name: /^执行过程/ });
    if ((await trace.getAttribute("aria-expanded")) !== "true")
      await trace.click();
    const engineering = page
      .locator(".node-toggle")
      .filter({ hasText: /engineering/ })
      .first();
    if (
      (await engineering.count()) &&
      (await engineering.getAttribute("aria-expanded")) === "false"
    )
      await engineering.click();
    await page.screenshot({ path: info.outputPath("archived-session.png") });
    await page.getByRole("button", { name: "放大预览" }).click();
    const before = await viewer.screenshot({
      path: info.outputPath("pellet-assembled.png"),
    });
    await page.getByRole("slider", { name: "爆炸视图" }).press("End");
    await expect(page.getByText("100%", { exact: true })).toBeVisible();
    const after = await viewer.screenshot({
      path: info.outputPath("pellet-exploded.png"),
    });
    expect(before.equals(after)).toBe(false);
    await page.screenshot({ path: info.outputPath("model-preview.png") });
    expect(errors).toEqual([]);
    expect(external).toEqual([]);
  });
}
