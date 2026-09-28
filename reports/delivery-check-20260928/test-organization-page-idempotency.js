"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const pageSource = fs.readFileSync(
  path.join(__dirname, "../../miniprogram/pages/system/organizations.js"),
  "utf8"
);
const calls = [];
let attempt = 0;
let definition;

const api = {
  createOrganization(body, idempotencyKey) {
    calls.push({ body: JSON.parse(JSON.stringify(body)), idempotencyKey });
    attempt += 1;
    if (attempt === 1) return Promise.reject(new Error("synthetic response loss"));
    return Promise.resolve({ tenant_id: attempt, property_id: attempt });
  },
};

const sandbox = {
  module: { exports: {} },
  exports: {},
  require(request) {
    if (request === "../../services/api") return api;
    if (request === "../../utils/ui") return { withLoading: (_page, operation) => operation() };
    throw new Error(`unexpected require ${request}`);
  },
  Page(value) { definition = value; },
  Date,
  Math,
  Promise,
  JSON,
  String,
};
vm.runInNewContext(pageSource, sandbox, { filename: "organizations.js" });

function instantiate() {
  const page = {
    data: {
      tenantName: "交付验收物业",
      tenantContact: "021-55550000",
      propertyName: "交付验收小区",
      propertyAddress: "上海市合成测试路 28 号",
      timezone: "Asia/Shanghai",
      initialAdminPhone: "19100000000",
      created: null,
      loading: false,
      error: "",
      notice: "",
    },
    setData(patch) { Object.assign(this.data, patch); },
  };
  for (const [name, value] of Object.entries(definition)) {
    if (typeof value === "function") page[name] = value.bind(page);
  }
  return page;
}

function settle() {
  return new Promise((resolve) => setImmediate(resolve));
}

(async () => {
  const page = instantiate();
  page.submit();
  await settle();
  assert.equal(calls.length, 1);
  assert.ok(page._pendingSubmission, "unknown response must retain pending submission");

  page.submit();
  await settle();
  assert.equal(calls.length, 2);
  assert.equal(calls[0].idempotencyKey, calls[1].idempotencyKey);
  assert.deepEqual(calls[0].body, calls[1].body);
  assert.equal(page._pendingSubmission, null, "confirmed success must clear pending submission");

  page.data.propertyName = "交付验收小区-正文已变更";
  page.data.initialAdminPhone = "19100000000";
  page.submit();
  await settle();
  assert.equal(calls.length, 3);
  assert.notEqual(calls[1].idempotencyKey, calls[2].idempotencyKey);

  process.stdout.write(JSON.stringify({
    page_source: "miniprogram/pages/system/organizations.js",
    unknown_response_same_body_reused_key: true,
    confirmed_success_cleared_pending: true,
    changed_body_used_new_key: true,
    call_count: calls.length,
  }));
})().catch((error) => {
  process.stderr.write(`${error && error.stack ? error.stack : error}\n`);
  process.exitCode = 1;
});
