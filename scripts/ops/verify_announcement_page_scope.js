"use strict";

const fs = require("fs");
const path = require("path");

global.Page = () => {};
const {
  scopeAllows,
  tenantScopeAllows,
  verifiedProperty,
} = require(path.resolve(
  __dirname,
  "../../miniprogram/pages/supervisor/announcements.js"
));

function check(value, message) {
  if (!value) throw new Error(message);
}

const property = verifiedProperty([{ id: 4, tenant_id: 4 }], 4);
const mixedGrants = [
  {
    scope_type: "TENANT",
    tenant_id: 3,
    property_id: null,
    permissions: ["announcement:draft", "announcement:publish"],
  },
  {
    scope_type: "PROPERTY",
    tenant_id: 4,
    property_id: 4,
    permissions: ["announcement:draft", "announcement:publish"],
  },
];
const tenantGrant = {
  scope_type: "TENANT",
  tenant_id: 4,
  property_id: null,
  permissions: ["announcement:draft", "announcement:publish"],
};

check(
  mixedGrants.some((scope) =>
    scopeAllows(scope, "announcement:publish", property.tenant_id, property.id)
  ),
  "matching property grant should publish in property 4"
);
check(
  !mixedGrants.some((scope) =>
    tenantScopeAllows(scope, "announcement:publish", property.tenant_id)
  ),
  "tenant 3 grant must not enable tenant-wide publishing for property 4"
);
check(
  scopeAllows(tenantGrant, "announcement:draft", property.tenant_id, property.id),
  "matching tenant grant should draft in its property"
);
check(
  tenantScopeAllows(tenantGrant, "announcement:publish", property.tenant_id),
  "matching tenant grant should publish a tenant target"
);
let missingPropertyDenied = false;
try {
  verifiedProperty([{ id: 5, tenant_id: 3 }], 4);
} catch (error) {
  missingPropertyDenied = error.message.includes("没有该小区");
}
check(missingPropertyDenied, "unverified property should be denied");

const result = {
  task_id: "ANNOUNCEMENT-GOVERNANCE-007-FIX-2",
  verification: "pure Node helper test; no API or business writes",
  cases: {
    mixed_grant_uses_matching_property_grant: true,
    mismatched_tenant_grant_cannot_enable_tenant_target: true,
    matching_tenant_grant_covers_property_and_tenant_target: true,
    absent_verified_property_denied: true,
  },
};
const payload = `${JSON.stringify(result, null, 2)}\n`;
if (process.argv[2]) {
  fs.writeFileSync(path.resolve(process.argv[2]), payload, "utf8");
}
process.stdout.write(payload);
