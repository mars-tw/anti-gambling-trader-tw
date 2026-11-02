"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const sourcePath = path.resolve(__dirname, "../core/ui/static/api-data.js");
const source = fs.readFileSync(sourcePath, "utf8").replace(/\r\n/g, "\n");

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

function fixture() {
  const events = new Map();
  const nodes = new Map();
  const timers = new Map();
  const calls = [];
  const applied = [];
  let nextTimer = 1;
  let syncReply = null;
  let analyzeReply = null;
  const defaults = {
    "data-provider": "binance", "data-kind": "fills", "data-market": "spot",
    "data-symbol": "BTCUSDT", "data-interval": "1h", "data-limit": "100",
    "credential-profile": "default", "data-pnl-basis": "unknown",
  };
  function node(id) {
    if (!nodes.has(id)) {
      const value = {
        id, value: defaults[id] || "", checked: false, disabled: false, hidden: false,
        open: false, textContent: "", listeners: {}, children: [], options: [{ value: "" }],
        addEventListener(type, listener) { (this.listeners[type] ||= []).push(listener); },
        setAttribute() {}, append(...children) { this.children.push(...children); },
        appendChild(child) { this.children.push(child); if (child && "value" in child) this.options.push(child); return child; },
        removeChild() {}, remove(index) { this.options.splice(index, 1); },
        querySelectorAll() { return []; }, reportValidity() { return true; },
      };
      nodes.set(id, value);
    }
    return nodes.get(id);
  }
  const ui = {
    post: async (endpoint, payload) => {
      calls.push({ method: "POST", endpoint, payload });
      if (endpoint === "/api/data-sync") return syncReply ? syncReply.promise : { job_id: `job-${calls.length}`, status: "running" };
      if (endpoint === "/api/data-analyze") return analyzeReply ? analyzeReply.promise : { available: true, analysis: { id: "fresh" } };
      return { status: "running" };
    },
    api: async (endpoint) => { calls.push({ method: "GET", endpoint }); return { status: "cancelled" }; },
    invalidateAnalysis() {}, applyAnalysis(analysis) { applied.push(analysis); },
    showNotice() {}, showError() {}, getState() { return null; }, downloadArtifact: async () => {},
  };
  const context = {
    console, Intl, Date, Number, Math, Object, Array, String, Boolean, Promise, Set, Map, Error,
    window: {
      AntiGamblingUI: ui, addEventListener() {},
      setTimeout(callback) { const id = nextTimer++; timers.set(id, callback); return id; },
      clearTimeout(id) { timers.delete(id); },
    },
    document: {
      getElementById: node,
      addEventListener(type, listener) { (events.get(type) || (events.set(type, []), events.get(type))).push(listener); },
      createElement() { return node(`generated-${nodes.size}`); }, createElementNS() { return node(`svg-${nodes.size}`); },
    },
  };
  const marker = "  bind();\n  if (typeof ui.getState";
  const instrumented = source.replace(marker, `  globalThis.audit = {
    startSync, analyzeClosed, invalidateForInput, buildRequest, updateFormForProvider,
    seed(value) { currentJobId = value.jobId; currentJobStatus = value.status; currentDataset = value.dataset; currentQuery = value.query || null; },
    state() { return { active, closed, currentJobId, currentJobStatus, currentDataset, requestInFlight, queryGeneration, analysisGeneration, analysisPending: analysisPending !== null }; }
  };\n${marker}`);
  assert.notEqual(instrumented, source, "test instrumentation must find the real bootstrap");
  vm.runInNewContext(instrumented, context, { filename: sourcePath, timeout: 1000 });
  return {
    audit: context.audit, node, calls, applied, timers,
    setSync(reply) { syncReply = reply; }, setAnalyze(reply) { analyzeReply = reply; },
    event(type, detail) { for (const listener of events.get(type) || []) listener({ detail }); },
    change(id) { for (const listener of node(id).listeners.change || []) listener({ target: node(id) }); },
    input(id) { for (const listener of node(id).listeners.input || []) listener({ target: node(id) }); },
    async tick() { await Promise.resolve(); await Promise.resolve(); },
    async flushTimers() {
      for (const [id, callback] of [...timers]) { timers.delete(id); await callback(); }
      await Promise.resolve();
    },
  };
}

test("navigation cancels an owned job, settles it, and permits a new sync", async () => {
  const f = fixture();
  f.event("ui:section-change", { section: "api-data" });
  await f.audit.startSync(false);
  assert.equal(f.node("data-sync").disabled, true);
  f.event("ui:section-change", { section: "results" });
  await f.tick();
  assert.equal(f.calls.filter((call) => call.endpoint === "/api/data-cancel").length, 1);
  f.event("ui:section-change", { section: "api-data" });
  await f.flushTimers();
  assert.equal(f.node("data-sync").disabled, false);
  await f.audit.startSync(false);
  assert.equal(f.calls.filter((call) => call.endpoint === "/api/data-sync").length, 2);
});

test("an edit before a sync acknowledgement cancels the late job without adopting it", async () => {
  const f = fixture();
  const reply = deferred();
  f.setSync(reply);
  const pending = f.audit.startSync(false);
  f.input("data-sync-form");
  reply.resolve({ job_id: "late-job", status: "running" });
  await pending;
  await f.tick();
  assert.equal(f.calls.some((call) => call.endpoint === "/api/data-cancel" && call.payload.job_id === "late-job"), true);
  assert.equal(f.audit.state().currentJobId, null);
  assert.equal(f.node("data-sync").disabled, false);
  f.setSync(null);
  await f.audit.startSync(false);
  assert.equal(f.calls.filter((call) => call.endpoint === "/api/data-sync").length, 2);
});

test("session close cancels a late acknowledgement and never restores UI state", async () => {
  const f = fixture();
  const reply = deferred();
  f.setSync(reply);
  const pending = f.audit.startSync(false);
  f.event("ui:session-close", {});
  reply.resolve({ job_id: "closed-job", status: "running" });
  await pending;
  await f.tick();
  assert.equal(f.audit.state().closed, true);
  assert.equal(f.audit.state().currentDataset, null);
  assert.equal(f.calls.some((call) => call.endpoint === "/api/data-cancel" && call.payload.job_id === "closed-job"), true);
  assert.equal(f.timers.size, 0);
});

test("stale analysis callbacks cannot apply after confirmation changes and a fresh run recovers", async () => {
  const f = fixture();
  f.audit.seed({ jobId: "job-a", status: "completed", dataset: { provider: "binance", kind: "fills" } });
  f.node("data-opening-zero").checked = true;
  f.node("data-transfers-reconciled").checked = true;
  const stale = deferred();
  f.setAnalyze(stale);
  const first = f.audit.analyzeClosed();
  const duplicate = f.audit.analyzeClosed();
  assert.equal(f.calls.filter((call) => call.endpoint === "/api/data-analyze").length, 1);
  f.node("data-opening-zero").checked = false;
  f.change("data-opening-zero");
  stale.resolve({ available: true, analysis: { id: "stale" } });
  await Promise.all([first, duplicate]);
  assert.deepEqual(f.applied, []);
  f.node("data-opening-zero").checked = true;
  f.change("data-opening-zero");
  f.setAnalyze(null);
  await f.audit.analyzeClosed();
  assert.deepEqual(f.applied, [{ id: "fresh" }]);
});

test("account references only travel with applicable Shioaji fills or account queries", () => {
  const f = fixture();
  f.node("data-provider").value = "shioaji";
  f.node("data-kind").value = "candles";
  f.node("data-symbol").value = "2330";
  f.node("data-account-ref").value = "synthetic-account";
  f.audit.updateFormForProvider();
  assert.equal("account_ref" in f.audit.buildRequest().query, false);
  f.node("data-kind").value = "fills";
  f.node("data-account-ref").value = "synthetic-account";
  f.audit.updateFormForProvider();
  assert.equal(f.audit.buildRequest().query.account_ref, "synthetic-account");
  f.node("data-provider").value = "binance";
  f.audit.updateFormForProvider();
  assert.equal(f.node("data-account-ref").value, "");
  assert.equal("account_ref" in f.audit.buildRequest().query, false);
});
