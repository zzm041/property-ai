const { BASE_URL } = require("../config/env");
const auth = require("./auth");
const { isUnauthorized, messageFromError } = require("../utils/error");

const CREDENTIAL_AUTH_PATHS = {
  "/auth/wechat/login": true,
  "/auth/wechat/bind": true,
  "/auth/wechat/rebind": true,
  "/auth/challenge": true,
  "/auth/sms/send": true,
  "/auth/sms/login": true,
  "/auth/sms/test-inbox": true,
  "/auth/password/login": true,
  "/auth/phone/change": true,
  "/auth/password/reset/verify": true,
  "/auth/password/reset/confirm": true,
  "/auth/token/refresh": true,
  "/auth/dev/token": true,
  "/auth/logout": true,
};

function sendUnauthorizedToLogin() {
  auth.clearSession();
  wx.reLaunch({ url: "/pages/login/login" });
}

let refreshInFlight = null;

function refreshSessionOnce() {
  if (refreshInFlight) {
    return refreshInFlight;
  }
  const refreshToken = auth.getRefreshToken();
  if (!refreshToken) {
    return Promise.reject(new Error("请重新登录"));
  }
  refreshInFlight = new Promise((resolve, reject) => {
    wx.request({
      url: `${BASE_URL}/auth/token/refresh`,
      method: "POST",
      header: { "Content-Type": "application/json" },
      data: { refresh_token: refreshToken },
      success: (response) => {
        const payload = response.data || {};
        if (response.statusCode >= 400) {
          reject(new Error(messageFromError(payload, "请重新登录")));
          return;
        }
        const data = payload.data || {};
        if (!data.access_token) {
          reject(new Error("请重新登录"));
          return;
        }
        auth.setToken(data.access_token);
        if (data.refresh_token) {
          auth.setRefreshToken(data.refresh_token);
        }
        resolve(data);
      },
      fail: () => reject(new Error("网络不可用")),
    });
  }).finally(() => {
    refreshInFlight = null;
  });
  return refreshInFlight;
}

function request({ path, method, data, headers, retried }) {
  if (!BASE_URL) {
    return Promise.reject(new Error("未配置 API 地址"));
  }
  const token = auth.getToken();
  const verb = method || "GET";
  const options = {
    url: `${BASE_URL}${path}`,
    method: verb,
    header: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(headers || {}),
    },
  };
  if (data !== undefined) {
    options.data = data;
  } else if (verb !== "GET") {
    options.data = {};
  }
  return new Promise((resolve, reject) => {
    wx.request({
      ...options,
      success: (response) => {
        const payload = response.data || {};
        if (isUnauthorized(response.statusCode, payload)) {
          if (CREDENTIAL_AUTH_PATHS[path]) {
            reject(new Error(messageFromError(payload, "认证失败")));
            return;
          }
          const latestToken = auth.getToken();
          if (!retried && latestToken && latestToken !== token) {
            request({ path, method, data, headers, retried: true })
              .then(resolve)
              .catch(reject);
            return;
          }
          if (!retried && auth.getRefreshToken()) {
            refreshSessionOnce()
              .then(() =>
                request({ path, method, data, headers, retried: true })
              )
              .then(resolve)
              .catch(() => {
                sendUnauthorizedToLogin();
                reject(new Error("请重新登录"));
              });
            return;
          }
          sendUnauthorizedToLogin();
          reject(new Error("请重新登录"));
          return;
        }
        if (response.statusCode === 403) {
          reject(new Error(messageFromError(payload, "无权限")));
          return;
        }
        if (response.statusCode >= 400) {
          reject(new Error(messageFromError(payload, "请求失败")));
          return;
        }
        resolve(payload.data);
      },
      fail: () => reject(new Error("网络不可用")),
    });
  });
}

function uploadFile({ fileId, filePath, retried }) {
  if (!BASE_URL) {
    return Promise.reject(new Error("未配置 API 地址"));
  }
  const token = auth.getToken();
  return new Promise((resolve, reject) => {
    wx.uploadFile({
      url: `${BASE_URL}/files/${fileId}/content`,
      filePath,
      name: "file",
      header: token ? { Authorization: `Bearer ${token}` } : {},
      success: (response) => {
        let payload = {};
        try {
          payload = JSON.parse(response.data || "{}");
        } catch (error) {
          reject(new Error("上传响应无效"));
          return;
        }
        if (isUnauthorized(response.statusCode, payload)) {
          const latestToken = auth.getToken();
          if (!retried && latestToken && latestToken !== token) {
            uploadFile({ fileId, filePath, retried: true })
              .then(resolve)
              .catch(reject);
            return;
          }
          if (!retried && auth.getRefreshToken()) {
            refreshSessionOnce()
              .then(() => uploadFile({ fileId, filePath, retried: true }))
              .then(resolve)
              .catch(() => {
                sendUnauthorizedToLogin();
                reject(new Error("请重新登录"));
              });
            return;
          }
          sendUnauthorizedToLogin();
          reject(new Error("请重新登录"));
          return;
        }
        if (response.statusCode >= 400) {
          reject(new Error(messageFromError(payload, "上传失败")));
          return;
        }
        resolve(payload.data);
      },
      fail: () => reject(new Error("上传失败，请检查网络后重试")),
    });
  });
}

function downloadFile({ fileId, retried }) {
  if (!BASE_URL) {
    return Promise.reject(new Error("未配置 API 地址"));
  }
  const token = auth.getToken();
  return new Promise((resolve, reject) => {
    wx.downloadFile({
      url: `${BASE_URL}/files/${fileId}/content`,
      header: token ? { Authorization: `Bearer ${token}` } : {},
      success: (response) => {
        if (response.statusCode === 401) {
          const latestToken = auth.getToken();
          if (!retried && latestToken && latestToken !== token) {
            downloadFile({ fileId, retried: true }).then(resolve).catch(reject);
            return;
          }
          if (!retried && auth.getRefreshToken()) {
            refreshSessionOnce()
              .then(() => downloadFile({ fileId, retried: true }))
              .then(resolve)
              .catch(() => {
                sendUnauthorizedToLogin();
                reject(new Error("请重新登录"));
              });
            return;
          }
          sendUnauthorizedToLogin();
          reject(new Error("请重新登录"));
          return;
        }
        if (response.statusCode >= 400 || !response.tempFilePath) {
          reject(new Error("附件读取失败"));
          return;
        }
        resolve(response.tempFilePath);
      },
      fail: () => reject(new Error("附件读取失败，请检查网络后重试")),
    });
  });
}

function registerAndUpload({ filePath, mimeType, sizeBytes, originalName, propertyId }) {
  return request({
    path: "/files",
    method: "POST",
    data: {
      mime_type: mimeType,
      size_bytes: sizeBytes,
      original_name: originalName,
      property_id: propertyId || undefined,
    },
  }).then((file) => uploadFile({ fileId: file.id, filePath }).then(() => file));
}

module.exports = {
  request,
  uploadFile,
  downloadFile,
  registerAndUpload,
  wechatLogin: (code) => request({ path: "/auth/wechat/login", method: "POST", data: { code } }),
  wechatBind: (code) => request({ path: "/auth/wechat/bind", method: "POST", data: { code } }),
  wechatRebind: (body) =>
    request({ path: "/auth/wechat/rebind", method: "POST", data: body }),
  issueChallenge: (phone, purpose) =>
    request({ path: "/auth/challenge", method: "POST", data: { phone, purpose: purpose || "login" } }),
  sendSms: (phone, purpose, challengeId, challengeAnswer) =>
    request({
      path: "/auth/sms/send",
      method: "POST",
      data: {
        phone,
        purpose: purpose || "login",
        challenge_id: challengeId,
        challenge_answer: challengeAnswer,
      },
    }),
  smsLogin: (phone, code) =>
    request({ path: "/auth/sms/login", method: "POST", data: { phone, code } }),
  passwordLogin: (phone, password) =>
    request({ path: "/auth/password/login", method: "POST", data: { phone, password } }),
  changePhone: (body) =>
    request({ path: "/auth/phone/change", method: "POST", data: body }),
  verifyPasswordReset: (phone, code) =>
    request({ path: "/auth/password/reset/verify", method: "POST", data: { phone, code } }),
  confirmPasswordReset: (resetToken, newPassword, confirmPassword) =>
    request({
      path: "/auth/password/reset/confirm",
      method: "POST",
      data: {
        reset_token: resetToken,
        new_password: newPassword,
        confirm_password: confirmPassword,
      },
    }),
  refreshToken: (refreshToken) =>
    request({ path: "/auth/token/refresh", method: "POST", data: { refresh_token: refreshToken } }),
  logout: (refreshToken) =>
    request({ path: "/auth/logout", method: "POST", data: { refresh_token: refreshToken || undefined } }),
  updateProfile: (displayName) =>
    request({ path: "/me/profile", method: "PATCH", data: { display_name: displayName } }),
  catalogProperties: (afterId) =>
    request({
      path: `/properties?limit=100${afterId ? `&after_id=${afterId}` : ""}`,
    }),
  catalogBuildings: (propertyId) => request({ path: `/properties/${propertyId}/buildings` }),
  catalogUnits: (buildingId) => request({ path: `/buildings/${buildingId}/units` }),
  catalogHouses: (unitId) => request({ path: `/units/${unitId}/houses` }),
  claimEmployee: (token) =>
    request({ path: "/staff/employee-claim", method: "POST", data: { token } }),
  devToken: (seedKey) =>
    request({ path: "/auth/dev/token", method: "POST", data: { seed_key: seedKey } }),
  me: () => request({ path: "/me" }),
  houses: () => request({ path: "/me/houses" }),
  myBindRequests: () => request({ path: "/resident-bind-requests/mine" }),
  createBindRequest: (body) =>
    request({ path: "/resident-bind-requests", method: "POST", data: body }),
  createWorkOrder: (body) =>
    request({
      path: "/work-orders",
      method: "POST",
      data: body,
      headers: { "Idempotency-Key": `mp-${Date.now()}-${Math.random().toString(16).slice(2)}` },
    }),
  listWorkOrders: () => request({ path: "/work-orders" }),
  workOrder: (id) => request({ path: `/work-orders/${id}` }),
  confirmWorkOrder: (id, body = {}) =>
    request({ path: `/work-orders/${id}/confirm`, method: "POST", data: body }),
  reopenWorkOrder: (id, body) =>
    request({ path: `/work-orders/${id}/reopen`, method: "POST", data: body }),
  announcements: (propertyId) =>
    request({ path: `/announcements?property_id=${propertyId}` }),
  queryAssistant: (body) =>
    request({ path: "/assistant/query", method: "POST", data: body }),
  notifications: () => request({ path: "/notifications" }),
  readNotification: (id) =>
    request({ path: `/notifications/${id}/read`, method: "POST", data: {} }),
  checkIn: (propertyId) =>
    request({
      path: "/staff/attendance/checkin",
      method: "POST",
      data: { property_id: propertyId },
    }),
  checkOut: (propertyId) =>
    request({
      path: "/staff/attendance/checkout",
      method: "POST",
      data: { property_id: propertyId },
    }),
  staffWorkOrders: () => request({ path: "/staff/work-orders" }),
  acceptWorkOrder: (id, body = {}) =>
    request({ path: `/staff/work-orders/${id}/accept`, method: "POST", data: body }),
  rejectWorkOrder: (id, body) =>
    request({ path: `/staff/work-orders/${id}/reject`, method: "POST", data: body }),
  startWorkOrder: (id, body = {}) =>
    request({ path: `/staff/work-orders/${id}/start`, method: "POST", data: body }),
  feedbackWorkOrder: (id, body) =>
    request({ path: `/staff/work-orders/${id}/feedback`, method: "POST", data: body }),
  resolveWorkOrder: (id, body) =>
    request({ path: `/staff/work-orders/${id}/receipts`, method: "POST", data: body }),
  escalateWorkOrder: (id, body) =>
    request({ path: `/staff/work-orders/${id}/escalate`, method: "POST", data: body }),
  supervisorQueue: (propertyId, afterId) =>
    request({
      path: `/supervisor/work-orders?property_id=${propertyId}${
        afterId ? `&after_id=${afterId}` : ""
      }`,
    }),
  assignWorkOrder: (id, body) =>
    request({ path: `/supervisor/work-orders/${id}/assign`, method: "POST", data: body }),
  reassignWorkOrder: (id, body) =>
    request({
      path: `/supervisor/work-orders/${id}/decide`,
      method: "POST",
      data: { decision: "REASSIGN", ...body },
    }),
  decideWorkOrder: (id, body) =>
    request({ path: `/supervisor/work-orders/${id}/decide`, method: "POST", data: body }),
  analyzeWorkOrder: (id) =>
    request({ path: `/supervisor/work-orders/${id}/analyze`, method: "POST", data: {} }),
  workOrderRecommendations: (id) =>
    request({ path: `/supervisor/work-orders/${id}/recommendations` }),
  incidents: (propertyId) =>
    request({ path: `/supervisor/incidents?property_id=${propertyId}` }),
  createAnnouncement: (body) =>
    request({ path: "/admin/announcements", method: "POST", data: body }),
  submitAnnouncementReview: (id) =>
    request({
      path: `/admin/announcements/${id}/submit-review`,
      method: "POST",
      data: {},
    }),
  publishAnnouncement: (id, body) =>
    request({
      path: `/admin/announcements/${id}/publish`,
      method: "POST",
      data: body || { decision: "APPROVE" },
    }),
  bindRequests: (propertyId, tenantId) =>
    request({
      path: `/admin/resident-bind-requests?property_id=${propertyId}&tenant_id=${tenantId}`,
    }),
  approveBindRequest: (id) =>
    request({
      path: `/admin/resident-bind-requests/${id}/approve`,
      method: "POST",
      data: {},
    }),
  rejectBindRequest: (id, reason) =>
    request({
      path: `/admin/resident-bind-requests/${id}/reject`,
      method: "POST",
      data: { reason },
    }),
  employees: (propertyId, tenantId, afterId, includeHistorical) =>
    request({
      path: `/admin/employees?property_id=${propertyId}&tenant_id=${tenantId}&limit=100${
        afterId ? `&after_id=${afterId}` : ""
      }${includeHistorical ? "&include_historical=true" : ""}`,
    }),
  employeeCatalog: (propertyId, tenantId) =>
    request({
      path: `/admin/employee-catalog?property_id=${propertyId}&tenant_id=${tenantId}`,
    }),
  createEmployee: (body) =>
    request({ path: "/admin/employees", method: "POST", data: body }),
  updateEmployee: (id, body) =>
    request({ path: `/admin/employees/${id}`, method: "PATCH", data: body }),
  createDuty: (body) =>
    request({ path: "/admin/duties", method: "POST", data: body }),
  createSkill: (body) =>
    request({ path: "/admin/skills", method: "POST", data: body }),
  issueEmployeeInvite: (id, phone) =>
    request({
      path: `/admin/employees/${id}/invites`,
      method: "POST",
      data: { phone },
    }),
  assignEmployeeProperty: (id, propertyId) =>
    request({
      path: `/admin/employees/${id}/property-assignments`,
      method: "POST",
      data: { property_id: propertyId },
    }),
  endEmployeeProperty: (employeeId, assignmentId) =>
    request({
      path: `/admin/employees/${employeeId}/property-assignments/${assignmentId}/end`,
      method: "POST",
      data: {},
    }),
  propertyArchive: (propertyId, tenantId) =>
    request({
      path: `/admin/property-archive?property_id=${propertyId}&tenant_id=${tenantId}`,
    }),
  createBuilding: (body) =>
    request({ path: "/admin/buildings", method: "POST", data: body }),
  createUnit: (body) =>
    request({ path: "/admin/units", method: "POST", data: body }),
  createHouse: (body) =>
    request({ path: "/admin/houses", method: "POST", data: body }),
  updateHouse: (id, body) =>
    request({ path: `/admin/houses/${id}`, method: "PATCH", data: body }),
  importHouses: (body) =>
    request({ path: "/admin/houses/import", method: "POST", data: body }),
  exportHouses: (propertyId, tenantId) =>
    request({
      path: `/admin/houses/export?property_id=${propertyId}&tenant_id=${tenantId}`,
    }),
  residentRelationHistory: (propertyId, tenantId, afterId, houseId) =>
    request({
      path: `/admin/resident-house-relations?property_id=${propertyId}&tenant_id=${tenantId}&limit=100${
        afterId ? `&after_id=${afterId}` : ""
      }${houseId ? `&house_id=${houseId}` : ""}`,
    }),
  endResidentRelation: (id, status) =>
    request({
      path: `/admin/resident-house-relations/${id}/end`,
      method: "POST",
      data: { status: status || "ENDED" },
    }),
  staffDaily: (propertyId, tenantId, afterId) =>
    request({
      path: `/supervisor/staff/daily?property_id=${propertyId}&tenant_id=${tenantId}&limit=100${
        afterId ? `&after_id=${afterId}` : ""
      }`,
    }),
};
