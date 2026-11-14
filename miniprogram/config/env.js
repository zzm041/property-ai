function loadLocal() {
  try {
    return require("./env.local");
  } catch (error) {
    return {};
  }
}

function envVersion() {
  try {
    return wx.getAccountInfoSync().miniProgram.envVersion;
  } catch (error) {
    return "";
  }
}

function resolveBaseUrl() {
  const local = loadLocal();
  const configured = {
    develop: local.develop || "",
    trial: local.trial || "",
    release: local.release || "",
  };
  const env = envVersion() || "develop";
  return configured[env] || configured.develop || "";
}

function localPreviewEnabled() {
  const local = loadLocal();
  return Boolean(local.localPreview) && envVersion() === "develop";
}

function localIdentities() {
  if (!localPreviewEnabled()) {
    return [];
  }
  const local = loadLocal();
  const rows = local.identities || [];
  return rows.filter((item) => item && item.seedKey && item.label);
}

function environmentNote() {
  const local = loadLocal();
  if (typeof local.envNote === "string") {
    return local.envNote;
  }
  return envVersion() === "release" ? "" : "测试环境 · 外部服务状态以当前运行配置为准";
}

module.exports = {
  BASE_URL: resolveBaseUrl(),
  ENV_VERSION: envVersion(),
  LOCAL_PREVIEW: localPreviewEnabled(),
  LOCAL_IDENTITIES: localIdentities(),
  PREVIEW_HOUSE_ID: localPreviewEnabled() ? localPreviewHouseId() : "",
  ENV_NOTE: environmentNote(),
};

function localPreviewHouseId() {
  const local = loadLocal();
  return local.previewHouseId ? String(local.previewHouseId) : "";
}
