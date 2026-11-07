const api = require("../../services/api");
const auth = require("../../services/auth");
const { withLoading } = require("../../utils/ui");

Page({
  data: {
    loading: false,
    error: "",
    notice: "",
    capabilities: null,
    purposes: [
      { value: "MERGE_SOURCE_PROOF", label: "证明待合并来源账号" },
      { value: "ACCOUNT_RECOVERY", label: "高风险账号恢复" },
      { value: "PHONE_REUSE", label: "新持有人号码复用" },
    ],
    purposeIndex: 0,
    phone: "",
    proofAssertion: "",
    cases: [],
    currentPassword: "",
    targetReauthAssertion: "",
    newPassword: "",
    confirmPassword: "",
    confirmReplacePhone: false,
  },
  onShow() {
    withLoading(this, () => Promise.all([
      api.identityRecoveryCapabilities(),
      api.myIdentityRecoveryCases(),
    ]).then(([capabilities, result]) => {
      const labels = require("../../utils/business-labels");
      const cases = (result.items || []).map((item) => ({
        ...item,
        purposeText: labels.identityPurpose(item.purpose),
        statusText: labels.identityStatus(item.status),
      }));
      this.setData({ capabilities, cases, error: "" });
    })).catch(() => {});
  },
  onPurpose(event) {
    this.setData({ purposeIndex: Number(event.detail.value), notice: "" });
  },
  onField(event) {
    this.setData({ [event.currentTarget.dataset.field]: event.detail.value, notice: "" });
  },
  submit() {
    if (!this.data.capabilities || !this.data.capabilities.enabled) return;
    const phone = String(this.data.phone || "").trim();
    const proofAssertion = String(this.data.proofAssertion || "").trim();
    if (phone.length < 8 || proofAssertion.length < 8) {
      this.setData({ error: "请填写联系电话和获批准核验渠道返回的一次性令牌" });
      return;
    }
    const purpose = this.data.purposes[this.data.purposeIndex].value;
    withLoading(this, () => api.createIdentityRecoveryCase({
      purpose,
      phone,
      proof_assertion: proofAssertion,
    }).then((item) => {
      this.setData({
        phone: "",
        proofAssertion: "",
        notice: `申请 #${item.id} 已进入隔离与双人审核`,
        error: "",
      });
    })).catch(() => {});
  },
  complete(event) {
    const id = Number(event.currentTarget.dataset.id);
    const item = this.data.cases.find((entry) => entry.id === id);
    if (!item || item.status !== "APPROVED") return;
    const newPassword = String(this.data.newPassword || "");
    const confirmPassword = String(this.data.confirmPassword || "");
    if (newPassword !== confirmPassword) {
      this.setData({ error: "新密码与确认密码不一致" });
      return;
    }
    if (item.purpose === "PHONE_REUSE" && this.data.confirmReplacePhone !== true) {
      this.setData({ error: "请明确确认本次号码将替换目标账号当前手机号" });
      return;
    }
    withLoading(this, () => api.completeIdentityRecoveryCase(id, {
      expected_version: item.version,
      current_password: String(this.data.currentPassword || "") || undefined,
      target_reauth_assertion: String(this.data.targetReauthAssertion || "").trim() || undefined,
      new_password: newPassword || undefined,
      confirm_password: confirmPassword || undefined,
      confirm_replace_phone: item.purpose === "PHONE_REUSE" && this.data.confirmReplacePhone === true,
    }).then((completed) => {
      this.setData({
        cases: this.data.cases.map((entry) => entry.id === completed.id ? {
          ...completed,
          purposeText: require("../../utils/business-labels").identityPurpose(completed.purpose),
          statusText: require("../../utils/business-labels").identityStatus(completed.status),
        } : entry),
        currentPassword: "",
        targetReauthAssertion: "",
        newPassword: "",
        confirmPassword: "",
        confirmReplacePhone: false,
        notice: `申请 #${completed.id} 的身份事务已完成，请重新登录`,
        error: "",
      });
      auth.clearSession();
      wx.showToast({ title: "身份事务已完成，请重新登录", icon: "none" });
      wx.reLaunch({ url: "/pages/login/login" });
    })).catch(() => {});
  },
  onReplacePhoneConfirm(event) {
    const values = Array.isArray(event.detail.value) ? event.detail.value : [];
    this.setData({ confirmReplacePhone: values.includes("replace"), notice: "" });
  },
});
