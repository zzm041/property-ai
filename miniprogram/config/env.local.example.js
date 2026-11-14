// Copy to env.local.js (not committed). Set the Mini Program API origin including /api/v1.
// Do not put App Secret, database URLs, or provider keys here.
// localPreview and identities are ignored unless WeChat envVersion is develop.
// release must stay empty of localPreview/identities.
module.exports = {
  develop: "http://127.0.0.1:8082/api/v1",
  trial: "",
  release: "",
  localPreview: true,
  identities: [
    { label: "住户", seedKey: "__DEV_RESIDENT__" },
    { label: "物业", seedKey: "__DEV_PROPERTY_MANAGER__" },
    { label: "员工", seedKey: "__DEV_STAFF__" },
  ],
  // Optional default for the bind-apply input. Look up from the current test DB; do not assume a global id.
  previewHouseId: "",
};
