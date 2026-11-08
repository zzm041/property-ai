// Existing ascending API cursors; no business filtering or authorization here.
function appendPage(existing, payload, afterId, limit, key = "id") {
  const incoming = (payload && payload.items) || [];
  let previous = Number(afterId || 0);
  incoming.forEach((row) => {
    const id = Number(row[key]);
    if (!Number.isSafeInteger(id) || id <= previous) throw new Error("分页记录顺序无效，请刷新");
    previous = id;
  });
  const explicit = payload && Object.prototype.hasOwnProperty.call(payload, "next_cursor");
  const nextCursor = explicit ? Number(payload.next_cursor || 0) : (incoming.length === limit ? previous : 0);
  if (!Number.isSafeInteger(nextCursor) || nextCursor < 0 || (nextCursor && (!incoming.length || nextCursor !== previous || nextCursor <= Number(afterId || 0)))) throw new Error("分页游标无效，请刷新");
  const seen = new Set();
  const items = existing.concat(incoming).filter((row) => {
    const id = String(row[key]);
    if (seen.has(id)) return false;
    seen.add(id);
    return true;
  });
  return { items, nextCursor };
}
module.exports = { appendPage };
