function normalizeContact(value) {
  if (typeof value !== 'string') return '';
  let phone = value.trim().replace(/[\s\-()]/g, '');
  if (phone.startsWith('00')) phone = '+' + phone.slice(2);
  if (/^1[3-9]\d{9}$/.test(phone)) phone = '+86' + phone;
  return /^\+[1-9]\d{7,14}$/.test(phone) ? phone : '';
}
module.exports = { normalizeContact };
