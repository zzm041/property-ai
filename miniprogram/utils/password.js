function strength(password) {
  if (!password) {
    return "未输入";
  }
  let classes = 0;
  if (/[a-z]/.test(password)) classes += 1;
  if (/[A-Z]/.test(password)) classes += 1;
  if (/\d/.test(password)) classes += 1;
  if (/[^A-Za-z0-9]/.test(password)) classes += 1;
  if (password.length < 8 || /^\d+$/.test(password) || classes < 2) {
    return "偏弱";
  }
  if (password.length >= 12 && classes >= 3) {
    return "较强";
  }
  return "可用";
}

function meetsCurrentPolicy(password) {
  if (!password || password.length < 8 || password.length > 128) {
    return false;
  }
  if (/^\d+$/.test(password)) {
    return false;
  }
  if (/^[a-z]+$/.test(password)) {
    return false;
  }
  return true;
}

module.exports = { strength, meetsCurrentPolicy };
