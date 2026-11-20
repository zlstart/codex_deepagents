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
    page.getByRole("heading", { name: "今天，想构建什么？" }),
  ).toBeVisible();
  return username;
}
async function send(page: Page, prompt: string) {
  await page.getByRole("textbox", { name: "任务描述" }).fill(prompt);
  await page.getByRole("button", { name: "发送任务", exact: true }).click();
}
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
  await page.getByRole("button", { name: "制粒机爆炸模型" }).click();
  await expect(page.getByRole("textbox", { name: "任务描述" })).toHaveValue(
    /导出 GLB/,
  );
  await send(page, "生成独立网格");
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
  await page.getByRole("button", { name: "重置视角" }).click();
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
  await expect(page.locator(".text-preview")).toContainText("<script>");
  expect(
    await page.evaluate(() => Reflect.get(window, "untrusted")),
  ).toBeUndefined();
  await page.getByRole("button", { name: "关闭文件" }).click();
  await page.reload();
  await expect(page.getByText("生成完成：")).toBeVisible();
  await page.getByRole("button", { name: "新建会话" }).click();
  await expect(
    page.getByRole("heading", { name: "今天，想构建什么？" }),
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
  await page.getByRole("button", { name: "停止执行" }).click();
  await expect(page.getByText("已停止本次执行。")).toBeVisible();
  await send(page, "测试失败");
  await expect(page.getByRole("alert")).toContainText("任务执行失败");
  await expect(page.getByRole("button", { name: "发送任务" })).toBeDisabled();
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
    await page.getByRole("button", { name: "放大预览" }).click();
    const before = await viewer.screenshot({
      path: info.outputPath("pellet-assembled.png"),
    });
    await page.getByRole("slider", { name: "爆炸视图" }).fill("75");
    await expect(page.getByText("75%", { exact: true })).toBeVisible();
    const after = await viewer.screenshot({
      path: info.outputPath("pellet-exploded.png"),
    });
    expect(before.equals(after)).toBe(false);
    await page.screenshot({ path: info.outputPath("model-preview.png") });
    expect(errors).toEqual([]);
    expect(external).toEqual([]);
  });
}
