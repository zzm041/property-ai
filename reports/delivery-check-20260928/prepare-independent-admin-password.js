"use strict";

const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const path = require("path");

const API_BASE = "http://127.0.0.1:8094/api/v1";
const PHONE = process.env.DELIVERY_TENANT_ADMIN_PHONE;
const PASSWORD = process.env.DELIVERY_TENANT_ADMIN_PASSWORD;
const RUN_ID = process.env.DELIVERY_ORG_RUN_ID;
const EXPECTED_USER_ID = Number(process.env.DELIVERY_EXPECTED_USER_ID || 0);

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
    const req = http.request(url, { method, headers }, (res) => {
      const chunks = [];
      res.on("data", (chunk) => chunks.push(chunk));
      res.on("end", () => {
        let payload;
        try { payload = JSON.parse(Buffer.concat(chunks).toString("utf8")); }
        catch (error) { return reject(new Error(`invalid JSON from ${pathname}`)); }
        if (res.statusCode !== 200) {
          const code = payload && payload.error && payload.error.code;
          return reject(new Error(`${pathname} returned ${res.statusCode} ${code || "UNKNOWN"}`));
        }
        resolve(payload.data);
      });
    });
    req.on("error", reject);
    if (encoded) req.write(encoded);
    req.end();
  });
}

function answer(prompt) {
  const match = String(prompt).match(/(\d+)\s*\+\s*(\d+)/);
  if (!match) throw new Error("unsupported challenge prompt");
  return String(Number(match[1]) + Number(match[2]));
}

async function main() {
  if (!PHONE || !PASSWORD || !RUN_ID || !EXPECTED_USER_ID) {
    throw new Error("required private/runtime inputs are missing");
  }
  const challenge = await request("POST", "/auth/challenge", { phone: PHONE, purpose: "reset" });
  await request("POST", "/auth/sms/send", {
    phone: PHONE,
    purpose: "reset",
    challenge_id: challenge.challenge_id,
    challenge_answer: answer(challenge.prompt),
  });
  const inbox = await request("POST", "/auth/sms/test-inbox", { phone: PHONE, purpose: "reset" });
  const verified = await request("POST", "/auth/password/reset/verify", { phone: PHONE, code: inbox.code });
  await request("POST", "/auth/password/reset/confirm", {
    reset_token: verified.reset_token,
    new_password: PASSWORD,
    confirm_password: PASSWORD,
  });
  const login = await request("POST", "/auth/password/login", { phone: PHONE, password: PASSWORD });
  const me = await request("GET", "/me", undefined, login.access_token);
  if (Number(me.user_id) !== EXPECTED_USER_ID) throw new Error("password login resolved to another user");
  if ((me.roles || []).includes("SYSTEM_ADMIN")) throw new Error("account unexpectedly has SYSTEM_ADMIN");
  const evidence = {
    task_id: "DELIVERY-ORGANIZATION-ONBOARDING-028",
    run_id: RUN_ID,
    recorded_at_utc: new Date().toISOString(),
    purpose: "establish a normal password credential for the independent synthetic target account",
    api: API_BASE,
    provider_boundary: "Development SMS test facility only",
    user_id: me.user_id,
    phone_sha256_16: crypto.createHash("sha256").update(PHONE).digest("hex").slice(0, 16).toUpperCase(),
    password_login_verified: true,
    system_admin_absent_before_onboarding: true,
    roles_before_onboarding: me.roles || [],
    scopes_before_onboarding: me.scopes || [],
  };
  fs.writeFileSync(
    path.join(__dirname, `${RUN_ID}-independent-admin-prerequisite.json`),
    JSON.stringify(evidence, null, 2),
    "utf8"
  );
  process.stdout.write(JSON.stringify({ user_id: me.user_id, system_admin_absent: true }));
}

main().catch((error) => {
  process.stderr.write(`${error && error.stack ? error.stack : error}\n`);
  process.exitCode = 1;
});
