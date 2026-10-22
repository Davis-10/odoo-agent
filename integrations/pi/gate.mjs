// Pure logic of the AgentOdoo host confirmation gate (S8), testable without Pi.
import { createHmac } from "node:crypto";
import { readFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";

export const TOOL_NAME = "mcp__agentodoo__apply_plan";
const PLAN_ID = /^[0-9a-f]{32}$/;
const KEY = /^[0-9a-f]{64}$/i;
const INIT_HINT = "Run: agentodoo host-gate init";

export function stateDir(env = process.env, home = homedir()) {
  return env.XDG_STATE_HOME ? join(env.XDG_STATE_HOME, "agentodoo") : join(home, ".local", "state", "agentodoo");
}

export function isPlanId(value) {
  return typeof value === "string" && PLAN_ID.test(value);
}

export function hostConfirmation(keyHex, planId) {
  return createHmac("sha256", Buffer.from(keyHex, "hex")).update(planId, "utf8").digest("hex");
}

const named = (item) => (item && typeof item === "object" ? `${item.name ?? ""} (${item.id})` : item);
const hours = (value) => `${value} h`;

export function planSummary(plan) {
  const display = plan.display ?? {};
  const values = plan.values ?? {};
  const lines = [`Kind: ${plan.kind}`, `Profile: ${plan.profile}`];
  const add = (label, value, format = (v) => v) => {
    if (value !== undefined && value !== null && value !== "") lines.push(`${label}: ${format(value)}`);
  };
  add("Project", named(display.project));
  add("Task", named(display.task));
  add("Stage", named(display.stage));
  add("Name", display.name ?? (plan.kind === "task" ? values.name : undefined));
  add("Description", display.description);
  add("Date", display.date ?? values.date);
  add("Hours", display.hours, hours);
  add("Planned hours", display.planned_hours, hours);
  add("Day total before", display.day_hours_before, hours);
  add("Day total after", display.day_hours_after, hours);
  lines.push(`Plan: ${plan.plan_id}`);
  return lines.join("\n");
}

const block = (reason) => ({ block: true, reason });

async function readText(path) {
  try {
    return await readFile(path, "utf8");
  } catch {
    return undefined;
  }
}

// Handles one tool_call event. Returns undefined to let the call run, or { block, reason }.
export async function handleToolCall(event, ctx, dir = stateDir()) {
  if (event.toolName !== TOOL_NAME) return undefined;
  const input = event.input ?? {};
  delete input.host_confirmation;

  const planId = input.plan_id;
  if (!isPlanId(planId)) return block("AgentOdoo write not approved: invalid plan id (expected 32 lowercase hex characters)");

  const planText = await readText(join(dir, "plans", `${planId}.json`));
  if (planText === undefined) return block(`AgentOdoo write not approved: plan ${planId} not found`);
  let plan;
  try {
    plan = JSON.parse(planText);
  } catch {
    return block(`AgentOdoo write not approved: plan ${planId} is unreadable`);
  }
  if (!plan || plan.plan_id !== planId) return block(`AgentOdoo write not approved: plan ${planId} is invalid`);

  const key = (await readText(join(dir, "host-gate.key")))?.trim();
  if (!key) return block(`AgentOdoo write not approved: host gate key missing. ${INIT_HINT}`);
  if (!KEY.test(key)) return block(`AgentOdoo write not approved: host gate key invalid. ${INIT_HINT}`);

  if (!ctx?.hasUI || typeof ctx.ui?.confirm !== "function")
    return block(`AgentOdoo write not approved: no UI to confirm. Run: agentodoo apply ${planId} --confirm ${planId}`);
  const approved = await ctx.ui.confirm("Write to Odoo?", planSummary(plan));
  if (approved !== true) return block("AgentOdoo write not approved");

  input.host_confirmation = hostConfirmation(key, planId);
  return undefined;
}
