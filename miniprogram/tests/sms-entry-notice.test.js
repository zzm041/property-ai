const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const { createRequire } = require("module");

function pageFor(name, sendSms, overrides = {}) {
  const file = path.resolve(__dirname, `../pages/${name}/${name}.js`);
  let definition;
  const localRequire = createRequire(file);
  vm.runInNewContext(fs.readFileSync(file, "utf8"), {
    Page: (value) => { definition = value; },
    require: (requested) => requested === "../../services/api"
      ? Object.assign({ sendSms }, overrides.api || {})
      : requested === "../../services/auth" && overrides.auth
        ? overrides.auth
        : localRequire(requested),
    wx: overrides.wx || {},
    setInterval: () => 1,
    clearInterval: () => {},
  }, { filename: file });
  const page = Object.assign({}, definition, {
    data: Object.assign({}, definition.data),
    setData(update) { Object.assign(this.data, update); },
  });
  page.setData({
    phone: "13800138000", challengeId: "visual", challengePrompt: "1 + 1 = ?",
    challengeAnswer: "2",
  });
  return page;
}

async function settle() {
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
}

async function main() {
  const login = pageFor("login", async () => ({ delivery_channel: "sms", expires_in: 300 }));
  login.sendSms();
  await settle();
  assert.match(login.data.error, /已受理/);
  assert.strictEqual(login.data.cooldown, 60);

  const reset = pageFor("password-reset", async () => ({ delivery_channel: "sms" }));
  reset.sendSms();
  await settle();
  assert.match(reset.data.error, /已受理/);
  assert.doesNotMatch(reset.data.error, /开发短信/);

  const devReset = pageFor("password-reset", async () => ({ delivery_channel: "development" }));
  devReset.sendSms();
  await settle();
  assert.match(devReset.data.error, /开发/);

  const failed = pageFor("login", async () => { throw new Error("暂不可用"); });
  failed.sendSms();
  await settle();
  assert.strictEqual(failed.data.cooldown, 0);
  assert.strictEqual(failed.data.error, "暂不可用");

  const actions = [];
  const confirmed = pageFor("password-reset", async () => ({}), {
    api: { confirmPasswordReset: async () => ({ ok: true }) },
    auth: { clearSession: () => actions.push("clear") },
    wx: {
      showToast: () => actions.push("toast"),
      redirectTo: () => actions.push("redirect"),
    },
  });
  confirmed.setData({
    resetToken: "ticket", newPassword: "NewPass2Strong", confirmPassword: "NewPass2Strong",
  });
  confirmed.confirm();
  await settle();
  assert.deepStrictEqual(actions, ["clear", "toast", "redirect"]);

  const navigation = [];
  const profile = pageFor("profile", async () => ({}), {
    wx: { navigateTo: (item) => navigation.push(item.url) },
  });
  profile.goPasswordSetup();
  assert.deepStrictEqual(navigation, ["/pages/password-reset/password-reset"]);
  const profileMarkup = fs.readFileSync(
    path.resolve(__dirname, "../pages/profile/profile.wxml"), "utf8"
  );
  assert.match(profileMarkup, /bindtap="goPasswordSetup"/);
  assert.match(profileMarkup, /首次设置密码/);
  console.log("sms entry notice tests passed");
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
