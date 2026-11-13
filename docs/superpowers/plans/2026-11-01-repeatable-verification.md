# T15a：本轮增量的可重复验证入口

scripts/verify_business_ai.py默认只列计划；--run明确执行已声明的本地合成测试，--suite可选择access/analysis/rag/diagnosis/client/all。使用现有项目Python与Node，不安装依赖；白名单使用mock，不安排Docker、部署、迁移或真实Provider调用。套件白名单不含integration；all仅为本轮修订相邻范围，不是历史全量验收。该入口不是网络或环境隔离器：子进程继承环境，应用导入也可能通过Settings读取环境或.env；脚本不主动读取/打印凭证，不能据此保证测试进程无法接触凭证。

示例：`.\.venv\Scripts\python.exe -B scripts/verify_business_ai.py --suite diagnosis --run`。

每次创建独立reports/business-ai-verification/<UTC时间>-<随机后缀>，记录实际命令、源码/测试指纹、每组原始日志、退出码与运行状态。失败或超时不返回成功；中断保持INTERRUPTED，不伪造PASS。不会删除或覆盖旧报告。脚本单项最长300秒保护只终止其自有子进程；真实测试耗时不是生产性能。

这是复测入口，不替代每次差异审查、真实页面/运行验收和发布批准。新源码或测试范围变化时更新白名单并复审；不能凭该入口全绿宣布商业上线。

2026-11-05：新增 `--suite semantic` 与 `--suite agent`，client纳入主管Agent页面逻辑测试。agent中的`test_supervisor_agent_integration.py`位于unit目录，使用合成工具/桩，不启动数据库或真实模型。semantic纳入事件来源契约、危险/职责一致性、语义覆盖和机械省略表达测试。默认仍只打印计划；按受影响套件复测，不要求每批执行all。

实际运行把pytest临时目录限定在本轮报告目录内，并记录测试前后源码/测试指纹；检测到增加、删除或修改，即使子进程全部返回0，也将本轮标为FAIL。该比较仅覆盖脚本声明的文件类别，不是全工作区防篡改或环境隔离证明。
