function loadLocal() {
  try {
    return require("./env.local");
  } catch (error) {
    return {};
  }
}

function resolveBaseUrl() {
  const local = loadLocal();
  const configured = {
    develop: local.develop || "",
    trial: local.trial || "",
    release: local.release || "",
  };
  try {
    const env = wx.getAccountInfoSync().miniProgram.envVersion;
    return configured[env] || configured.develop;
  } catch (error) {
    return configured.develop;
  }
}

module.exports = {
  BASE_URL: resolveBaseUrl(),
};
