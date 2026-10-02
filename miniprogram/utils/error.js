const USER_MESSAGES = {
  "SYS.VALIDATION_ERROR": "提交内容有误，请检查后重试",
  "SYS.NOT_FOUND": "内容不存在或已失效",
  "SYS.METHOD_NOT_ALLOWED": "当前操作不可用",
  "SYS.HTTP_ERROR": "请求未能完成，请稍后重试",
  "SYS.INTERNAL_ERROR": "服务暂时异常，请稍后重试",
  "AUTH.MISSING_TOKEN": "请重新登录",
  "AUTH.INVALID_TOKEN": "登录状态无效，请重新登录",
  "AUTH.TOKEN_EXPIRED": "登录已过期，请重新登录",
  "AUTH.INVALID_USER": "账号不可用",
  "AUTH.USER_DISABLED": "账号已停用，请联系管理员",
  "AUTH.INVALID_CODE": "验证码不正确或已失效",
  "AUTH.INVALID_PASSWORD": "手机号或密码不正确",
  "AUTH.SMS_RATE_LIMITED": "验证码请求过于频繁，请稍后再试",
  "AUTH.SMS_PROVIDER_DOWN": "验证码服务暂时不可用",
  "AUTH.INVALID_PHONE": "请输入有效的联系电话",
  "AUTH.SESSION_REVOKED": "登录状态已失效，请重新登录",
  "AUTH.CHALLENGE_REQUIRED": "请先完成安全验证",
  "AUTH.CHALLENGE_INVALID": "安全验证未通过，请重试",
  "AUTH.WEAK_PASSWORD": "新密码不符合安全要求",
  "AUTH.PASSWORD_MISMATCH": "两次输入的新密码不一致",
  "AUTH.RESET_TOKEN_INVALID": "重置凭证不正确或已失效",
  "AUTH.OLD_PHONE_MISMATCH": "原手机号与当前账号不一致",
  "AUTH.PHONE_ALREADY_REGISTERED": "该手机号已关联其他账号，请使用账号合并流程",
  "AUTH.IDENTITY_RECOVERY_REQUIRED": "该号码需要先完成身份恢复审核",
  "AUTH.WECHAT_BIND_REQUIRED": "请先绑定微信身份",
  "AUTH.WECHAT_ALREADY_BOUND": "该微信身份已绑定其他账号",
  "AUTH.WECHAT_REBIND_REQUIRED": "请通过安全流程换绑微信身份",
  "AUTH.WECHAT_SAME_BINDING": "当前微信身份已经绑定",
  "AUTH.WECHAT_INVALID_CODE": "微信登录凭证无效，请重试",
  "AUTH.WECHAT_UNAVAILABLE": "微信登录服务暂时不可用",
  "IAM.FORBIDDEN": "无权执行此操作",
  "IAM.INVALID_SCOPE": "当前账号不在该业务范围内",
  "IAM.USER_NOT_FOUND": "用户不存在或不可用",
  "IAM.ROLE_NOT_FOUND": "角色配置不存在或不可用",
  "IAM.PERMISSION_NOT_FOUND": "权限配置不存在或不可用",
  "IAM.DUPLICATE_GRANT": "该授权已存在",
  "IAM.GRANT_NOT_FOUND": "授权记录不存在或已失效",
  "IAM.MUTATION_CONFLICT": "数据已发生变化，请刷新后重试",
  "RES.HOUSE_NOT_FOUND": "房屋不存在或无权查看",
  "RES.BIND_REQUEST_NOT_FOUND": "绑定申请不存在或无权查看",
  "RES.DUPLICATE_PENDING": "已有待审核的绑定申请",
  "RES.DUPLICATE_ACTIVE_RELATION": "该房屋关系已生效",
  "RES.ALREADY_PROCESSED": "该申请已处理",
  "RES.CONFLICT": "房屋关系已发生变化，请刷新后重试",
  "RES.INVALID_INPUT": "房屋资料有误，请检查后重试",
  "RES.HOUSE_NOT_AVAILABLE": "该房屋当前不可申请",
  "WO.INVALID_INPUT": "工单内容有误，请检查后重试",
  "WO.NOT_FOUND": "工单不存在或无权查看",
  "WO.INVALID_TRANSITION": "当前工单状态不能执行此操作",
  "WO.ALREADY_ASSIGNED": "工单已安排处理人员",
  "WO.ASSIGNMENT_CONFLICT": "派单状态已变化，请刷新后重试",
  "WO.EMPLOYEE_NOT_ELIGIBLE": "所选人员当前不符合派单条件",
  "WO.NOT_ASSIGNEE": "当前账号不是本工单处理人员",
  "WO.ALREADY_PROCESSED": "该操作已经处理",
  "WO.SCOPE_MISMATCH": "工单不在当前管理范围内",
  "WO.CONFLICT": "工单已发生变化，请刷新后重试",
  "WO.HOUSE_NOT_OWNED": "当前账号没有该房屋的有效关系",
  "WO.CONTENT_REQUIRED": "请填写问题说明或添加附件",
  "WO.IDEMPOTENCY_CONFLICT": "请勿用同一次提交重复发送不同内容",
  "FILE.INVALID_INPUT": "附件内容无效，请重新选择",
  "FILE.CONFLICT": "附件已上传，不能替换内容",
  "FILE.NOT_FOUND": "附件不存在或无权查看",
  "FILE.TOO_LARGE": "附件超过大小限制",
  "FILE.UNSUPPORTED_TYPE": "不支持该附件格式",
  "FILE.STORAGE_UNAVAILABLE": "附件服务暂时不可用，请稍后重试",
  "AI.TIMEOUT": "智能分析超时，工单已保留并将继续处理",
  "AI.DATABASE_RETRY_EXHAUSTED": "工单处理暂时繁忙，系统将按规则继续处理",
  "AI.UNAVAILABLE": "智能分析暂时不可用，工单已保留",
  "AI.INVALID_OUTPUT": "智能分析结果未通过校验，工单已保留",
  "AI.RATE_LIMITED": "智能分析任务较多，工单已排队",
  "AI.PROVIDER_FAILED": "智能分析暂时失败，工单已保留",
  "AI.STALE_RESULT": "工单已更新，本次旧分析不会生效",
  "AI.LOW_CONFIDENCE": "信息不足，请补充现场情况",
  "AI.NOT_FOUND": "智能分析记录不存在或无权查看",
  "EMP.INVALID_INPUT": "员工资料有误，请检查后重试",
  "EMP.EMPLOYEE_NOT_FOUND": "员工不存在或无权查看",
  "EMP.DEPARTMENT_NOT_FOUND": "部门不存在或已失效",
  "EMP.DUTY_NOT_FOUND": "职责不存在或已失效",
  "EMP.SKILL_NOT_FOUND": "技能不存在或已失效",
  "EMP.ASSIGNMENT_NOT_FOUND": "任职关系不存在或已失效",
  "EMP.DUPLICATE_EMPLOYEE": "该员工已存在",
  "EMP.DUPLICATE_ASSIGNMENT": "该小区任职已存在",
  "EMP.CHECKIN_DENIED": "当前无法签到，请核对任职和日期",
  "EMP.CHECKOUT_DENIED": "当前无法签退，请刷新后重试",
  "EMP.CONFLICT": "员工资料已发生变化，请刷新后重试",
  "INC.INVALID_INPUT": "事件内容有误，请检查后重试",
  "INC.NOT_FOUND": "事件不存在或无权查看",
  "INC.SCOPE_MISMATCH": "事件不在当前管理范围内",
  "INC.CONFLICT": "事件已发生变化，请刷新后重试",
  "ANN.INVALID_INPUT": "公告内容有误，请检查后重试",
  "ANN.NOT_FOUND": "公告不存在或无权查看",
  "ANN.INVALID_TRANSITION": "当前公告状态不能执行此操作",
  "ANN.NOT_REVIEWED": "公告尚未完成审核",
  "ANN.SCOPE_MISMATCH": "公告不在当前管理范围内",
  "ANN.CONFLICT": "公告已发生变化，请刷新后重试",
  "KB.NOT_FOUND": "知识资料不存在或无权查看",
  "KB.INVALID_INPUT": "知识资料内容有误，请检查后重试",
  "KB.SCOPE_MISMATCH": "知识资料不在当前管理范围内",
  "KB.CONFLICT": "知识资料已发生变化，请刷新后重试",
  "KB.UNAVAILABLE": "知识服务暂时不可用，请稍后重试",
  "KB.INSUFFICIENT": "暂未找到足够依据，请补充问题",
  "KB.ARCHIVED": "该知识资料已归档",
  "SLA.INVALID_INPUT": "SLA 策略内容有误，请检查后重试",
  "SLA.CONFLICT": "SLA 策略已发生变化，请刷新后重试",
  "SLA.VERSIONING_UNAVAILABLE": "当前环境尚未启用 SLA 版本管理",
  "STATS.INVALID_INPUT": "统计范围有误，请重新选择",
  "NTF.NOT_FOUND": "通知不存在或无权查看",
  "NTF.PROVIDER_FAILED": "通知发送失败，系统将按规则处理",
  "NTF.PROVIDER_UNCERTAIN": "通知发送状态待确认，不会重复执行业务操作",
  "IDENTITY_RECOVERY.INVALID_INPUT": "身份申请内容有误，请检查后重试",
  "IDENTITY_RECOVERY.UNAVAILABLE": "身份恢复能力暂不可用",
  "IDENTITY_RECOVERY.NOT_FOUND": "身份申请不存在或无权查看",
  "IDENTITY_RECOVERY.CONFLICT": "请核对申请状态，并确保由两名不同且当前有权的审核人完成审核",
  "IDENTITY_RECOVERY.PROOF_REJECTED": "身份核验未通过",
  "IDENTITY_RECOVERY.QUARANTINE_ACTIVE": "号码仍在隔离期内，暂不能完成此操作",
};

function containsChinese(value) {
  return /[\u3400-\u9fff]/.test(String(value || ""));
}

function messageFromError(payload, fallback) {
  const error = payload && payload.error;
  if (error && USER_MESSAGES[error.code]) {
    return USER_MESSAGES[error.code];
  }
  if (error && containsChinese(error.message)) {
    return error.message;
  }
  return fallback || "请求失败";
}

function isUnauthorized(statusCode, payload) {
  if (statusCode === 401) {
    return true;
  }
  const code = payload && payload.error && payload.error.code;
  return (
    code === "AUTH.MISSING_TOKEN" ||
    code === "AUTH.INVALID_TOKEN" ||
    code === "AUTH.TOKEN_EXPIRED"
  );
}

module.exports = {
  messageFromError,
  isUnauthorized,
};
