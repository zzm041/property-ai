## 做了什么

<!-- 一句话说明改动与动机；关联 issue 用 “Closes #123”。 -->

## 验证方式

<!-- 贴命令与输出摘要，而不是“应该能过”。 -->

```
python scripts/ops/unit_gate.py
# unit_gate: OK - red set is a subset of known-red
```

判据是**红集 ⊆ `scripts/ops/known-red.txt`**（出现 `NEW RED: <nodeid>` → exit 1；`SHRUNK: n` → exit 0）。
env 三件（`APP_ENV=test` / `DATABASE_URL` / `TEST_DATABASE_URL`）见 `ENGINEERING.md` §4；缺了 gate 直接 exit 2。

## 影响面

- [ ] 后端 API / 服务
- [ ] AI 分析与派单
- [ ] 小程序
- [ ] 部署脚本 / 配置
- [ ] 文档

## 检查清单

- [ ] 没有提交真实住户数据、令牌或 `.env` 内容
- [ ] `unit_gate` 通过：红集 ⊆ `known-red.txt`；新增红要么修掉，要么在 `scripts/ops/known-red.txt` 加白并写清定性理由
- [ ] 如改变运行契约（接口字段、配置键、错误码），已在文档或 CHANGELOG 里写明
