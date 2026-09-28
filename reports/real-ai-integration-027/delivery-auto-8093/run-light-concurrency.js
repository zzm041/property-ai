"use strict";

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { performance } = require("perf_hooks");

const ROOT = path.resolve(__dirname, "..", "..", "..");
const API = "http://127.0.0.1:8093/api/v1";
const PRIVATE_PATH = path.join(ROOT, "tmp", "delivery-auto-8093", "fixture-context.json");
const PUBLIC_PATH = path.join(__dirname, "light-concurrency.json");
const DURATION_MS = 60_000;
const INTERVAL_MS = 1_000;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function request(method, pathname, { token, body, expected = 200, label = "load" } = {}) {
  const started = performance.now();
  const response = await fetch(`${API}${pathname}`, {
    method,
    headers: {
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      "X-Request-Id": `${label}-${crypto.randomUUID()}`,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const raw = await response.text();
  const elapsed = performance.now() - started;
  if (response.status !== expected) {
    let code = "UNKNOWN";
    try { code = JSON.parse(raw).error.code; } catch (_) {}
    throw new Error(`${method} ${pathname} status=${response.status} expected=${expected} code=${code}`);
  }
  return { elapsed, data: raw ? JSON.parse(raw).data : null, status: response.status };
}

async function login(phone, password, suffix) {
  return (await request("POST", "/auth/password/login", {
    body: { phone, password }, label: `delivery-auto-load-login-${suffix}`,
  })).data;
}

function percentile(values, fraction) {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * fraction))] || 0;
}

async function main() {
  const context = JSON.parse(fs.readFileSync(PRIVATE_PATH, "utf8"));
  if (context.phase !== "PASSWORDS_READY") throw new Error("Fixture passwords are not ready");
  const keys = Object.keys(context.phones);
  const sessions = [];
  for (let round = 0; round < 2; round += 1) {
    for (const key of keys) {
      const loginResult = await login(context.phones[key], context.passwords[key], `${round}-${key}`);
      sessions.push({ key, token: loginResult.access_token, user_id: loginResult.user_id });
    }
  }
  if (sessions.length !== 20) throw new Error("Expected exactly 20 authenticated sessions");
  const managerA = sessions.find((item) => item.key === "managerA");
  const managerB = sessions.find((item) => item.key === "managerB");
  const orgA = context.organizations[0];
  const orgB = context.organizations[1];
  const crossA = await request("GET", `/admin/property-archive?tenant_id=${orgB.tenant_id}&property_id=${orgB.property_id}`, {
    token: managerA.token, expected: 403, label: "delivery-auto-cross-a-b",
  });
  const crossB = await request("GET", `/admin/property-archive?tenant_id=${orgA.tenant_id}&property_id=${orgA.property_id}`, {
    token: managerB.token, expected: 403, label: "delivery-auto-cross-b-a",
  });
  const start = performance.now();
  const latencies = [];
  const errors = [];
  let completed = 0;
  let cycles = 0;
  while (performance.now() - start < DURATION_MS) {
    const cycleStarted = performance.now();
    const results = await Promise.allSettled(sessions.map((session, index) =>
      request("GET", "/me", { token: session.token, label: `delivery-auto-load-${cycles}-${index}` })
    ));
    for (const result of results) {
      if (result.status === "fulfilled") {
        latencies.push(result.value.elapsed);
        completed += 1;
      } else {
        errors.push(String(result.reason && result.reason.message || result.reason));
      }
    }
    cycles += 1;
    const remaining = INTERVAL_MS - (performance.now() - cycleStarted);
    if (remaining > 0) await sleep(remaining);
  }
  const elapsedSeconds = (performance.now() - start) / 1000;
  const result = {
    task_id: "REAL-AI-INTEGRATION-027-DELIVERY-AUTO-CONCURRENCY",
    run_id: context.run_id,
    captured_at: new Date().toISOString(),
    environment: "8093/property-ai-delivery-check@0014",
    sessions: sessions.length,
    distinct_users: new Set(sessions.map((item) => item.user_id)).size,
    properties: context.organizations.map((item) => item.property_id),
    duration_seconds: Number(elapsedSeconds.toFixed(3)),
    cycles,
    completed_requests: completed,
    errors: errors.length,
    throughput_rps: Number((completed / elapsedSeconds).toFixed(3)),
    latency_ms: {
      min: Number(Math.min(...latencies).toFixed(3)),
      p50: Number(percentile(latencies, 0.50).toFixed(3)),
      p95: Number(percentile(latencies, 0.95).toFixed(3)),
      max: Number(Math.max(...latencies).toFixed(3)),
    },
    cross_property: {
      manager_a_to_b_status: crossA.status,
      manager_b_to_a_status: crossB.status,
    },
    ai_requests: 0,
    boundaries: {
      page_clicks: 0,
      real_devices: 0,
      claimed_capacity: "20 authenticated local sessions at a controlled read rate; not 20 devices or production scale",
    },
  };
  fs.writeFileSync(PUBLIC_PATH, `${JSON.stringify(result, null, 2)}\n`, "utf8");
  console.log(JSON.stringify(result));
  if (errors.length) process.exitCode = 2;
}

main().catch((error) => {
  const message = String(error && (error.stack || error.message) || error)
    .replace(/\+86\d{11}/g, "[REDACTED_PHONE]")
    .replace(/eyJ[\w-]+\.[\w-]+\.[\w-]+/g, "[REDACTED_TOKEN]");
  console.error(message);
  process.exitCode = 1;
});
