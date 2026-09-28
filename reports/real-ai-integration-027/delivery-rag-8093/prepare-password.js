"use strict";

const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const path = require("path");

const API_BASE = "http://127.0.0.1:8094/api/v1";
const PHONE = process.env.DELIVERY_RAG_PHONE;
const PASSWORD = process.env.DELIVERY_RAG_PASSWORD;
const RUN_ID = process.env.DELIVERY_RAG_RUN_ID;
const EXPECTED_USER_ID = Number(process.env.DELIVERY_RAG_USER_ID || 0);

function request(method, pathname, body, token) {
  return new Promise((resolve, reject) => {
    const encoded = body === undefined ? null : Buffer.from(JSON.stringify(body));
    const url = new URL(`${API_BASE}${pathname}`);
    const headers = { "X-Request-Id": `${RUN_ID}-${pathname.replace(/\W+/g, "-")}` };
    if (encoded) {
      headers["Content-Type"] = "application/json";
      headers["Content-Length"] = encoded.length;
    }
    if (token) headers.Authorization = `Bearer ${token}`;
    const message = http.request(url, { method, headers }, (response) => {
      const chunks = [];
      response.on("data", (chunk) => chunks.push(chunk));
      response.on("end", () => {
        let payload;
        try {
          payload = JSON.parse(Buffer.concat(chunks).toString("utf8"));
        } catch (error) {
          reject(new Error(`invalid JSON from ${pathname}`));
          return;
        }
        if (response.statusCode !== 200) {
          const code = payload && payload.error && payload.error.code;
          reject(new Error(`${pathname} returned ${response.statusCode} ${code || "UNKNOWN"}`));
          return;
        }
        resolve(payload.data);
      });
    });
    message.on("error", reject);
    if (encoded) message.write(encoded);
    message.end();
  });
}

function solve(prompt) {
  const match = String(prompt).match(/(\d+)\s*\+\s*(\d+)/);
  if (!match) throw new Error("unsupported challenge prompt");
  return String(Number(match[1]) + Number(match[2]));
}

async function main() {
  if (!PHONE || !PASSWORD || !RUN_ID || !EXPECTED_USER_ID) {
    throw new Error("private runtime inputs are missing");
  }
  const challenge = await request("POST", "/auth/challenge", { phone: PHONE, purpose: "reset" });
  await request("POST", "/auth/sms/send", {
    phone: PHONE,
    purpose: "reset",
    challenge_id: challenge.challenge_id,
    challenge_answer: solve(challenge.prompt),
  });
  const inbox = await request("POST", "/auth/sms/test-inbox", { phone: PHONE, purpose: "reset" });
  const verified = await request("POST", "/auth/password/reset/verify", { phone: PHONE, code: inbox.code });
  await request("POST", "/auth/password/reset/confirm", {
    reset_token: verified.reset_token,
    new_password: PASSWORD,
    confirm_password: PASSWORD,
  });
  const issued = await request("POST", "/auth/password/login", { phone: PHONE, password: PASSWORD });
  const me = await request("GET", "/me", undefined, issued.access_token);
  if (Number(me.user_id) !== EXPECTED_USER_ID) throw new Error("password login resolved to another user");
  const evidence = {
    task_id: "REAL-AI-INTEGRATION-027",
    run_id: RUN_ID,
    recorded_at_utc: new Date().toISOString(),
    result: "LOCAL_SYNTHETIC_CREDENTIAL_READY",
    api: API_BASE,
    provider_boundary: "isolated Development SMS test facility; not a real SMS provider",
    user_id: me.user_id,
    roles: me.roles,
    scopes: me.scopes,
    phone_sha256_16: crypto.createHash("sha256").update(PHONE).digest("hex").slice(0, 16),
    password_login_verified: true,
    password_persisted_in_evidence: false,
  };
  fs.writeFileSync(
    path.join(__dirname, `${RUN_ID}-credential-preparation.json`),
    JSON.stringify(evidence, null, 2),
    "utf8"
  );
  process.stdout.write(JSON.stringify({
    result: evidence.result,
    user_id: evidence.user_id,
    roles: evidence.roles,
  }));
}

main().catch((error) => {
  const redacted = String(error && error.stack ? error.stack : error)
    .split(PHONE || "").join("[PRIVATE]")
    .split(PASSWORD || "").join("[PRIVATE]");
  process.stderr.write(`${redacted}\n`);
  process.exitCode = 1;
});
