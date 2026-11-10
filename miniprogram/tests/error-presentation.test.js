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

// PA-127 F3: a dispatch 409 now carries details.reason; show it as a short
// parenthetical so a supervisor can tell a skill gate from an attendance one.
assert.strictEqual(
  messageFromError({
    error: {
      code: "WO.EMPLOYEE_NOT_ELIGIBLE",
      message: "Employee is not eligible for dispatch",
      details: { reason: "DUTY_MISMATCH" }
    }
  }),
  "所选人员当前不符合派单条件（原因：班组职责与工单品类不符）"
);
assert.strictEqual(
  messageFromError({
    error: {
      code: "WO.EMPLOYEE_NOT_ELIGIBLE",
      message: "Employee is not eligible for dispatch",
      details: { reason: "NOT_CHECKED_IN" }
    }
  }),
  "所选人员当前不符合派单条件（原因：当日未签到）"
);
// An unrecognised reason must never leak the raw code into the copy.
assert.strictEqual(
  messageFromError({
    error: {
      code: "WO.EMPLOYEE_NOT_ELIGIBLE",
      message: "Employee is not eligible for dispatch",
      details: { reason: "SOME_FUTURE_REASON" }
    }
  }),
  "所选人员当前不符合派单条件"
);

console.log("user-facing error presentation tests passed");
