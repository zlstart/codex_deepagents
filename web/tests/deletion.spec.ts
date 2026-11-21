import { randomUUID } from "node:crypto";
import { expect, test } from "@playwright/test";

test("deletion keeps the other session, supports cancellation and rejects an active run", async ({ page }) => {
  await page.goto("/");
  const registration = await page.request.post("/api/auth/register", {
    data: { username: "remove-" + randomUUID().slice(0, 10), password: "Deletion-fixture-12345" },
  });
  expect(registration.ok()).toBeTruthy();
  for (const title of ["保留的会话", "临时会话"]) {
    expect((await page.request.post("/api/sessions", { data: { title } })).ok()).toBeTruthy();
  }
  await page.reload();
  const remove = page.getByRole("button", { name: "删除会话：临时会话" });
  await page.getByRole("button", { name: "临时会话", exact: true }).click();
  await remove.click();
  const dialog = page.getByRole("dialog", { name: "删除会话？" });
  await expect(dialog.getByRole("button", { name: "取消" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(remove).toBeFocused();

  await page.getByRole("textbox", { name: "任务描述" }).fill("分步汇总渠道订单");
  await page.getByRole("button", { name: "发送任务", exact: true }).click();
  const stop = page.getByRole("button", { name: "停止执行" });
  await expect(stop).toBeVisible();
  await remove.click();
  const confirm = dialog.getByRole("button", { name: "确认删除" });
  await confirm.click();
  await expect(dialog.getByRole("alert")).toContainText("请先停止");
  await dialog.getByRole("button", { name: "取消" }).click();
  await stop.click();
  await expect(page.getByText("已停止本次执行。", { exact: true })).toBeVisible();

  await remove.click();
  const [deleted] = await Promise.all([
    page.waitForResponse((response) => response.request().method() === "DELETE"),
    confirm.click(),
  ]);
  expect(deleted.status()).toBe(204);
  await expect(page.getByText("会话已删除。", { exact: true })).toBeVisible();
  await expect(remove).toHaveCount(0);
  await expect(page.getByRole("button", { name: "保留的会话", exact: true })).toHaveAttribute("aria-current", "page");
  await page.reload();
  await expect(remove).toHaveCount(0);
  await expect(page.getByRole("button", { name: "保留的会话", exact: true })).toBeVisible();
});
