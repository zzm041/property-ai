# 批次 E：RAG 有效关系与住户/物业用途

依据总设计 T04b、用户连续实施授权和批次 D 的后续入口。采用 brainstorming 的有界设计检查、writing-plans、TDD 与 executing-plans；普通修正已授权，不再次请求同一范围批准。主会话实施，最终独立只读复审。

## 已确认缺口及最小契约

现行 `_visibilities_for_target` 将角色与有效关系作 OR；过期住户关系、离职员工保留旧角色时仍可能读取知识。`/assistant/query` 没有用途参数，共用小程序页面会合并双角色的内部权限。以下是该既有功能的最小权限修正，不创建新的业务系统。

1. 请求新增 `audience: resident|property`，缺省 resident。住户用途只召回 RESIDENT 文档；物业用途才按合法角色返回内部 visibility。用途只是请求上下文，不能授予角色或范围。省略用途的旧内部查询调用需显式升级；普通住户请求保持兼容。
2. 选定小区时，住户资格必须同时有匹配范围 knowledge:read 与当前有效房屋关系；普通 STAFF 必须同时有匹配 STAFF 授权、ACTIVE Employee、同租户和有效 EmployeeProperty。有效关系不能给另一种角色补足其资格。
3. SUPERVISOR/PROPERTY_ADMIN 的明确管理授权，以及 TENANT_ADMIN/SYSTEM_ADMIN 的匹配管理授权，继续现有管理通道，不新增要求管理账号必须建立普通 Employee。只有匹配目标的管理角色能走该通道；广域 STAFF/RESIDENT 授权不能成为租户级全小区权限。
4. 不指定小区的租户级查询只允许匹配该租户的 TENANT_ADMIN 或 SYSTEM_ADMIN（仍需 knowledge:read）。有多个企业时仍要求明确范围，禁止混用别的租户管理权。
5. 每个批次 D 读取阶段重新计算这些规则；关系在重排/生成期间失效则不向后续 Provider/客户端返回原证据。保留已有不可变快照、严格引用、失败回退。
6. 住户关系使用目标小区与租户的存在性查询，检查 ACTIVE、valid_from/valid_to 及 House 对应关系；避免扫描前100项后漏掉合法关系。无需迁移。
7. 小程序默认住户知识视图，提供有明确标签的物业内部用途。服务端最终鉴权。切换用途/小区、页面重新进入和新查询时清空旧答案；旧异步响应不得覆盖新用途，失败后不能保留内部旧答案。管理员/员工显式选择内部视图，不能静默升级。

## 修改范围与验证

目标：retrieval Service、Resident Repository 存在性读取、assistant API/schema、知识助手页面、API 文档；新增权限与页面合成测试，更新原测试的显式物业请求。不修改派单、通知、数据库、配置、Provider 或企业知识库。

- [x] 建立 WIP/source-before 基线。
- [x] RED：有效/失效住户、STAFF 状态/租户/服务关系、双角色、跨小区/租户、广域错误角色、API 默认/非法用途、生成中撤销关系。
- [x] GREEN：最小修正；页面真实函数测试覆盖显式用途、切换清空、陈旧响应与失败。
- [x] 相关回归、独立只读复审、保护哈希、实际差异与报告。157 项相关回归、8 组 Node 通过；独立发现 1 项 P2 已返修并独立关闭。范围与限制见 `reports/implementation-review-20261101-05/review-ledger.md`。

测试只使用合成数据、SQL 编译/逻辑 Session 和模拟外部依赖。API 测试可使用本地 TestClient，不连接真实服务。真实 MySQL/微信页面、有效关系生产状态和服务部署仍未验证。T04b 的通知与其他历史摘要不随本批关闭。

## 工程边界

在现有 WIP 分支按 source-before 增量实施，不提交、不清理或改写历史。用户批准范围覆盖技能默认的重复审批/commit/工作树建议。原生小程序沿用项目方式，FastAPI 继续同步 Session 与普通 def；技能中的通用 async 建议不能覆盖仓库冻结规则。
