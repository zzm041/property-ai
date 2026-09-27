const api = require("../../services/api");
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
  },
  onShow() {
    withLoading(this, () => api.identityRecoveryCapabilities().then((capabilities) => {
      this.setData({ capabilities, error: "" });
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
});
