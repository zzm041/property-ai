const api = require("../../services/api");
const { withLoading } = require("../../utils/ui");

Page({
  data: {
    tenantName: "",
    tenantContact: "",
    propertyName: "",
    propertyAddress: "",
    timezone: "Asia/Shanghai",
    initialAdminPhone: "",
    created: null,
    loading: false,
    error: "",
    notice: "",
  },

  onInput(event) {
    this.setData({ [event.currentTarget.dataset.field]: event.detail.value });
  },

  submit() {
    const required = [
      this.data.tenantName,
      this.data.propertyName,
      this.data.propertyAddress,
      this.data.timezone,
      this.data.initialAdminPhone,
    ];
    if (required.some((value) => !String(value || "").trim())) {
      this.setData({ error: "请填写租户、小区、地址、时区和首位管理员手机号" });
      return;
    }
    const body = {
      tenant_name: this.data.tenantName.trim(),
      tenant_contact: this.data.tenantContact.trim() || null,
      property_name: this.data.propertyName.trim(),
      property_address: this.data.propertyAddress.trim(),
      timezone: this.data.timezone.trim(),
      initial_admin_phone: this.data.initialAdminPhone.trim(),
    };
    const fingerprint = JSON.stringify(body);
    if (!this._pendingSubmission || this._pendingSubmission.fingerprint !== fingerprint) {
      this._pendingSubmission = {
        fingerprint,
        idempotencyKey: `organization-${Date.now()}-${Math.random()
          .toString(16)
          .slice(2)}`,
      };
    }
    withLoading(this, () =>
      api
        .createOrganization(body, this._pendingSubmission.idempotencyKey)
        .then((created) => {
          this._pendingSubmission = null;
          this.setData({
            created,
            initialAdminPhone: "",
            error: "",
            notice: "物业组织已建立，首位管理员重新登录后即可进入管理范围",
          });
        })
    ).catch(() => {});
  },
});
