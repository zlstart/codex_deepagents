/**
 * Run one ordinary, billable task in a configured deployment. This captures the
 * actual browser, provider events, and downloaded Markdown. No login state is saved.
 * From web/: node scripts/live-timeline.mjs http://127.0.0.1:8080 ../docs
 */
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { randomBytes, createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "@playwright/test";
import Papa from "papaparse";

const [baseURL, docs] = process.argv.slice(2);
if (!baseURL || !docs)
  throw new Error("Pass the deployment URL and docs directory.");
const evidencePath = resolve(docs, "testing");
const images = resolve(docs, "screenshots");
await Promise.all([
  mkdir(images, { recursive: true }),
  mkdir(evidencePath, { recursive: true }),
]);
const evidence = {
  started_utc: new Date().toISOString(),
  checks: {},
  errors: [],
};
const brief =
  "帮我做一份渠道销售小结。订单如下（渠道、销售额，元）：门店 1200，线上 800，门店 500，合作 1000。先用一句话告诉我你准备如何汇总，然后动手。将明细写入 orders.csv，把各渠道金额和总额写入 channel-summary.md。请让 reviewer 子智能体核对一次合计数，再用中文简短告诉我结果。";
const browser = await chromium.launch({
  executablePath: process.env.FAMSUN_E2E_CHROMIUM || undefined,
  args: [
    "--use-gl=angle",
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
  ],
});
const context = await browser.newContext({
  baseURL,
  viewport: { width: 1440, height: 960 },
  deviceScaleFactor: 1,
  locale: "zh-CN",
  reducedMotion: "reduce",
});
const page = await context.newPage();
page.setDefaultTimeout(20000);
const errors = [];
page.on("pageerror", (error) => errors.push(error.name));
const seenText = new Set();
let sid, rid;
let originalSSE = false;
page.on("response", (response) => {
  if (
    response.url().includes("/stream?") &&
    (response.headers()["content-type"] || "").includes("text/event-stream")
  )
    originalSSE = true;
});
async function screenshot(name) {
  await page.evaluate(() => document.fonts.ready);
  const png = await page.screenshot({ animations: "disabled", timeout: 12000 });
  const conversion = spawnSync(
    process.env.FAMSUN_E2E_PYTHON || "python",
    [
      "-c",
      "from PIL import Image; import io,sys; Image.open(io.BytesIO(sys.stdin.buffer.read())).save(sys.argv[1],format='WEBP',quality=92,method=6)",
      resolve(images, name + ".webp"),
    ],
    { input: png },
  );
  assert.equal(
    conversion.status,
    0,
    "Screenshot conversion requires Python with Pillow",
  );
}
async function api(path) {
  const response = await context.request.get(path, { timeout: 30000 });
  if (!response.ok())
    throw new Error(
      "HTTP " + response.status() + " " + new URL(response.url()).pathname,
    );
  return response;
}
let captured = false;
try {
  await page.goto("/");
  await page.getByRole("heading", { name: "登录工作台" }).waitFor();
  await page.locator(".auth-scene img").evaluate((img) => img.decode());
  await page.getByRole("button", { name: "还没有账户？创建账户" }).click();
  await page
    .getByLabel("用户名", { exact: true })
    .fill("studio-" + randomBytes(5).toString("hex"));
  await page
    .getByLabel("密码", { exact: true })
    .fill(randomBytes(24).toString("hex"));
  await page.getByRole("button", { name: "创建账户", exact: true }).click();
  await page.getByRole("heading", { name: "有什么可以帮你？" }).waitFor();
  await page.getByRole("textbox", { name: "任务描述" }).fill(brief);
  const accepted = page.waitForResponse(
    (r) => r.request().method() === "POST" && r.url().endsWith("/runs"),
  );
  await page.getByRole("button", { name: "发送任务", exact: true }).click();
  const resp = await accepted;
  assert.equal(resp.status(), 202);
  const submitted = await resp.json();
  sid = submitted.session_id;
  rid = submitted.id;
  Object.assign(evidence, { session_id: sid, run_id: rid, prompt: brief });
  console.log("Started real browser task", rid);
  const endpoint = "/api/sessions/" + sid + "/runs/" + rid;
  const summary = page.locator(".trace-summary");
  await summary.waitFor();
  if ((await summary.getAttribute("aria-expanded")) !== "true")
    await summary.click();
  const rows = [];
  let cursor = 0,
    status = "running",
    lastProgress = "";
  const deadline = Date.now() + 32 * 60_000;
  for (;;) {
    const block = await (
      await api(endpoint + "/events?after=" + cursor)
    ).json();
    const appended = block.events.filter((row) => row.seq > cursor);
    rows.push(...appended);
    cursor = block.cursor;
    status = block.status;
    if (appended.length) {
      const latest = appended.at(-1);
      const progress = latest.kind + "/" + latest.label + "/" + latest.status;
      if (progress !== lastProgress) {
        console.log("Activity", cursor, progress);
        lastProgress = progress;
      }
    }
    if (["running", "queued"].includes(status)) {
      for (const text of await page
        .locator(".timeline-message")
        .allInnerTexts()) {
        if (text.trim()) seenText.add(text.trim());
      }
      const modelRows = rows.filter(
        (e) => e.payload?.type === "model_text" && e.payload.delta,
      );
      if (
        !captured &&
        seenText.size &&
        modelRows.length &&
        rows.some(
          (e) =>
            e.kind === "tool" &&
            e.payload?.type === "tool" &&
            e.status === "completed" &&
            e.payload.result !== undefined,
        )
      ) {
        // Keep the visible human-facing explanation and its adjacent tool together.
        const tool = page
          .locator("li[data-kind='tool'] .node-toggle")
          .filter({ hasText: /写入文件|读取文件|运行命令/ })
          .first();
        if ((await tool.count()) && (await tool.isVisible())) {
          if ((await tool.getAttribute("aria-expanded")) === "false")
            await tool.click();
          await tool.scrollIntoViewIfNeeded();
          await screenshot("live-trace-running");
          evidence.live_capture_cursor = cursor;
          captured = true;
        }
      }
    }
    if (
      ["failed", "completed", "cancelled"].includes(status) &&
      !block.has_more
    )
      break;
    if (Date.now() > deadline)
      throw new Error("Timed out waiting for final run status");
    if (!block.has_more) await page.waitForTimeout(400);
  }
  const final = await (await api(endpoint)).json();
  const latest = new Map(rows.map((e) => [e.id, e]));
  const visibleRows = rows.filter(
    (e) => e.payload?.type === "model_text" && e.payload.delta,
  );
  const nested = [...latest.values()].filter((e) => {
    if (e.kind !== "tool") return false;
    let parent = e.parent_id;
    const visited = new Set();
    while (parent && latest.has(parent) && !visited.has(parent)) {
      visited.add(parent);
      if (latest.get(parent).kind === "subagent") return true;
      parent = latest.get(parent).parent_id;
    }
    return false;
  });
  evidence.status = final.status;
  evidence.service_error = final.error;
  evidence.event_count = rows.length;
  evidence.public_fragments = visibleRows.length;
  evidence.intermediate_visible_text = [...seenText]
    .sort((a, b) => a.length - b.length)
    .slice(0, 3);
  evidence.tools = [...latest.values()]
    .filter((e) => e.kind === "tool")
    .map((e) => ({
      name: e.label,
      status: e.status,
      argument_keys: Object.keys(e.payload?.arguments || {}),
      has_result: e.payload?.result !== undefined,
      exit_code: e.payload?.exit_code,
    }));
  evidence.nested_tool_count = nested.length;
  evidence.failed_tools = [...latest.values()]
    .filter((e) => e.kind === "tool" && e.status === "failed")
    .map((e) => ({
      name: e.label,
      result: e.payload?.result,
      command: e.payload?.arguments?.command,
      exit_code: e.payload?.exit_code,
    }));
  const firstPublic = visibleRows[0];
  if (firstPublic) {
    const same = visibleRows.filter((e) => e.id === firstPublic.id);
    evidence.public_sample = {
      first_seq: firstPublic.seq,
      last_seq: same.at(-1).seq,
      text: same
        .map((e) => e.payload.delta)
        .join("")
        .slice(0, 400),
      next_tool: rows.find((e) => e.kind === "tool" && e.seq > firstPublic.seq)
        ?.label,
    };
  }
  const example = [...latest.values()].find(
    (e) =>
      e.kind === "tool" &&
      e.status === "completed" &&
      e.payload?.arguments?.file_path,
  );
  if (example)
    evidence.tool_sample = {
      name: example.label,
      parent_id: example.parent_id,
      file_path: example.payload.arguments.file_path,
      result: example.payload.result,
    };
  evidence.browser_exceptions = errors;
  evidence.checks.event_stream = originalSSE;
  evidence.checks.visible_before_terminal = seenText.size > 0 && captured;
  evidence.checks.captured_public_fragments = visibleRows.length > 0;
  evidence.checks.nested_tools = nested.length > 0;
  evidence.checks.tool_details = evidence.tools.some(
    (t) => t.argument_keys.length && t.has_result,
  );
  evidence.checks.terminal_completed = final.status === "completed";
  await page.reload();
  await page.locator(".trace-summary").waitFor();
  if (
    (await page.locator(".trace-summary").getAttribute("aria-expanded")) !==
    "true"
  )
    await page.locator(".trace-summary").click();
  await page.locator(".timeline-message").first().waitFor();
  const previews = await page.locator(".timeline-message").allInnerTexts();
  evidence.checks.replayed_public_text = previews.length > 0;
  const delegate = page
    .locator(".node-toggle")
    .filter({ hasText: /子智能体/ })
    .first();
  if (nested.length) await delegate.waitFor();
  if (
    (await delegate.count()) &&
    (await delegate.getAttribute("aria-expanded")) === "false"
  )
    await delegate.click();
  const nestedTool = delegate
    .locator("xpath=ancestor::li[1]")
    .locator("li[data-kind='tool'] .node-toggle")
    .first();
  if ((await nestedTool.count()) && (await nestedTool.isVisible())) {
    if ((await nestedTool.getAttribute("aria-expanded")) === "false")
      await nestedTool.click();
    await nestedTool.scrollIntoViewIfNeeded();
  }
  await screenshot("live-trace");
  const files = await (await api("/api/sessions/" + sid + "/files")).json();
  evidence.files = files.map(({ path, size }) => ({ path, size }));
  const orders = (
    await (await api("/api/sessions/" + sid + "/file?path=orders.csv")).text()
  ).trim();
  const csv = Papa.parse(orders.replace(/^\uFEFF/, ""), {
    header: true,
    skipEmptyLines: true,
  });
  const ordered = csv.data.map((row) => {
    const values = Object.values(row).map((v) => String(v).trim());
    const channel = values.find((v) => ["门店", "线上", "合作"].includes(v));
    const amount = values
      .map((v) => Number(v.replace(/,/g, "")))
      .find((n) => [1200, 800, 500, 1000].includes(n));
    return channel + " " + amount;
  });
  evidence.checks.verified_input_orders =
    ordered.sort().join("|") ===
    ["门店 1200", "线上 800", "门店 500", "合作 1000"].sort().join("|");
  let report = (
    await (
      await api("/api/sessions/" + sid + "/file?path=channel-summary.md")
    ).text()
  ).trim();
  await writeFile(
    resolve(evidencePath, "channel-summary.md"),
    report + "\n",
    "utf8",
  );
  const normalized = report.replace(/[,，\s]/g, "");
  evidence.checks.checked_amounts =
    ["1700", "800", "1000", "3500"].every((n) => normalized.includes(n)) &&
    [
      ["门店", "1700"],
      ["线上", "800"],
      ["合作", "1000"],
    ].every(([channel, amount]) =>
      report
        .split("\n")
        .some(
          (line) =>
            line.includes(channel) &&
            line.replace(/[,，\s]/g, "").includes(amount),
        ),
    );
  evidence.report_sha256 = createHash("sha256")
    .update(report + "\n")
    .digest("hex");
  const panel = page.getByRole("complementary", { name: "工作区文件" });
  async function openReport() {
    if (!(await panel.isVisible()))
      await page.getByRole("button", { name: "文件", exact: true }).click();
    await panel.getByRole("button", { name: /channel-summary.md/ }).click();
    await panel
      .getByText(/3[,，]?500/)
      .first()
      .waitFor({ timeout: 30000 });
  }
  await openReport();
  evidence.checks.artifact_in_ui = (await panel.innerText())
    .replace(/[,，\s]/g, "")
    .includes("3500");
  console.log("Artifact loaded from completed run", rid);
  const previewDeadline =
    Date.now() + Number(process.env.FAMSUN_E2E_PREVIEW_RETRY_MS || 0);
  const original = panel.getByRole("button", { name: "原文", exact: true });
  while (!(await original.isVisible()) && Date.now() < previewDeadline) {
    await page.waitForTimeout(10000);
    await page.reload();
    await openReport();
  }
  await original.waitFor();
  evidence.checks.rendered_report =
    (await panel.getByRole("heading", { level: 1 }).count()) > 0 &&
    (await panel.getByRole("table").count()) > 0;
  await screenshot("live-report-preview");
  evidence.checks.no_page_errors = errors.length === 0;
  for (const [check, ok] of Object.entries(evidence.checks))
    if (!ok) evidence.errors.push(check);
} catch (error) {
  // Playwright may include authentication headers in exceptions; retain only a safe summary.
  evidence.errors.push(
    error.name +
      ": " +
      String(error.message)
        .split("\n")[0]
        .replace(/famsun_session=[^\s;]+/g, "[redacted]")
        .slice(0, 220),
  );
} finally {
  await context.request
    .post("/api/auth/logout", { timeout: 5000 })
    .catch(() => {});
  await browser.close();
  evidence.finished_utc = new Date().toISOString();
  await writeFile(
    resolve(evidencePath, "live-timeline.json"),
    JSON.stringify(evidence, null, 2) + "\n",
  );
}
console.log("Live checks", evidence.status, evidence.checks, evidence.errors);
if (evidence.errors.length) process.exitCode = 1;
