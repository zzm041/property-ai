# Evidence boundary — AUTH-IDENTITY-AI-001

VIS-001..004 remain local visualization only. They used DevAuth seed login and Fake providers.

| Claim | Status |
| --- | --- |
| Unified login page with SMS/password fields calling `/auth/sms/*` and `/auth/password/*` | Software implemented. Not formally accepted. |
| Real credential verification (not `/auth/dev/token`) | Backend routes implemented. Formal login.js does not call DevAuth except LOCAL_PREVIEW. Runtime DB tests skip until 0009. |
| OTP attempts persist before 401 | `_apply_otp` then commit then `_raise_otp`. |
| Redis throttle | Lua INCR+EXPIRE, fail-closed. |
| WeChat unknown openid | `AUTH.WECHAT_BIND_REQUIRED`, no User insert. |
| WeChat bind | Requires Principal.auth_method sms or password. |
| WeChat known-binding login | Issues AuthSession + refresh. Needs 0009 table. |
| Formal admin employee `user_id` | Rejected when APP_ENV not development/test. |
| Mini-program session restore | 401 refresh-once then retry, shared in-flight refresh. Credential-auth 401 does not clear session. |
| Development SMS labeled | `delivery_channel=development`. Not real SMS. |
| Formal house picker without typed house_id | Mini-program cascading selectors. Catalog GET routes exist. |
| Employee invite/claim | Software implemented. Needs 0009 `employee_invites`. |
| Automatic Assignment without human assign tap | **Not complete.** `ai_auto_assign` default False. Pipeline returns `SCHEMA_PENDING_0009` until assignment source columns are applied and mapped. |
| Real LLM/VLM/ASR/Embedding | NOT_RUN. Adapters exist. Day 4 still Fake. |
| Production V1 / Day 4 PASS | Not claimed. |

Do not treat HTTP 200, ANALYZE events, or citation arrays as real-AI completion.
