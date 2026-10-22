// The plan card's script (src/agentodoo/ui/plan.html) driven with a fake DOM and a fake MCP Apps host.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const HTML = readFileSync(new URL("../../src/agentodoo/ui/plan.html", import.meta.url), "utf8");
const SCRIPT = HTML.match(/<script>([\s\S]*)<\/script>/)[1];
const PLAN_ID = "0123456789abcdef0123456789abcdef";
const TIME_PLAN = {
  plan_id: PLAN_ID, kind: "timesheet", profile: "acme",
  display: {task: {id: 9101, name: "Improvement backlog"}, project: {id: 501, name: "Globex | Webshop"},
            date: "2026-10-22", hours: 1.5, description: "Write gate", day_hours_before: 4, day_hours_after: 5.5},
  values: {name: "Write gate", unit_amount: 1.5}, confirm: "agentodoo apply ...",
};

class FakeElement {
  constructor(tag) { Object.assign(this, {tag, children: [], textContent: "", disabled: false, hidden: false, listeners: {}}); }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...children) { this.children = children; }
  addEventListener(type, fn) { this.listeners[type] = fn; }
  click() { this.listeners.click?.({}); }
  text() { return this.textContent + this.children.map((c) => c.text()).join(" "); }
}

function load() {
  const elements = {};
  const listeners = [];
  const parent = {sent: [], postMessage(message) { this.sent.push(structuredClone(message)); }};
  const document = {
    documentElement: {scrollWidth: 400, scrollHeight: 200},
    getElementById: (id) => (elements[id] ??= new FakeElement(id)),
    createElement: (tag) => new FakeElement(tag),
  };
  const window = {parent, addEventListener: (type, fn) => type === "message" && listeners.push(fn)};
  vm.runInNewContext(SCRIPT, {window, document, JSON, Number, String, Array, Object});
  const deliver = (data, source = parent) => listeners.forEach((fn) => fn({source, data}));
  const sent = (method) => parent.sent.filter((m) => m.method === method);
  const el = (id) => document.getElementById(id);
  // Finish the handshake and deliver a plan_time result, as the host does.
  const ready = (result = {content: [{type: "text", text: JSON.stringify(TIME_PLAN)}]}) => {
    deliver({jsonrpc: "2.0", id: sent("ui/initialize")[0].id, result: {protocolVersion: "2026-01-26"}});
    deliver({jsonrpc: "2.0", method: "ui/notifications/tool-result", params: result});
  };
  return {parent, deliver, sent, el, ready};
}

test("handshake: ui/initialize then initialized", () => {
  const card = load();
  const [init] = card.sent("ui/initialize");
  assert.equal(init.params.protocolVersion, "2026-01-26");
  assert.equal(card.sent("ui/notifications/initialized").length, 0);
  card.ready();
  assert.equal(card.sent("ui/notifications/initialized").length, 1);
});

test("renders the plan with enabled buttons", () => {
  const card = load();
  assert.equal(card.el("confirm").disabled, true);
  card.ready();
  assert.equal(card.el("title").textContent, "Timesheet line");
  const text = card.el("details").text();
  for (const expected of ["Globex | Webshop (501)", "Improvement backlog (9101)", "2026-10-22",
                          "1.5", "Write gate", "4 h → 5.5 h"]) {
    assert.ok(text.includes(expected), `${expected} in ${text}`);
  }
  // Raw values and the plan id live in the collapsed technical section, not the main list.
  assert.ok(!text.includes(PLAN_ID), `plan id must not be in the main list: ${text}`);
  const technical = card.el("technical").text();
  for (const expected of ['"unit_amount":1.5', PLAN_ID]) {
    assert.ok(technical.includes(expected), `${expected} in ${technical}`);
  }
  assert.equal(card.el("confirm").disabled, false);
  assert.equal(card.el("cancel").disabled, false);
});

test("Confirm calls confirm_plan with the plan id once and shows the receipt", () => {
  const card = load();
  card.ready();
  card.el("confirm").click();
  card.el("confirm").click();
  const calls = card.sent("tools/call");
  assert.deepEqual(calls.map((c) => c.params), [{name: "confirm_plan", arguments: {plan_id: PLAN_ID}}]);
  assert.equal(card.el("confirm").disabled, true);
  assert.equal(card.el("cancel").disabled, true);
  const receipt = {status: "applied", record_id: 9001, plan_id: PLAN_ID};
  card.deliver({jsonrpc: "2.0", id: calls[0].id, result: {content: [{type: "text", text: JSON.stringify(receipt)}]}});
  assert.match(card.el("status").textContent, /applied/);
  assert.match(card.el("status").textContent, /9001/);
  assert.equal(card.el("eyebrow").textContent, "AgentOdoo · written");
  // The model never sees confirm_plan, so the card tells it the outcome for its next turn.
  const [context] = card.sent("ui/update-model-context");
  assert.equal(context.params.content[0].type, "text");
  assert.equal(context.params.content[0].text,
    `AgentOdoo plan ${PLAN_ID} was confirmed by the user on the plan card and written to Odoo: ` +
    "status applied, record 9001. Do not apply or re-plan it.");
});

test("Confirm shows the tool error or JSON-RPC error", () => {
  for (const reply of [{result: {isError: true, content: [{type: "text", text: "Plan x was already applied"}]}},
                       {error: {code: -32000, message: "Plan x was already applied"}}]) {
    const card = load();
    card.ready();
    card.el("confirm").click();
    card.deliver({jsonrpc: "2.0", id: card.sent("tools/call")[0].id, ...reply});
    assert.match(card.el("status").textContent, /Plan x was already applied/);
    assert.equal(card.el("confirm").disabled, true);
    assert.equal(card.el("eyebrow").textContent, "AgentOdoo · not written");
    assert.equal(card.sent("ui/update-model-context")[0].params.content[0].text,
      `AgentOdoo plan ${PLAN_ID}: the user pressed Confirm on the plan card but the write failed: ` +
      "Plan x was already applied");
  }
});

test("Cancel calls nothing and says nothing was written", () => {
  const card = load();
  card.ready();
  card.el("cancel").click();
  card.el("confirm").click();
  assert.equal(card.sent("tools/call").length, 0);
  assert.equal(card.el("status").textContent, "Nothing was written");
  assert.equal(card.el("confirm").disabled, true);
  assert.equal(card.el("cancel").disabled, true);
  assert.equal(card.el("eyebrow").textContent, "AgentOdoo · discarded");
  assert.equal(card.sent("ui/update-model-context")[0].params.content[0].text,
    `AgentOdoo plan ${PLAN_ID} was cancelled by the user on the plan card; nothing was written to Odoo.`);
});

test("a tool error result shows the message and keeps buttons disabled", () => {
  const card = load();
  card.ready({isError: true, content: [{type: "text", text: "Project 502 is flagged closed_stage"}]});
  assert.match(card.el("status").textContent, /Project 502 is flagged closed_stage/);
  assert.equal(card.el("confirm").disabled, true);
});

test("messages from other windows are ignored", () => {
  const card = load();
  card.deliver({jsonrpc: "2.0", id: card.sent("ui/initialize")[0].id, result: {}}, {});
  assert.equal(card.sent("ui/notifications/initialized").length, 0);
});
