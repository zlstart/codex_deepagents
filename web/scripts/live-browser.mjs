/** Exercise a deployed instance, including its actual model and execution sandbox.
 * Run from web/: node scripts/live-browser.mjs http://127.0.0.1:8080 ../docs/testing
 * Requires enabled signup; creates one account and a billable test task.
 */
import assert from "node:assert/strict";
import { createHash, randomBytes } from "node:crypto";
import { mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { chromium } from "@playwright/test";

const base = process.argv[2];
const directory = process.argv[3];
if (!base || !directory)
  throw new Error("Pass the deployment URL and an evidence directory.");
await mkdir(directory, { recursive: true });
const viewport = { width: 1440, height: 940 };
const browser = await chromium.launch({
  executablePath: process.env.FAMSUN_E2E_CHROMIUM || undefined,
  args: [
    "--use-gl=angle",
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
  ],
});
const digest = (buffer) => createHash("sha256").update(buffer).digest("hex");
const brief =
  "生成双半联轴器 coupling.glb。让 engineering 子智能体仅用一次 Python 命令执行以下方案：用 trimesh.creation.cylinder 与 trimesh.util.concatenate 拼接法兰与轴套，绕 X 轴旋转到水平，两半分别设为青绿、银灰，作为两个命名网格加入 Scene。直接导出并用 trimesh.load 验证有 2 个几何体。只需示意模型，使用预装 trimesh，不需要钻孔、布尔运算或安装包。主智能体收到验证结果后直接答复。";
const resume = process.env.FAMSUN_SMOKE_RESUME;
const sessionDir =
  resume || (await mkdtemp(resolve(tmpdir(), "famsun-live-browser-")));
const storageFile = resolve(sessionDir, "state.json");
const runFile = resolve(sessionDir, "run.json");
console.log("Private test session recovery directory", sessionDir);

try {
  const page = await browser.newPage({
    baseURL: base,
    viewport,
    locale: "zh-CN",
    storageState: resume ? storageFile : undefined,
  });
  const errors = [];
  const httpIssues = [];
  const states = new Set();
  const events = new Map();
  page.on("pageerror", (error) =>
    errors.push(error.name + ": " + error.message),
  );
  page.on("response", async (response) => {
    const path = new URL(response.url()).pathname;
    if (!path.startsWith("/api/")) return;
    if (
      response.status() >= 400 &&
      !(path === "/api/auth/me" && response.status() === 401)
    ) {
      httpIssues.push({ path, code: response.status() });
    }
    if (path.endsWith("/events")) {
      try {
        const body = await response.json();
        states.add(body.status);
        for (const evt of body.events) events.set(evt.id, evt);
      } catch {
        /* A completed poll may be aborted during a React refresh. */
      }
    }
  });
  await page.goto("/");
  assert.match(await page.title(), /FAMSUN Agent/);
  let runId;
  if (!resume) {
    await page.getByRole("button", { name: "还没有账户？创建账户" }).click();
    await page
      .getByLabel("用户名", { exact: true })
      .fill("studio-" + randomBytes(4).toString("hex"));
    await page
      .getByLabel("密码", { exact: true })
      .fill(randomBytes(24).toString("hex"));
    await page.getByRole("button", { name: "创建账户", exact: true }).click();
    await page.getByRole("heading", { name: "有什么可以帮你？" }).waitFor();
    await page.getByRole("textbox", { name: "任务描述" }).fill(brief);
    const [accepted] = await Promise.all([
      page.waitForResponse(
        (r) => r.request().method() === "POST" && r.url().endsWith("/runs"),
      ),
      page.getByRole("button", { name: "发送任务", exact: true }).click(),
    ]);
    assert.equal(accepted.status(), 202);
    runId = (await accepted.json()).id;
    await writeFile(
      storageFile,
      JSON.stringify(await page.context().storageState()),
      { mode: 0o600 },
    );
    await writeFile(runFile, JSON.stringify({ runId }), { mode: 0o600 });
    console.log("Live run submitted", runId);
  } else {
    runId = JSON.parse(await readFile(runFile, "utf8")).runId;
  }
  const tree = page.getByRole("button", { name: /执行过程/ });
  await tree.waitFor();
  if ((await tree.getAttribute("aria-expanded")) !== "true") await tree.click();
  const terminalStatus = () =>
    page.waitForFunction(
      () => {
        const status = document.querySelector(".trace-summary small");
        return (
          status &&
          ["已完成", "失败", "已停止"].includes(status.textContent.trim())
        );
      },
      null,
      { timeout: 30 * 60 * 1000 },
    );
  const finished = terminalStatus().then(() =>
    tree.locator("small").innerText(),
  );
  // Also check when the file first becomes available, even if the model is still reviewing it.
  await Promise.race([
    finished,
    new Promise((done) => setTimeout(done, resume ? 0 : 80000)),
  ]);
  await page.getByRole("button", { name: "文件", exact: true }).click();
  const panel = page.getByRole("complementary", { name: "工作区文件" });
  const file = panel.getByRole("button", { name: /coupling\.glb/ });
  const deadline = Date.now() + 30 * 60 * 1000;
  while (!(await file.isVisible()) && Date.now() < deadline) {
    const status = (await tree.locator("small").innerText()).trim();
    const terminal = ["已完成", "失败", "已停止"].includes(status);
    await panel.getByRole("button", { name: "刷新文件" }).click();
    try {
      await file.waitFor({ timeout: 20000 });
      break;
    } catch (error) {
      if (terminal || error.name !== "TimeoutError") throw error;
    }
  }
  await file.click({ timeout: 30000 });
  const slider = page.getByRole("slider", { name: "爆炸视图" });
  await slider.waitFor({ timeout: 60000 });
  const canvas = page.getByLabel("三维模型画布");
  const webgl = await canvas.evaluate((element) => {
    const gl = element.getContext("webgl2");
    return {
      version: gl.getParameter(gl.VERSION),
      width: element.width,
      height: element.height,
    };
  });
  const meshLabel = await page
    .locator(".model-controls div > span")
    .first()
    .innerText();
  const meshes = Number.parseInt(meshLabel);
  assert.ok(meshes >= 2);
  const original = await canvas.screenshot();
  const bounds = await canvas.boundingBox();
  await page.mouse.move(
    bounds.x + bounds.width * 0.45,
    bounds.y + bounds.height * 0.52,
  );
  await page.mouse.down();
  await page.mouse.move(
    bounds.x + bounds.width * 0.58,
    bounds.y + bounds.height * 0.57,
    { steps: 10 },
  );
  await page.mouse.up();
  const rotated = await canvas.screenshot();
  assert.notEqual(digest(original), digest(rotated));
  await slider.focus();
  await slider.press("Home");
  for (let i = 0; i < 55; i++) await slider.press("ArrowRight");
  assert.equal(await slider.inputValue(), "55");
  const exploded = await canvas.screenshot();
  assert.notEqual(digest(rotated), digest(exploded));
  const pixels = await page.evaluate(async (encoded) => {
    const bytes = Uint8Array.from(atob(encoded), (c) => c.charCodeAt(0));
    const bitmap = await createImageBitmap(
      new Blob([bytes], { type: "image/png" }),
    );
    const surface = new OffscreenCanvas(bitmap.width, bitmap.height);
    const ctx = surface.getContext("2d");
    ctx.drawImage(bitmap, 0, 0);
    const data = ctx.getImageData(0, 0, surface.width, surface.height).data;
    const background = Array.from(data.subarray(0, 3));
    let foreground = 0;
    for (let index = 0; index < data.length; index += 4) {
      if (
        background.some(
          (component, channel) =>
            Math.abs(component - data[index + channel]) > 20,
        )
      )
        foreground++;
    }
    return { foreground, total: bitmap.width * bitmap.height };
  }, exploded.toString("base64"));
  assert.ok(
    pixels.foreground > pixels.total * 0.005,
    "Rendered geometry must cover at least 0.5% of the viewport",
  );
  console.log("WebGL preview verified", JSON.stringify({ meshes, pixels }));
  const terminal = await finished;
  console.log("Live run terminal", terminal);
  const subagent = page
    .locator(".trace-tree")
    .getByRole("button", { name: /engineering/ })
    .first();
  await subagent.waitFor();
  if ((await subagent.getAttribute("aria-expanded")) !== "true")
    await subagent.click();
  // Frame the separated parts for a desktop screenshot after verifying free orbit.
  await page.getByRole("button", { name: "重置视角" }).click();
  await canvas.hover();
  await page.mouse.wheel(0, 1050);
  await page.locator(".conversation-scroll").hover();
  await page.mouse.wheel(0, -2500);
  await page.locator(".trace-summary").waitFor();
  await page.evaluate(
    () =>
      new Promise((done) =>
        requestAnimationFrame(() => requestAnimationFrame(done)),
      ),
  );
  // Verify Chromium actually used a font with CJK glyphs for visible Chinese UI.
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("DOM.enable");
  await cdp.send("CSS.enable");
  const { root } = await cdp.send("DOM.getDocument");
  const { nodeId } = await cdp.send("DOM.querySelector", {
    nodeId: root.nodeId,
    selector: ".trace-summary > span",
  });
  const { fonts } = await cdp.send("CSS.getPlatformFontsForNode", { nodeId });
  assert.ok(
    fonts.some(
      (f) => f.glyphCount >= 4 && /CJK|Han|PingFang|YaHei/i.test(f.familyName),
    ),
    "Chinese text must use a CJK font",
  );
  assert.ok(
    await slider.isVisible(),
    "File panel must remain visible beside the execution tree",
  );
  const screenshot = await page.screenshot({
    path: resolve(directory, "workbench-live.png"),
  });
  const nodes = [...events.values()];
  assert.ok(nodes.some((e) => e.kind === "subagent"));
  assert.ok(nodes.some((e) => e.kind === "tool" && e.label === "execute"));
  assert.equal(errors.length, 0, errors.join("\n"));
  assert.equal(httpIssues.length, 0, JSON.stringify(httpIssues));
  const installationWarnings = nodes.filter(
    (e) =>
      e.kind === "tool" &&
      /uv pip install/.test(e.detail || "") &&
      /error: (request failed|failed to)/i.test(e.detail || ""),
  );
  const result = {
    runId,
    recordedAt: new Date().toISOString(),
    browser: browser.version(),
    viewport,
    signupViaUI: true,
    submissionHTTPStatus: 202,
    terminal,
    statesObserved: [...states],
    traceNodes: nodes.length,
    nestedTools: nodes.filter(
      (e) =>
        e.kind === "tool" &&
        nodes.some((p) => p.kind === "subagent" && p.id === e.parent_id),
    ).length,
    toolFailures: nodes
      .filter((e) => e.kind === "tool" && e.status === "failed")
      .map((e) => e.label),
    packageInstallWarnings: installationWarnings.length,
    artifact: "coupling.glb",
    meshes,
    webgl,
    explodedPercent: 55,
    uniqueCanvasStates: new Set([original, rotated, exploded].map(digest)).size,
    pixels,
    fonts: fonts.map(({ familyName, glyphCount }) => ({
      familyName,
      glyphCount,
    })),
    unexpectedHttpErrors: httpIssues,
    pageErrors: errors,
    screenshotSHA256: digest(screenshot),
  };
  assert.ok(result.nestedTools > 0);
  await writeFile(
    resolve(directory, "workbench-live.json"),
    JSON.stringify(result, null, 2) + "\n",
  );
  assert.equal(
    terminal,
    "已完成",
    "Geometry preview succeeded but agent did not complete",
  );
  console.log("Live browser acceptance passed", JSON.stringify(result));
} finally {
  await browser.close();
}
