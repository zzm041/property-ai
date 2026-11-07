const assert = require("assert");
const path = require("path");

const { messageFromError } = require(path.resolve(__dirname, "../utils/error"));

assert.strictEqual(
  messageFromError({ error: { code: "IAM.FORBIDDEN", message: "Permission denied" } }),
  "无权执行此操作"
);
assert.strictEqual(
  messageFromError({ error: { code: "FILE.STORAGE_UNAVAILABLE", message: "Object storage is unavailable" } }),
  "附件服务暂时不可用，请稍后重试"
);
assert.strictEqual(
  messageFromError({ error: { code: "WO.CONFLICT", message: "stale version" } }),
  "工单已发生变化，请刷新后重试"
);
assert.strictEqual(
  messageFromError({ error: { code: "EMP.CHECKIN_DENIED", message: "Check-in was denied" } }),
  "当前无法签到，请核对任职和日期"
);
assert.strictEqual(
  messageFromError({ error: { code: "IDENTITY_RECOVERY.QUARANTINE_ACTIVE", message: "The quarantine is active" } }),
  "号码仍在隔离期内，暂不能完成此操作"
);
assert.strictEqual(
  messageFromError({ error: { code: "CUSTOM", message: "请填写处置原因" } }),
  "请填写处置原因"
);
assert.strictEqual(
  messageFromError({ error: { code: "CUSTOM", message: "debug backend exception" } }, "请求失败"),
  "请求失败"
);

console.log("user-facing error presentation tests passed");
