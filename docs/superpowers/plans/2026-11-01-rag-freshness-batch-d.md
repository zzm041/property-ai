# 批次 D：RAG 短事务与证据时效

延续总台账 T03。用户授权继续普通增量实施；使用 writing-plans 明确契约、TDD 实现，最后独立只读复审。保护前批 WIP，EKB 冻结，不更换技术栈、部署、迁移或调用真实 Provider。

## 已读代码与当前问题

`RetrievalService.query` 从 `_acl_for` 的关系读取开始，由同步 Session 自动开启事务；随后 embedding、vector、rerank 和生成期间没有结束读取事务。生成后仍使用初始权限和 ORM chunk，`_citations` 另读文档标题；文档撤回/新版本/权限变化无法在输出前可靠拒绝。默认 Session `expire_on_commit=False`，仅结束事务不足以保证身份映射缓存的新鲜性。

## 修改契约

1. 复用 `begin_owned_transaction`；入口拒绝已有事务，不提交或回滚调用者工作。RAG 自己的四段只读事务：初始身份/ACL；向量召回后的身份/ACL/证据快照；生成前重验；输出前重验。各段进入时 expire_all，防止 Session 身份映射复用旧对象；不把 ORM 对象传出读取边界。
2. 默认通过 AuthService.load_principal 重新读取账号与权限；测试可注入 principal_loader。继续现有 tenant/property/visibility ACL 政策，不把客户端 principal 当最新权限真相。
3. 不可变快照包含 chunk/document 标识、租户/小区、visibility、版本、excerpt、source_locator、标题。快照时检查文档 ACTIVE、当前版本、与 chunk 的租户/小区/可见性一致。引用由已验证快照构造，不在返回时混入另一版本标题。
4. 重排和生成调用时不持有业务 Session 事务。生成前/输出前重验全部选中证据，不仅引用 ID：回答可能受到未引用上下文影响。任一已选证据撤回、删除、改版本/内容/定位或越权则返回固定 knowledge_unavailable、空引用，不保留原答案。不重新调用模型或静默移除部分引用后展示旧答案。
5. 当前账号/范围权限撤回保留现有 Auth/ACL 错误语义；不输出模型答案。正常无证据/Provider 失败/重排降级/严格引用规则继承批次 A，不修改评分阈值。
6. 这是最后一次读取边界处的权限重验，不承诺网络返回与并发撤权绝对原子。T04 中角色与有效关系的政策细节、双角色 RAG 视图不是本批自动闭环。

## 目标文件与验收

- 主要改 `backend/app/service/retrieval.py`，更新 API 契约；既有 failure-contract 测试只更新 Session/新鲜身份夹具，保持原输出断言。
- [x] 保存前批成果与源码快照。
- [x] 新增 `test_retrieval_freshness_contract.py`，用真实 SQLAlchemy Session 的逻辑事务与模拟 Repository/Provider 重现：Provider 时事务未释放、撤权、已选但未引用证据撤回、版本和内容变更、快照隔离、调用者事务保护。
- [x] RED 后最小实现短事务、快照及两次重验；异常/早退无事务遗留。
- [x] 既有 RAG/Provider/ACL、事务契约和相关分析回归通过，独立只读复审与保护哈希检查。相关 290 项通过；独立复审 68 项通过，无 P1/P2 阻断；44 项保护哈希一致。详见 `reports/implementation-review-20261101-04/review-ledger.md`。

## MySQL 验收边界

本轮不会启动/连接共享业务库，也不拿 SQLite 模拟 MySQL 集成通过。上述测试仅证明 Python 编排契约；真实 MySQL 8 的连接归还、REPEATABLE READ 跨事务新鲜度与并发撤权仍 OPEN。

后续独立环境验证方案：具名隔离 MySQL 8 测试库及明确允许的连接，合成租户/用户/文档；Provider 使用本地阻塞桩。另一独立连接在生成期间提交文档撤回/新版本或权限撤回；确认回答被拒绝，并通过池 checkout/checkin 与事务观察证明等待 Provider 时未持连接。测试不得读取业务库或触碰共享 schema；环境/数据准备先明确授权，不在本批自动执行。

## 缓存复用核查入口

现有 `analysis_attempt.py` 已有模型版本和媒体协议检查点字段，不能笼统称“没有缓存”。本批先记录调用链与待验证项；随后单独验证版本变更、输入/媒体变化、旧结果复用，不为补深度直接加入语义缓存或改冻结依赖。
