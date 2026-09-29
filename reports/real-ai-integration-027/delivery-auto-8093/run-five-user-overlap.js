"use strict";

const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { performance } = require("perf_hooks");

const ROOT = "D:/property-ai";
const API = "http://127.0.0.1:8093/api/v1";
const PRIVATE_CONTEXT = path.join(ROOT, "tmp/delivery-auto-8093/fixture-context.json");
const PREP_STATE = path.join(ROOT, "tmp/delivery-auto-8093/five-user-overlap-prep.json");
const PUBLIC_RESULT = path.join(__dirname, "five-user-overlap-submit.json");
const AUDIO_REFERENCE = path.join(ROOT, "reports/real-ai-integration-027/audio-reference.json");
const ASR_START_FREE_RAM_MIB = 11264;

const image = (name) => path.join(ROOT, "reports/auth-identity-ai-001/local-qwen/repair-images", name);
const context = JSON.parse(fs.readFileSync(PRIVATE_CONTEXT, "utf8"));
const audioReference = JSON.parse(fs.readFileSync(AUDIO_REFERENCE, "utf8"));

if (context.phase !== "PASSWORDS_READY") throw new Error("fixture passwords are not ready");
if (fs.existsSync(PUBLIC_RESULT)) throw new Error("public result already exists; refusing a second submission run");

const specs = [
  {
    key: "residentA1", propertyId: context.organizations[0].property_id, houseId: context.organizations[0].house_ids[0],
    marker: `${context.run_id}-OVERLAP-A1`, text: "客厅天花板出现水渍和局部起皮，请维修组检查渗水来源并处理。",
    media: [{ file: image("ceiling-peeling.jpg"), mime: "image/jpeg", name: "repair-ceiling.jpg" }],
  },
  {
    key: "residentA2", propertyId: context.organizations[0].property_id, houseId: context.organizations[0].house_ids[1],
    marker: `${context.run_id}-OVERLAP-A2`, text: "厨房阀门接口缓慢滴水，请维修组检查并处理。",
    media: [{ file: image("ans-ball-valve.jpg"), mime: "image/jpeg", name: "repair-valve.jpg" }],
  },
  {
    key: "residentA3", propertyId: context.organizations[0].property_id, houseId: context.organizations[0].house_ids[2],
    marker: `${context.run_id}-OVERLAP-A3`, text: "卧室墙角渗水并有墙皮起泡，请维修组联系检查。",
    media: [{ file: image("ceiling-peeling.jpg"), mime: "image/jpeg", name: "repair-wall.jpg" }],
  },
  {
    key: "residentB1", propertyId: context.organizations[1].property_id, houseId: context.organizations[1].house_ids[0],
    marker: `${context.run_id}-OVERLAP-B1`, text: "卫生间阀门开关费力且有少量渗水，请维修组检查。",
    media: [{ file: image("ans-ball-valve.jpg"), mime: "image/jpeg", name: "repair-bath-valve.jpg" }],
  },
  {
    key: "residentB2", propertyId: context.organizations[1].property_id, houseId: context.organizations[1].house_ids[1],
    marker: `${context.run_id}-OVERLAP-B2`, text: "厨房水龙头关紧后仍滴水，没有积水，请维修组检查。语音为本次同一问题说明。",
    media: [
      { file: image("ans-ball-valve.jpg"), mime: "image/jpeg", name: "repair-kitchen-valve.jpg" },
      { file: audioReference.path, mime: "audio/wav", name: "repair-reference.wav", expectedSha256: audioReference.sha256 },
    ],
  },
];

function redact(message) {
  return String(message || "")
    .replace(/\+?86\d{11}/g, "[REDACTED_PHONE]")
    .replace(/eyJ[\w-]+\.[\w-]+\.[\w-]+/g, "[REDACTED_TOKEN]");
}

async function request(method, pathname, { token, body, headers = {}, expected = 200 } = {}) {
  const response = await fetch(`${API}${pathname}`, {
    method,
    headers: {
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      "X-Request-Id": `delivery-overlap-${crypto.randomUUID()}`,
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

async function login(spec) {
  const data = await request("POST", "/auth/password/login", {
    body: { phone: context.phones[spec.key], password: context.passwords[spec.key] },
  });
  return { ...spec, token: data.access_token, userId: data.user_id };
}

async function uploadOne(session, media) {
  const bytes = fs.readFileSync(media.file);
  const sha256 = crypto.createHash("sha256").update(bytes).digest("hex").toUpperCase();
  if (media.expectedSha256 && sha256 !== media.expectedSha256.toUpperCase()) {
    throw new Error(`media hash mismatch for ${media.name}`);
  }
  const record = await request("POST", "/files", {
    token: session.token,
    body: {
      mime_type: media.mime,
      size_bytes: bytes.length,
      original_name: media.name,
      property_id: session.propertyId,
    },
  });
  const form = new FormData();
  form.append("file", new Blob([bytes], { type: media.mime }), media.name);
  const response = await fetch(`${API}/files/${record.id}/content`, {
    method: "POST",
    headers: { Authorization: `Bearer ${session.token}`, "X-Request-Id": `delivery-upload-${crypto.randomUUID()}` },
    body: form,
  });
  const raw = await response.text();
  if (response.status !== 200) throw new Error(`upload file=${record.id} status=${response.status} body=${raw.slice(0, 160)}`);
  return { id: record.id, mime: media.mime, sha256 };
}

async function prepare() {
  if (fs.existsSync(PREP_STATE)) {
    const saved = JSON.parse(fs.readFileSync(PREP_STATE, "utf8"));
    if (saved.run_id !== context.run_id || saved.items.length !== 5) throw new Error("invalid saved preparation state");
    return Promise.all(saved.items.map(async (item) => ({ ...specs.find((spec) => spec.key === item.key), ...(await login(specs.find((spec) => spec.key === item.key))), mediaFiles: item.mediaFiles })));
  }
  const sessions = await Promise.all(specs.map(login));
  const prepared = [];
  for (const session of sessions) {
    const mediaFiles = [];
    for (const media of session.media) mediaFiles.push(await uploadOne(session, media));
    prepared.push({ ...session, mediaFiles });
  }
  fs.writeFileSync(PREP_STATE, `${JSON.stringify({ run_id: context.run_id, items: prepared.map(({ key, userId, propertyId, houseId, marker, mediaFiles }) => ({ key, userId, propertyId, houseId, marker, mediaFiles })) }, null, 2)}\n`);
  return prepared;
}

async function main() {
  const prepared = await prepare();
  if (process.argv.includes("--prepare-only")) {
    console.log(JSON.stringify({
      prepared: prepared.length,
      users: prepared.map((item) => item.key),
      file_counts: prepared.map((item) => item.mediaFiles.length),
      work_orders_created: 0,
    }));
    return;
  }
  const freeRamMiB = Math.floor(os.freemem() / 1024 / 1024);
  if (freeRamMiB < ASR_START_FREE_RAM_MIB) {
    throw new Error(
      `ASR resource guard: free RAM ${freeRamMiB} MiB is below ${ASR_START_FREE_RAM_MIB} MiB; no work order submitted`,
    );
  }
  const release = {};
  release.promise = new Promise((resolve) => { release.go = resolve; });
  const submissions = prepared.map((item) => (async () => {
    await release.promise;
    const started = performance.timeOrigin + performance.now();
    const result = await request("POST", "/work-orders", {
      token: item.token,
      headers: { "Idempotency-Key": `delivery-overlap-${context.run_id}-${item.key}` },
      body: {
        house_id: item.houseId,
        contact_phone: context.phones[item.key],
        text: `${item.marker}：${item.text}`,
        file_ids: item.mediaFiles.map((file) => file.id),
      },
    });
    const finished = performance.timeOrigin + performance.now();
    return {
      key: item.key,
      user_id: item.userId,
      tenant_id: context.organizations.find((org) => org.property_id === item.propertyId).tenant_id,
      property_id: item.propertyId,
      house_id: item.houseId,
      marker_sha256: crypto.createHash("sha256").update(item.marker).digest("hex"),
      file_ids: item.mediaFiles.map((file) => file.id),
      media_types: item.mediaFiles.map((file) => file.mime),
      work_order_id: result.id || result.work_order?.id,
      status: result.status || result.work_order?.status,
      started_at: new Date(started).toISOString(),
      finished_at: new Date(finished).toISOString(),
      duration_ms: Number((finished - started).toFixed(3)),
      _started_ms: started,
      _finished_ms: finished,
    };
  })());
  release.go();
  const settled = await Promise.allSettled(submissions);
  const failures = settled.filter((item) => item.status === "rejected");
  const items = settled.filter((item) => item.status === "fulfilled").map((item) => item.value);
  const overlapConfirmed = items.length === 5 && Math.max(...items.map((item) => item._started_ms)) < Math.min(...items.map((item) => item._finished_ms));
  const result = {
    task_id: "REAL-AI-INTEGRATION-027-DELIVERY-AUTO-OVERLAP",
    run_id: context.run_id,
    captured_at: new Date().toISOString(),
    environment: "8093/property_ai_delivery_check@0014",
    interaction: "five normal password-authenticated users; real file APIs followed by concurrent real work-order API submissions; not page clicks",
    submitted: items.length,
    overlap_confirmed: overlapConfirmed,
    overlap_span_ms: items.length ? Number((Math.min(...items.map((item) => item._finished_ms)) - Math.max(...items.map((item) => item._started_ms))).toFixed(3)) : 0,
    items: items.map(({ _started_ms, _finished_ms, ...item }) => item),
    failures: failures.map((item) => redact(item.reason?.message || item.reason)),
    boundaries: {
      page_clicks: 0,
      direct_database_business_write: false,
      manual_enqueue_or_assign: false,
      media_uploads_completed_before_barrier: true,
      work_order_idempotency_keys_are_stable: true,
      credentials_or_tokens_saved: false,
    },
  };
  fs.writeFileSync(PUBLIC_RESULT, `${JSON.stringify(result, null, 2)}\n`);
  console.log(JSON.stringify({ submitted: result.submitted, overlap_confirmed: result.overlap_confirmed, overlap_span_ms: result.overlap_span_ms, work_order_ids: result.items.map((item) => item.work_order_id), failures: result.failures }));
  if (failures.length || items.length !== 5 || !overlapConfirmed) process.exitCode = 2;
}

main().catch((error) => {
  console.error(redact(error && (error.stack || error.message) || error));
  process.exitCode = 1;
});
