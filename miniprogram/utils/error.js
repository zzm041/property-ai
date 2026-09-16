function messageFromError(payload, fallback) {
  if (payload && payload.error && payload.error.message) {
    return payload.error.message;
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
