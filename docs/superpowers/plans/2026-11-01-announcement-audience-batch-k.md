# T04：公告用途隔离

承接用户要求的住户/物业隔离和已批准v2，沿用C/E相同原则。GET /announcements 新增 audience=resident|property，默认resident；住户用途必须有该小区有效房屋关系与read权限，只提供对应OWNER/TENANT/RESIDENT公告；同人兼任员工/主管不得自动扩大默认住户结果。property用途需目标scope内同grant管理授权，或同租户ACTIVE员工+有效服务关系+本范围员工read角色。参数不授予权限。管理发布/草稿API不变，历史个人通知政策不在本批。

小程序列表显式切换住户/物业用途；详情继承经校验的用途，后台复核权限。重新进入列表默认住户；切换小区/用途、隐藏/退出、加载失败清空结果并丢弃过期异步响应，详情重新进入重新读取。复用现有me/catalog/property-scope，不增加角色系统。

范围：announcement Service/API、api.js、公告list/detail页面，API说明；合成Python和Node交互测试，不访问真实微信/Provider。先失败再修正，覆盖双角色、跨范围、无房屋/任职到期/员工停用/账号停用/非法用途、分页参数和详情访问；保留已发布及定向过滤。

J新增管理正例须显式property以迁移旧调用，不放宽断言。用户已授权普通源码/页面，既有策略“住户视图不含内部资料”不再重复请求批准。不会安装缺失CloudBase技能或依赖，本项目使用原生小程序+自有后端。
