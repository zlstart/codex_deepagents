import { randomUUID } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";

async function files(page: Page) {
  await page.goto("/");
  const registered = await page.request.post("/api/auth/register", {
    data: {
      username: "docs-" + randomUUID().slice(0, 12),
      password: "Document-fixture-12345",
    },
  });
  expect(registered.ok()).toBeTruthy();
  await page.reload();
  await page
    .getByRole("textbox", { name: "任务描述" })
    .fill("常见文件预览验收");
  await page.getByRole("button", { name: "发送任务", exact: true }).click();
  await expect(
    page.getByText("验收文件已就绪。", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "文件", exact: true }).click();
  return page.getByRole("complementary", { name: "工作区文件" });
}

test("read two PDF pages and preview static HTML with local-only resources", async ({
  page,
}) => {
  const panel = await files(page);
  await panel.getByRole("button", { name: /report.pdf/ }).click();
  await expect(panel.getByRole("img", { name: "文档 第 1 页" })).toBeVisible();
  await panel.getByText("本页文字", { exact: true }).click();
  await expect(panel.locator(".text-preview")).toContainText("Weekly summary");
  await panel.getByRole("button", { name: "下一页" }).click();
  await expect(panel.locator(".text-preview")).toContainText(
    "Purchasing review",
  );
  await expect(panel.getByRole("img", { name: "文档 第 2 页" })).toBeVisible();
  await panel.getByRole("combobox", { name: "预览缩放" }).selectOption("1.5");
  await expect(panel.getByRole("img", { name: "文档 第 2 页" })).toBeVisible();
  await panel.getByRole("button", { name: "全部文件" }).click();
  const external: string[] = [];
  page.on("request", (r) => {
    if (r.url().startsWith("https://example.org/")) external.push(r.url());
  });
  await panel.getByRole("button", { name: /report.html/ }).click();
  const frame = page.frameLocator('iframe[title="HTML 静态页面"]');
  await expect(frame.getByRole("heading", { name: "渠道报告" })).toHaveCSS(
    "color",
    "rgb(138, 59, 17)",
  );
  await expect(frame.getByRole("cell", { name: "3500 元" })).toBeVisible();
  expect(await frame.locator("script").count()).toBe(0);
  expect(external).toEqual([]);
  await panel.getByRole("button", { name: "源码", exact: true }).click();
  await expect(panel.locator(".text-preview")).toContainText("<script>");
});

test("Office converter displays Word pages, slides, visible sheets and print layout", async ({
  page,
}) => {
  test.skip(
    process.env.FAMSUN_E2E_OFFICE !== "true",
    "Requires the built LibreOffice preview image",
  );
  test.setTimeout(150_000);
  const panel = await files(page);
  for (const [filename, section, text] of [
    ["report.docx", "文档", "Document two"],
    ["report.pptx", "幻灯片", "FAMSUN slide 2"],
  ]) {
    await panel.getByRole("button", { name: new RegExp(filename) }).click();
    await expect(
      panel.getByRole("img", { name: section + " 第 1 页" }),
    ).toBeVisible({ timeout: 40_000 });
    await panel.getByRole("button", { name: "下一页" }).click();
    await panel.getByText("本页文字", { exact: true }).click();
    // PDF glyph positioning can split a Latin word in mixed Chinese fonts.
    await expect.poll(async () => (await panel.locator(".text-preview").innerText()).replace(/\s+/gu, "")).toContain(text.replace(/\s+/gu, ""));
    await panel.getByRole("button", { name: "全部文件" }).click();
  }
  for (const filename of ["report.xlsx", "report.xls"]) {
    await panel
      .locator(".file-row")
      .filter({ has: page.getByText(filename, { exact: true }) })
      .click();
    await expect(
      panel.getByRole("cell", { name: "FAMSUN worksheet 1847" }),
    ).toBeVisible({ timeout: 40_000 });
    await panel.getByLabel("选择工作表").selectOption("1");
    await expect(
      panel.getByRole("cell", { name: "Second worksheet" }),
    ).toBeVisible({ timeout: 40_000 });
    await expect(
      panel.getByRole("option", { name: "Hidden data" }),
    ).toHaveCount(0);
    if (filename.endsWith(".xlsx")) {
      await panel.getByRole("button", { name: "打印版式" }).click();
      await expect(
        panel.getByRole("img", { name: "文档 第 1 页" }),
      ).toBeVisible({ timeout: 40_000 });
    }
    await panel.getByRole("button", { name: "全部文件" }).click();
  }
});
