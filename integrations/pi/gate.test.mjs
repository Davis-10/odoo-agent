import { test, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { TOOL_NAME, hostConfirmation, isPlanId, stateDir, planSummary, handleToolCall } from "./gate.mjs";

const PLAN_ID = "0123456789abcdef0123456789abcdef";
const KEY = "ab".repeat(32);
const TIMESHEET = {
  plan_id: PLAN_ID, kind: "timesheet", profile: "acme",
  values: { name: "Checkout work", date: "2026-10-21", unit_amount: 2.5, task_id: 9102 },
  display: { task: { id: 9102, name: "Checkout redesign" }, project: { id: 501, name: "Globex" },
    date: "2026-10-21", hours: 2.5, description: "Checkout work", day_hours_before: 3, day_hours_after: 5.5 },
};
const TASK = {
  plan_id: PLAN_ID, kind: "task", profile: "acme", values: { name: "New task", project_id: 501, stage_id: 44 },
  display: { project: { id: 501, name: "Globex" }, client: null, name: "New task", description: "Some details",
    planned_hours: 4, stage: { id: 44, name: "EN PROCESO" } },
};

let dir;
beforeEach(() => { dir = mkdtempSync(join(tmpdir(), "agentodoo-pi-")); mkdirSync(join(dir, "plans")); });
afterEach(() => rmSync(dir, { recursive: true, force: true }));

const writePlan = (plan) => writeFileSync(join(dir, "plans", `${plan.plan_id}.json`), JSON.stringify(plan));
const writeKey = (text = `${KEY}\n`) => writeFileSync(join(dir, "host-gate.key"), text);
const ui = (answer) => { const calls = []; return { calls, hasUI: true,
  ui: { confirm: async (title, body) => { calls.push({ title, body }); return answer; } } }; };
const event = (input, toolName = TOOL_NAME) => ({ type: "tool_call", toolCallId: "1", toolName, input });

test("cross-language vector", () => {
  const value = hostConfirmation("00".repeat(32), PLAN_ID);
  console.log(`cross-language vector: ${value}`);
  assert.equal(value, createHmac("sha256", Buffer.alloc(32)).update(PLAN_ID, "utf8").digest("hex"));
  assert.match(value, /^[0-9a-f]{64}$/);
});

test("plan id validation", () => {
  assert.ok(isPlanId(PLAN_ID));
  for (const bad of ["../etc/passwd", PLAN_ID.toUpperCase(), PLAN_ID + "0", "", 5, undefined]) assert.equal(isPlanId(bad), false);
});

test("state dir follows XDG_STATE_HOME, else ~/.local/state", () => {
  assert.equal(stateDir({ XDG_STATE_HOME: "/x" }, "/home/me"), join("/x", "agentodoo"));
  assert.equal(stateDir({}, "/home/me"), join("/home/me", ".local", "state", "agentodoo"));
});

test("other tools pass through untouched", async () => {
  const ctx = ui(true);
  const input = { plan_id: PLAN_ID, host_confirmation: "x" };
  assert.equal(await handleToolCall(event(input, "mcp__agentodoo__plan_time"), ctx, dir), undefined);
  assert.deepEqual(input, { plan_id: PLAN_ID, host_confirmation: "x" });
  assert.equal(ctx.calls.length, 0);
});

test("approve injects the HMAC and replaces the model's value", async () => {
  writePlan(TIMESHEET); writeKey();
  const ctx = ui(true);
  const input = { plan_id: PLAN_ID, host_confirmation: "forged" };
  assert.equal(await handleToolCall(event(input), ctx, dir), undefined);
  assert.equal(input.host_confirmation, createHmac("sha256", Buffer.from(KEY, "hex")).update(PLAN_ID).digest("hex"));
  assert.equal(ctx.calls.length, 1);
});

test("decline blocks and injects nothing", async () => {
  writePlan(TIMESHEET); writeKey();
  const input = { plan_id: PLAN_ID, host_confirmation: "forged" };
  const result = await handleToolCall(event(input), ui(false), dir);
  assert.equal(result.block, true);
  assert.match(result.reason, /AgentOdoo write not approved/);
  assert.equal("host_confirmation" in input, false);
});

test("no UI blocks without asking", async () => {
  writePlan(TIMESHEET); writeKey();
  const ctx = ui(true); ctx.hasUI = false;
  const input = { plan_id: PLAN_ID, host_confirmation: "forged" };
  const result = await handleToolCall(event(input), ctx, dir);
  assert.equal(result.block, true);
  assert.equal(ctx.calls.length, 0);
  assert.equal("host_confirmation" in input, false);
});

for (const [label, setup, pattern] of [
  ["missing plan", () => writeKey(), /plan/i],
  ["missing key", () => writePlan(TIMESHEET), /agentodoo host-gate init/],
  ["invalid key", () => { writePlan(TIMESHEET); writeKey("zz"); }, /agentodoo host-gate init/],
  ["unreadable plan JSON", () => { writeKey(); writeFileSync(join(dir, "plans", `${PLAN_ID}.json`), "{"); }, /plan/i],
]) {
  test(`${label} blocks with a reason`, async () => {
    setup();
    const ctx = ui(true);
    const input = { plan_id: PLAN_ID, host_confirmation: "forged" };
    const result = await handleToolCall(event(input), ctx, dir);
    assert.equal(result.block, true);
    assert.match(result.reason, pattern);
    assert.equal("host_confirmation" in input, false);
    assert.equal(ctx.calls.length, 0);
  });
}

test("bad plan id blocks with a reason", async () => {
  writeKey();
  const input = { plan_id: "../../host-gate", host_confirmation: "forged" };
  const result = await handleToolCall(event(input), ui(true), dir);
  assert.equal(result.block, true);
  assert.match(result.reason, /plan id/i);
  assert.equal("host_confirmation" in input, false);
});

test("timesheet summary", () => {
  const text = planSummary(TIMESHEET);
  for (const part of ["timesheet", "acme", "Globex", "Checkout redesign", "2026-10-21", "2.5", "3 h", "5.5 h", "Checkout work"])
    assert.ok(text.includes(part), `${part} missing from:\n${text}`);
});

test("task summary", () => {
  const text = planSummary(TASK);
  for (const part of ["task", "acme", "Globex", "New task", "EN PROCESO", "Some details", "4"])
    assert.ok(text.includes(part), `${part} missing from:\n${text}`);
});
