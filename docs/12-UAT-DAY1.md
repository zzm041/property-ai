# Production V1 UAT Checklist — Day 1

Execute against Development or a staging compose stack. Record PASS/FAIL.

- [ ] Login: WeChat Fake `/auth/wechat/login` and Development `/auth/dev/token`
- [ ] `/me` returns roles, permissions, scopes (no roles in JWT)
- [ ] House binding create / review
- [ ] Employee assignment + attendance check-in / check-out
- [ ] Resident repair create (AI async, status PENDING_ANALYSIS)
- [ ] AI analysis success and provider-failure manual fallback
- [ ] Dispatch assign / accept / start / receipt
- [ ] Resident confirm / reopen
- [ ] Incident list
- [ ] Announcement draft / review / publish (AI never publishes)
- [ ] Knowledge ingest async + Resident question returns citation
- [ ] Cross-property and staff-only RAG denial
- [ ] Notification list / unread / mark read
- [ ] Mini Program pages call the same API contract
- [ ] Production guards: DEV_AUTH off, no default secret, WeChat config when enabled
- [ ] healthz up; readyz MySQL critical; Milvus/embedding non-critical
