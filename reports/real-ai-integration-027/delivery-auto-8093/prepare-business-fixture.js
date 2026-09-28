"use strict";

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..", "..", "..");
const API = "http://127.0.0.1:8093/api/v1";
const TEST_AUTH_API = "http://127.0.0.1:8094/api/v1";
const PRIVATE_DIR = path.join(ROOT, "tmp", "delivery-auto-8093");
const PRIVATE_PATH = path.join(PRIVATE_DIR, "fixture-context.json");
const PUBLIC_PATH = path.join(__dirname, "fixture-public.json");
const BOOTSTRAP_PATH = path.join(ROOT, "deploy", ".env.delivery.bootstrap");

function parseEnv(file) {
  const result = {};
  for (const line of fs.readFileSync(file, "utf8").split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const index = trimmed.indexOf("=");
    if (index > 0) result[trimmed.slice(0, index)] = trimmed.slice(index + 1);
  }
  return result;
}

function phoneHash(phone) {
  return crypto.createHash("sha256").update(phone).digest("hex").slice(0, 16).toUpperCase();
}

function savePrivate(context) {
  fs.mkdirSync(PRIVATE_DIR, { recursive: true });
  fs.writeFileSync(PRIVATE_PATH, `${JSON.stringify(context, null, 2)}\n`, "utf8");
}

function savePublic(context, status = "PREPARED") {
  const publicData = {
    task_id: "REAL-AI-INTEGRATION-027-DELIVERY-AUTO",
    run_id: context.run_id,
    status,
    environment: "8093/property-ai-delivery-check@0014",
    objects: {
      organizations: (context.organizations || []).map((item) => ({
        tenant_id: item.tenant_id,
        property_id: item.property_id,
        manager_user_id: item.manager_user_id,
        building_id: item.building_id,
        unit_id: item.unit_id,
        house_ids: item.house_ids,
        employee_ids: item.employee_ids,
        resident_user_ids: item.resident_user_ids,
      })),
      phone_sha256_16: Object.fromEntries(
        Object.entries(context.phones || {}).map(([key, value]) => [key, phoneHash(value)])
      ),
    },
    preparation: {
      normal_authenticated_sessions: true,
      organization_business_api: true,
      property_business_api: true,
      employee_invite_and_claim: true,
      resident_bind_and_approval: true,
      staff_checkin: true,
      direct_database_business_write: false,
      work_orders_created: 0,
      model_requests: 0,
    },
    redaction: "No phone, password, token, OTP, invite token, or resident text is stored here.",
  };
  fs.writeFileSync(PUBLIC_PATH, `${JSON.stringify(publicData, null, 2)}\n`, "utf8");
}

async function request(method, pathname, { token, body, headers = {}, expected = 200, label, base = API }) {
  const response = await fetch(`${base}${pathname}`, {
    method,
    headers: {
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      "X-Request-Id": `${label || "delivery-auto"}-${crypto.randomUUID()}`,
      ...headers,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let payload = {};
  const raw = await response.text();
  if (raw) payload = JSON.parse(raw);
  if (response.status !== expected) {
    const error = payload && payload.error ? `${payload.error.code}:${payload.error.message}` : "unexpected response";
    throw new Error(`${method} ${pathname} returned ${response.status}, expected ${expected}: ${error}`);
  }
  return payload.data;
}

function challengeAnswer(prompt) {
  const match = String(prompt || "").match(/^(\d+)\s*\+\s*(\d+)\s*=\s*\?$/);
  if (!match) throw new Error("Unsupported visual challenge prompt");
  return String(Number(match[1]) + Number(match[2]));
}

async function smsLogin(phone, label) {
  const challenge = await request("POST", "/auth/challenge", {
    body: { phone, purpose: "login" }, label: `${label}-challenge`, base: TEST_AUTH_API,
  });
  await request("POST", "/auth/sms/send", {
    body: {
      phone,
      purpose: "login",
      challenge_id: challenge.challenge_id,
      challenge_answer: challengeAnswer(challenge.prompt),
    },
    label: `${label}-send`, base: TEST_AUTH_API,
  });
  const inbox = await request("POST", "/auth/sms/test-inbox", {
    body: { phone, purpose: "login" }, label: `${label}-inbox`, base: TEST_AUTH_API,
  });
  return request("POST", "/auth/sms/login", {
    body: { phone, code: inbox.code }, label: `${label}-login`, base: TEST_AUTH_API,
  });
}

async function resetPassword(phone, label) {
  const challenge = await request("POST", "/auth/challenge", {
    body: { phone, purpose: "reset" }, label: `${label}-challenge`, base: TEST_AUTH_API,
  });
  await request("POST", "/auth/sms/send", {
    body: {
      phone,
      purpose: "reset",
      challenge_id: challenge.challenge_id,
      challenge_answer: challengeAnswer(challenge.prompt),
    },
    label: `${label}-send`, base: TEST_AUTH_API,
  });
  const inbox = await request("POST", "/auth/sms/test-inbox", {
    body: { phone, purpose: "reset" }, label: `${label}-inbox`, base: TEST_AUTH_API,
  });
  const verified = await request("POST", "/auth/password/reset/verify", {
    body: { phone, code: inbox.code }, label: `${label}-verify`, base: TEST_AUTH_API,
  });
  const password = `a1!${crypto.randomBytes(18).toString("base64url")}`;
  await request("POST", "/auth/password/reset/confirm", {
    body: { reset_token: verified.reset_token, new_password: password, confirm_password: password },
    label: `${label}-confirm`, base: TEST_AUTH_API,
  });
  await passwordLogin(phone, password, `${label}-password-check`);
  return password;
}

async function passwordLogin(phone, password, label) {
  return request("POST", "/auth/password/login", {
    body: { phone, password }, label,
  });
}

function freshContext() {
  const run = crypto.randomBytes(5).toString("hex");
  const base = Number.parseInt(crypto.randomBytes(4).toString("hex"), 16) % 90000000 + 10000000;
  const phones = {};
  const names = ["managerA", "managerB", "staffA1", "staffA2", "staffB1",
    "residentA1", "residentA2", "residentA3", "residentB1", "residentB2"];
  names.forEach((name, index) => {
    phones[name] = `+86138${String((base + index) % 100000000).padStart(8, "0")}`;
  });
  return {
    format: "property-ai-delivery-auto-fixture-v1",
    run_id: `delivery-auto-${run}`,
    created_at: new Date().toISOString(),
    phase: "NEW",
    phones,
    sessions: {},
    organizations: [],
  };
}

async function createOrganization(context, systemToken, spec, index) {
  const managerSession = await smsLogin(context.phones[spec.manager], `${context.run_id}-${spec.manager}`);
  context.sessions[spec.manager] = managerSession;
  savePrivate(context);
  const organization = await request("POST", "/system/organizations", {
    token: systemToken,
    headers: { "Idempotency-Key": `${context.run_id}-organization-${index}` },
    body: {
      tenant_name: `合成交付租户-${context.run_id}-${index}`,
      property_name: `合成交付小区-${context.run_id}-${index}`,
      property_address: `合成地址 ${context.run_id} ${index}`,
      timezone: "Asia/Shanghai",
      initial_admin_phone: context.phones[spec.manager],
    },
    label: `${context.run_id}-organization-${index}`,
  });
  const manager = await smsLogin(context.phones[spec.manager], `${context.run_id}-${spec.manager}-scoped`);
  context.sessions[spec.manager] = manager;
  const token = manager.access_token;
  const tenantId = organization.tenant_id;
  const propertyId = organization.property_id;
  const building = await request("POST", "/admin/buildings", {
    token,
    body: { tenant_id: tenantId, property_id: propertyId, code: "A", name: "合成楼栋A" },
    label: `${context.run_id}-building-${index}`,
  });
  const unit = await request("POST", "/admin/units", {
    token,
    body: { tenant_id: tenantId, property_id: propertyId, building_id: building.id, code: "U1", name: "合成一单元" },
    label: `${context.run_id}-unit-${index}`,
  });
  const houses = [];
  for (let houseIndex = 0; houseIndex < spec.residents.length; houseIndex += 1) {
    houses.push(await request("POST", "/admin/houses", {
      token,
      body: {
        tenant_id: tenantId,
        property_id: propertyId,
        unit_id: unit.id,
        room_no: `${index}${String(houseIndex + 1).padStart(2, "0")}`,
        floor: index,
        status: "ACTIVE",
      },
      label: `${context.run_id}-house-${index}-${houseIndex}`,
    }));
  }
  const duty = await request("POST", "/admin/duties", {
    token,
    body: { tenant_id: tenantId, property_id: propertyId, code: "MAINTENANCE", name: "统一维修组" },
    label: `${context.run_id}-duty-${index}`,
  });
  const employeeIds = [];
  for (let staffIndex = 0; staffIndex < spec.staff.length; staffIndex += 1) {
    const staffKey = spec.staff[staffIndex];
    const employee = await request("POST", "/admin/employees", {
      token,
      body: {
        tenant_id: tenantId,
        property_id: propertyId,
        employee_no: `${context.run_id.slice(-6).toUpperCase()}-${index}-${staffIndex + 1}`,
        duty_ids: [duty.id],
        skill_ids: [],
        max_capacity: 1,
      },
      label: `${context.run_id}-employee-${index}-${staffIndex}`,
    });
    const invite = await request("POST", `/admin/employees/${employee.id}/invites`, {
      token,
      body: { phone: context.phones[staffKey] },
      label: `${context.run_id}-invite-${index}-${staffIndex}`,
    });
    const staffSession = await smsLogin(context.phones[staffKey], `${context.run_id}-${staffKey}`);
    await request("POST", "/staff/employee-claim", {
      token: staffSession.access_token,
      body: { token: invite.invite_token },
      label: `${context.run_id}-claim-${index}-${staffIndex}`,
    });
    const scopedStaff = await smsLogin(context.phones[staffKey], `${context.run_id}-${staffKey}-scoped`);
    context.sessions[staffKey] = scopedStaff;
    await request("POST", "/staff/attendance/checkin", {
      token: scopedStaff.access_token,
      headers: { "X-Property-Id": String(propertyId) },
      body: { tenant_id: tenantId, property_id: propertyId },
      label: `${context.run_id}-checkin-${index}-${staffIndex}`,
    });
    employeeIds.push(employee.id);
    savePrivate(context);
  }
  const residentUserIds = [];
  for (let residentIndex = 0; residentIndex < spec.residents.length; residentIndex += 1) {
    const residentKey = spec.residents[residentIndex];
    const resident = await smsLogin(context.phones[residentKey], `${context.run_id}-${residentKey}`);
    context.sessions[residentKey] = resident;
    const bind = await request("POST", "/resident-bind-requests", {
      token: resident.access_token,
      body: {
        tenant_id: tenantId,
        property_id: propertyId,
        house_id: houses[residentIndex].id,
        relation_type: "OWNER",
      },
      label: `${context.run_id}-bind-${index}-${residentIndex}`,
    });
    await request("POST", `/admin/resident-bind-requests/${bind.id}/approve`, {
      token,
      body: {},
      label: `${context.run_id}-approve-${index}-${residentIndex}`,
    });
    const scopedResident = await smsLogin(context.phones[residentKey], `${context.run_id}-${residentKey}-scoped`);
    context.sessions[residentKey] = scopedResident;
    residentUserIds.push(scopedResident.user_id);
    savePrivate(context);
  }
  return {
    tenant_id: tenantId,
    property_id: propertyId,
    manager_user_id: manager.user_id,
    manager_key: spec.manager,
    building_id: building.id,
    unit_id: unit.id,
    house_ids: houses.map((item) => item.id),
    employee_ids: employeeIds,
    staff_keys: spec.staff,
    resident_user_ids: residentUserIds,
    resident_keys: spec.residents,
  };
}

async function main() {
  let context;
  if (fs.existsSync(PRIVATE_PATH)) {
    const existing = JSON.parse(fs.readFileSync(PRIVATE_PATH, "utf8"));
    if (existing.phase === "PASSWORDS_READY") {
      savePublic(existing);
      console.log(JSON.stringify({ run_id: existing.run_id, status: existing.phase, resumed: true }));
      return;
    }
    if (existing.phase === "PREPARED") {
      context = existing;
    } else if (existing.phase === "SYSTEM_AUTHENTICATED" && (existing.organizations || []).length === 0) {
      context = existing;
    } else {
      throw new Error(`Incomplete private fixture exists at phase ${existing.phase}; inspect before retrying`);
    }
  } else {
    context = freshContext();
    savePrivate(context);
  }
  const env = parseEnv(BOOTSTRAP_PATH);
  if (!env.BOOTSTRAP_ADMIN_PHONE || !env.BOOTSTRAP_ADMIN_PASSWORD) {
    throw new Error("Local bootstrap administrator credentials are unavailable");
  }
  if (context.phase !== "PREPARED") {
    const system = await passwordLogin(
      env.BOOTSTRAP_ADMIN_PHONE,
      env.BOOTSTRAP_ADMIN_PASSWORD,
      `${context.run_id}-system-login`
    );
    context.phase = "SYSTEM_AUTHENTICATED";
    savePrivate(context);
    const specs = [
      { manager: "managerA", staff: ["staffA1", "staffA2"], residents: ["residentA1", "residentA2", "residentA3"] },
      { manager: "managerB", staff: ["staffB1"], residents: ["residentB1", "residentB2"] },
    ];
    for (let index = 0; index < specs.length; index += 1) {
      const organization = await createOrganization(context, system.access_token, specs[index], index + 1);
      context.organizations.push(organization);
      context.phase = `ORGANIZATION_${index + 1}_PREPARED`;
      savePrivate(context);
    }
    context.phase = "PREPARED";
    savePrivate(context);
  }
  context.passwords = context.passwords || {};
  for (const [key, phone] of Object.entries(context.phones)) {
    context.passwords[key] = await resetPassword(phone, `${context.run_id}-${key}-reset`);
    savePrivate(context);
  }
  context.phase = "PASSWORDS_READY";
  savePrivate(context);
  savePublic(context);
  console.log(JSON.stringify({
    run_id: context.run_id,
    status: context.phase,
    organizations: context.organizations.map((item) => ({
      tenant_id: item.tenant_id,
      property_id: item.property_id,
      houses: item.house_ids.length,
      employees: item.employee_ids.length,
      residents: item.resident_user_ids.length,
    })),
  }));
}

main().catch((error) => {
  const message = String(error && (error.stack || error.message) || error)
    .replace(/\+86\d{11}/g, "[REDACTED_PHONE]")
    .replace(/eyJ[\w-]+\.[\w-]+\.[\w-]+/g, "[REDACTED_TOKEN]");
  console.error(message);
  process.exitCode = 1;
});
