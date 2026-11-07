// Display policy: Beijing time to the minute. Never alter API concurrency tokens.
function formatBeijingTime(value) {
  if (!value) return '未记录';
  let source = String(value).trim().replace(' ', 'T');
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$/.test(source)) return '时间不可用';
  source = source.replace(/(\.\d{3})\d+/, '$1');
  if (!/(Z|[+-]\d{2}:\d{2})$/.test(source)) source += 'Z';
  const milliseconds = Date.parse(source);
  if (!Number.isFinite(milliseconds)) return '时间不可用';
  return new Date(milliseconds + 8 * 60 * 60 * 1000).toISOString().slice(0, 16).replace('T', ' ');
}
module.exports = { formatBeijingTime };
