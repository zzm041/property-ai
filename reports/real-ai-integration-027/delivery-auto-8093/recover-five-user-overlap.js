"use strict";

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const ROOT = path.resolve(__dirname, "../../..");
const API = "http://127.0.0.1:8093/api/v1";
const CONTEXT_PATH = path.join(ROOT, "tmp", "delivery-auto-8093", "fixture-context.json");
const EXECUTE = process.argv.includes("--execute");
const ONLY = process.argv.find((value) => value.startsWith("--only="));
const ONLY_WORK_ORDER = ONLY ? Number(ONLY.split("=", 2)[1]) : null;
const EXPECTED_RUN_ID = "delivery-auto-702c3db745";
const ITEMS = Object.freeze([
  { workOrderId: 7, intentId: 29, managerKey: "managerB", propertyId: 6 },
  { workOrderId: 8, intentId: 30, managerKey: "managerA", propertyId: 5 },
  { workOrderId: 9, intentId: 31, managerKey: "managerB", propertyId: 6 },
  { workOrderId: 10, intentId: 32, managerKey: "managerA", propertyId: 5 },
  { workOrderId: 11, intentId: 33, managerKey: "managerA", propertyId: 5 },
]);

async function request(method, pathname, { token, body, requestId, expected = 200 } = {}) {
  const response = await fetch(`${API}${pathname}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(requestId ? { "X-Request-Id": requestId } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  let payload;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    throw new Error(`${method} ${pathname} returned non-JSON status ${response.status}`);
  }
  if (response.status !== expected) {
    const code = payload?.error?.code || payload?.code || "UNKNOWN";
    throw new Error(`${method} ${pathname} returned ${response.status}/${code}`);
  }
  return payload?.data ?? payload;
}

async function login(context, key) {
  const phone = context.phones?.[key];
  const password = context.passwords?.[key];
  if (!phone || !password) throw new Error(`Missing private credential for ${key}`);
  return request("POST", "/auth/password/login", {
    body: { phone, password },
    requestId: `delivery-overlap-recovery-login-${key}`,
  });
}

async function main() {
  const context = JSON.parse(fs.readFileSync(CONTEXT_PATH, "utf8"));
  if (context.run_id !== EXPECTED_RUN_ID || context.phase !== "PASSWORDS_READY") {
    throw new Error("Private fixture context does not match the fixed overlap run");
  }
  const sessions = {};
  for (const key of ["managerA", "managerB"]) sessions[key] = await login(context, key);

  if (ONLY && (!Number.isInteger(ONLY_WORK_ORDER) || !ITEMS.some((item) => item.workOrderId === ONLY_WORK_ORDER))) {
    throw new Error("--only must identify one fixed work order");
  }
  const selected = ONLY_WORK_ORDER === null ? ITEMS : ITEMS.filter((item) => item.workOrderId === ONLY_WORK_ORDER);
  const checked = [];
  for (const item of selected) {
    const session = sessions[item.managerKey];
    const before = await request("GET", `/admin/task-intents/${item.intentId}`, {
      token: session.access_token,
      requestId: `delivery-overlap-recovery-inspect-${item.intentId}`,
    });
    if (before.operation !== "ANALYZE_WORK_ORDER" || before.resource_id !== item.workOrderId
        || before.generation !== 2 || before.status !== "FAILED") {
      throw new Error(`Intent ${item.intentId} no longer matches the immutable failed generation`);
    }
    checked.push({ ...item, before });
  }

  const results = [];
  if (EXECUTE) {
    for (const item of checked) {
      const session = sessions[item.managerKey];
      const stable = crypto.createHash("sha256")
        .update(`${EXPECTED_RUN_ID}:${item.intentId}:DEPENDENCY_RECOVERED`).digest("hex").slice(0, 24);
      const successor = await request("POST", `/admin/task-intents/${item.intentId}/retry`, {
        token: session.access_token,
        requestId: `delivery-overlap-recovery-${stable}`,
        body: { reason_code: "DEPENDENCY_RECOVERED" },
      });
      if (successor.operation !== "ANALYZE_WORK_ORDER" || successor.resource_id !== item.workOrderId
          || successor.generation !== 3) {
        throw new Error(`Intent ${item.intentId} returned an unexpected successor`);
      }
      results.push({ work_order_id: item.workOrderId, previous_intent_id: item.intentId,
        successor_intent_id: successor.id, generation: successor.generation, status: successor.status });
    }
  }

  console.log(JSON.stringify({
    task_id: "REAL-AI-INTEGRATION-027-DELIVERY-AUTO-OVERLAP-RECOVERY",
    run_id: EXPECTED_RUN_ID,
    captured_at: new Date().toISOString(),
    mode: EXECUTE ? "EXECUTED" : "READ_ONLY_PREFLIGHT",
    authenticated_managers: Object.fromEntries(Object.entries(sessions).map(([key, value]) => [key, value.user_id])),
    checked: checked.map((item) => ({ work_order_id: item.workOrderId, property_id: item.propertyId,
      intent_id: item.intentId, generation: item.before.generation, status: item.before.status,
      publish_attempt: item.before.publish_attempt, execution_attempt: item.before.execution_attempt,
      last_error_code: item.before.last_error_code })),
    successors: results,
    boundaries: { new_work_orders: 0, direct_database_business_write: false,
      failed_generation_rewritten: false, recovery_reason: EXECUTE ? "DEPENDENCY_RECOVERED" : null,
      credentials_or_tokens_saved: false },
  }, null, 2));
}

main().catch((error) => {
  console.error(JSON.stringify({ error: String(error.message || error) }));
  process.exit(1);
});
