"use strict";

const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { performance } = require("perf_hooks");

const ROOT = "D:/property-ai";
const API = "http://127.0.0.1:8093/api/v1";
const CONTEXT_PATH = path.join(ROOT, "tmp/delivery-auto-8093/fixture-context.json");
const PREP_PATH = path.join(ROOT, "tmp/delivery-auto-8093/five-user-overlap-prep.json");
const RESULT_PATH = path.join(__dirname, "five-user-overlap-current-submit.json");
const MIN_FREE_RAM_MIB = 11264;
const RUN_SUFFIX = "overlap2-20261002";

const context = JSON.parse(fs.readFileSync(CONTEXT_PATH, "utf8"));
const prep = JSON.parse(fs.readFileSync(PREP_PATH, "utf8"));

if (context.phase !== "PASSWORDS_READY") throw new Error("fixture passwords are not ready");
if (prep.run_id !== context.run_id || prep.items.length !== 5) throw new Error("invalid preparation state");
if (fs.existsSync(RESULT_PATH)) throw new Error("current result exists; refusing duplicate submission");

const texts = {
  residentA1: "客厅天花板出现水渍和局部起皮，请维修组检查渗水来源并处理。",
  residentA2: "厨房阀门接口缓慢滴水，请维修组检查并处理。",
  residentA3: "卧室墙角渗水并有墙皮起泡，请维修组联系检查。",
  residentB1: "卫生间阀门开关费力且有少量渗水，请维修组检查。",
  residentB2: "厨房水龙头关紧后仍滴水，没有积水，请维修组检查。语音为本次同一问题说明。",
};

function redact(value) {
  return String(value || "")
    .replace(/\+?86\d{11}/g, "[REDACTED_PHONE]")
    .replace(/eyJ[\w-]+\.[\w-]+\.[\w-]+/g, "[REDACTED_TOKEN]");
}

async function request(method, pathname, { token, body, headers = {}, expected = 200 } = {}) {
  const response = await fetch(`${API}${pathname}`, {
    method,
    headers: {
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      "X-Request-Id": `delivery-overlap2-${crypto.randomUUID()}`,
      ...headers,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const raw = await response.text();
  let payload = {};
  try { payload = raw ? JSON.parse(raw) : {}; } catch (_) {}
  if (response.status !== expected) {
    throw new Error(`${method} ${pathname} status=${response.status} code=${payload?.error?.code || "UNKNOWN"}`);
  }
  return payload.data;
}

async function login(item) {
  const data = await request("POST", "/auth/password/login", {
    body: { phone: context.phones[item.key], password: context.passwords[item.key] },
  });
  if (Number(data.user_id) !== Number(item.userId)) throw new Error(`fixture user mismatch for ${item.key}`);
  return { ...item, token: data.access_token };
}

async function main() {
  const freeRamMiB = Math.floor(os.freemem() / 1024 / 1024);
  if (freeRamMiB < MIN_FREE_RAM_MIB) {
    throw new Error(`ASR resource guard: free RAM ${freeRamMiB} MiB is below ${MIN_FREE_RAM_MIB} MiB; no work order submitted`);
  }

  const sessions = await Promise.all(prep.items.map(login));
  const barrier = {};
  barrier.promise = new Promise((resolve) => { barrier.release = resolve; });
  const submissions = sessions.map((item) => (async () => {
    await barrier.promise;
    const started = performance.timeOrigin + performance.now();
    const marker = `${context.run_id}-${RUN_SUFFIX}-${item.key}`;
    const data = await request("POST", "/work-orders", {
      token: item.token,
      headers: { "Idempotency-Key": `delivery-${RUN_SUFFIX}-${item.key}` },
      body: {
        house_id: item.houseId,
        contact_phone: context.phones[item.key],
        text: `${marker}：${texts[item.key]}`,
        file_ids: item.mediaFiles.map((file) => file.id),
      },
    });
    const finished = performance.timeOrigin + performance.now();
    return {
      key: item.key,
      user_id: item.userId,
      tenant_id: context.organizations.find((org) => Number(org.property_id) === Number(item.propertyId)).tenant_id,
      property_id: item.propertyId,
      house_id: item.houseId,
      marker_sha256: crypto.createHash("sha256").update(marker).digest("hex"),
      file_ids: item.mediaFiles.map((file) => file.id),
      media_types: item.mediaFiles.map((file) => file.mime),
      work_order_id: data.id || data.work_order?.id,
      status: data.status || data.work_order?.status,
      started_at: new Date(started).toISOString(),
      finished_at: new Date(finished).toISOString(),
      duration_ms: Number((finished - started).toFixed(3)),
      _started_ms: started,
      _finished_ms: finished,
    };
  })());

  barrier.release();
  const settled = await Promise.allSettled(submissions);
  const fulfilled = settled.filter((item) => item.status === "fulfilled").map((item) => item.value);
  const failed = settled.filter((item) => item.status === "rejected");
  const overlapConfirmed = fulfilled.length === 5
    && Math.max(...fulfilled.map((item) => item._started_ms)) < Math.min(...fulfilled.map((item) => item._finished_ms));
  const publicResult = {
    task_id: "REAL-AI-INTEGRATION-027-DELIVERY-AUTO-OVERLAP2",
    run_id: context.run_id,
    run_suffix: RUN_SUFFIX,
    captured_at: new Date().toISOString(),
    environment: "8093/property_ai_delivery_check@0014",
    free_ram_before_submit_mib: freeRamMiB,
    interaction: "five normal password-authenticated users; concurrent real work-order API submissions using previously persisted same-owner media; not page clicks",
    submitted: fulfilled.length,
    overlap_confirmed: overlapConfirmed,
    overlap_span_ms: fulfilled.length
      ? Number((Math.min(...fulfilled.map((item) => item._finished_ms)) - Math.max(...fulfilled.map((item) => item._started_ms))).toFixed(3))
      : 0,
    items: fulfilled.map(({ _started_ms, _finished_ms, ...item }) => item),
    failures: failed.map((item) => redact(item.reason?.message || item.reason)),
    boundaries: {
      page_clicks: 0,
      direct_database_business_write: false,
      manual_enqueue_or_assign: false,
      media_reused_only_for_same_authenticated_owner: true,
      work_order_idempotency_keys_are_new_and_stable: true,
      credentials_or_tokens_saved: false,
    },
  };
  fs.writeFileSync(RESULT_PATH, `${JSON.stringify(publicResult, null, 2)}\n`);
  console.log(JSON.stringify({
    submitted: publicResult.submitted,
    overlap_confirmed: publicResult.overlap_confirmed,
    overlap_span_ms: publicResult.overlap_span_ms,
    work_order_ids: publicResult.items.map((item) => item.work_order_id),
    failures: publicResult.failures,
  }));
  if (failed.length || fulfilled.length !== 5 || !overlapConfirmed) process.exitCode = 2;
}

main().catch((error) => {
  console.error(redact(error && (error.stack || error.message) || error));
  process.exitCode = 1;
});
