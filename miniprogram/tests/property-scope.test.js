const assert = require("assert/strict");
const scope = require("../utils/property-scope");

const properties = [
  { id: 10, tenant_id: 1, name: "甲小区" },
  { id: 20, tenant_id: 1, name: "乙小区" },
  { id: 30, tenant_id: 2, name: "丙小区" },
];

const api = {
  me: () => Promise.resolve({
    scopes: [
      { scope_type: "PROPERTY", tenant_id: 1, property_id: 20, permissions: ["work_order:dispatch"] },
      { scope_type: "TENANT", tenant_id: 2, property_id: null, permissions: ["work_order:dispatch"] },
    ],
  }),
  catalogProperties: (afterId) => Promise.resolve({
    items: afterId ? [] : properties,
  }),
};

(async () => {
  const allowed = await scope.authorizedProperties(api, "work_order:dispatch");
  assert.deepEqual(allowed.map((item) => item.id), [20, 30]);
  assert.equal(scope.preferredIndex(allowed, 30), 1);
  assert.equal(scope.preferredIndex(allowed, 10), 0);
  assert.equal(scope.scopeAllows(
    { scope_type: "SYSTEM", permissions: ["announcement:read"] },
    "announcement:read",
    properties[0]
  ), true);
  console.log("property scope picker PASS");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
