# Agent Skills 索引（三套工具侧配置，均不入库）
工作区根目录有三套内容高度重复的第三方 agent/skill 配置（约 250KB，`SKILL.md` 三份 8796/8796/8771B 互为副本）。属个人 AI 工具链，不是产品交付物；按 `DEC-2026-11-10-PA003二次裁决.md` F7 全部改判 C-不入库，仅留本索引。
| 位置 | 内容 | 用途 | 去向 |
| --- | --- | --- | --- |
| `.agents/skills/`（36 文件） | deepeval、fastapi-python、grill-me、grill-with-docs、grilling、miniprogram-development、teach | 本地 agent 技能定义（评测 / 后端规范 / 需求拷问 / 小程序） | 不入库；需要时按 `.agents/skills/<name>/SKILL.md` 本地取用 |
| `.claude/skills/`（24 文件） | deepeval、fastapi-python、miniprogram-development | Claude Code 侧同名技能副本 | 不入库；与 `.agents/` 重复，可整目录删除 |
| `agent/skills/`（24 文件） | deepeval、fastapi-python、miniprogram-development | 早期 agent 目录遗留副本 | 不入库；与上两套重复，确认无人引用后可删 |
新增技能请写到本机工具目录，不再落进仓库根。
