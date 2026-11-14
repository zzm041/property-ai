# 微信原生小程序

Production V1 唯一客户端。同一应用按 `/me` 返回的 Role / Permission / Scope 展示菜单。

前端隐藏不是安全边界。Backend RBAC 始终生效。

## 配置

复制 `config/env.local.example.js` 为 `config/env.local.js`，按小程序环境填写 API origin。

不要在客户端保存 App Secret、数据库凭证或 Provider Secret。不要把生产域名或密钥提交进仓库。

Access Token 仅存微信 `wx.setStorageSync`。

401 会清除 Token 并回到登录页。403 只提示无权限。
