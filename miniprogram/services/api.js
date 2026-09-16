const { BASE_URL } = require("../config/env");
const auth = require("./auth");
const { isUnauthorized, messageFromError } = require("../utils/error");

function request({ path, method, data, headers }) {
  if (!BASE_URL) {
    return Promise.reject(new Error("未配置 API 地址"));
  }
  const token = auth.getToken();
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${BASE_URL}${path}`,
      method: method || "GET",
      data: data || {},
      header: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(headers || {}),
      },
      success: (response) => {
        const payload = response.data || {};
        if (isUnauthorized(response.statusCode, payload)) {
          auth.clearToken();
          wx.reLaunch({ url: "/pages/login/login" });
          reject(new Error("请重新登录"));
          return;
        }
        if (response.statusCode === 403) {
          reject(new Error("无权限"));
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

module.exports = {
  request,
  wechatLogin: (code) => request({ path: "/auth/wechat/login", method: "POST", data: { code } }),
  me: () => request({ path: "/me" }),
  houses: () => request({ path: "/me/houses" }),
  createWorkOrder: (body) =>
    request({
      path: "/work-orders",
      method: "POST",
      data: body,
      headers: { "Idempotency-Key": `mp-${Date.now()}` },
    }),
  listWorkOrders: () => request({ path: "/work-orders" }),
  workOrder: (id) => request({ path: `/work-orders/${id}` }),
  confirmWorkOrder: (id) =>
    request({ path: `/work-orders/${id}/confirm`, method: "POST" }),
  reopenWorkOrder: (id, body) =>
    request({ path: `/work-orders/${id}/reopen`, method: "POST", data: body }),
  announcements: () => request({ path: "/announcements" }),
  announcement: (id) => request({ path: `/announcements/${id}` }),
  queryAssistant: (body) =>
    request({ path: "/assistant/query", method: "POST", data: body }),
  notifications: () => request({ path: "/notifications" }),
  readNotification: (id) =>
    request({ path: `/notifications/${id}/read`, method: "POST" }),
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
  acceptWorkOrder: (id) =>
    request({ path: `/staff/work-orders/${id}/accept`, method: "POST" }),
  startWorkOrder: (id) =>
    request({ path: `/staff/work-orders/${id}/start`, method: "POST" }),
  supervisorQueue: () => request({ path: "/supervisor/work-orders" }),
  assignWorkOrder: (id, body) =>
    request({ path: `/supervisor/work-orders/${id}/assign`, method: "POST", data: body }),
  incidents: () => request({ path: "/supervisor/incidents" }),
  createAnnouncement: (body) =>
    request({ path: "/admin/announcements", method: "POST", data: body }),
  publishAnnouncement: (id, body) =>
    request({
      path: `/admin/announcements/${id}/publish`,
      method: "POST",
      data: body,
    }),
  bindRequests: () => request({ path: "/admin/resident-bind-requests" }),
  staffDaily: () => request({ path: "/supervisor/staff/daily" }),
};
