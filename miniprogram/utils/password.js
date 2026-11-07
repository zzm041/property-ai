function codePoints(password) {
  return Array.from(password || "");
}

function isAsciiDigitsOnly(chars) {
  return chars.length > 0 && chars.every((item) => item >= "0" && item <= "9");
}

function isAsciiLowercaseLettersOnly(chars) {
  return chars.length > 0 && chars.every((item) => item >= "a" && item <= "z");
}

function strength(password) {
  if (!password) {
    return "未输入";
  }
  const chars = codePoints(password);
  let classes = 0;
  if (chars.some((item) => item >= "a" && item <= "z")) classes += 1;
  if (chars.some((item) => item >= "A" && item <= "Z")) classes += 1;
  if (chars.some((item) => item >= "0" && item <= "9")) classes += 1;
  if (chars.some((item) => !/[A-Za-z0-9]/.test(item))) classes += 1;
  if (chars.length < 8 || isAsciiDigitsOnly(chars) || classes < 2) {
    return "偏弱";
  }
  if (chars.length >= 12 && classes >= 3) {
    return "较强";
  }
  return "可用";
}

function meetsCurrentPolicy(password) {
  const chars = codePoints(password);
  if (!password || chars.length < 8 || chars.length > 128) {
    return false;
  }
  if (isAsciiDigitsOnly(chars) || isAsciiLowercaseLettersOnly(chars)) {
    return false;
  }
  return true;
}

module.exports = { strength, meetsCurrentPolicy, codePoints };
