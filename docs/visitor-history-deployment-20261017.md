# 访客记录上线验收（2026-10-17）

## 交付

- 站点：https://test.gguuai.com/admin 。登录后在“模型节点”下方查看“访客检测记录”。
- 源码：`/opt/manxue-ai`，分支 `feat/visitor-history-20261017`。
- 运行功能提交：`fbb8620b62115712b180394c676c27fa666b6e1e`。
- 运行镜像：`manxue-ai:visitor-history-fbb8620`，image revision 标签绑定以上提交。
- 发布源码：`/opt/manxue-ai/releases/fbb8620b62115712b180394c676c27fa666b6e1e`。
- 部署配置：`/opt/manxue-ai/compose.production.yaml`，沿用原数据卷、端口和资源限额。
- 仅在服务器本地提交，没有推送到远端 Git 仓库。

完整分析和分阶段解决方案见 [visitor-failure-analysis-20261017.md](visitor-failure-analysis-20261017.md)。本次部署增加后台历史及诊断字段，没有调整模型、推理强度、检测预算、重试次数或答案标准。

## 验证结果

- 在继承原运行依赖的隔离候选镜像中，以非 root 用户、断网、1 CPU/1 GiB、PID 上限 256 运行全部测试：**150 项全部通过，97.753 秒**。包含真实 Chromium、原视觉审核/传输回归及新增 7 项访客记录测试；模型接口使用测试替身。
- 新增测试覆盖：管理员鉴权与退出会话、非法筛选参数、逐项错误与请求耗时、敏感数据白名单、公开 1 小时过期与后台历史分离、旧记录迁移和任务中断、分页/30 天保留/容量限制、排队状态、恶意模型文本转义、390/768 像素无页面横向溢出。
- 候选镜像与最终发布源码的 Python 和 web 运行文件逐字节一致；最终镜像只增加了正确的发布 revision 标签。
- JavaScript 语法、Python 编译与 Git diff 空白检查通过。
- 独立界面复核结论 SHIP，范围为新增访客记录区域与相关回归，非整个旧系统的安全审计。详情见设计复核文档。非阻断建议为筛选请求失败时更明确标记旧结果，以及增大任务 ID/诊断代码字号。
- 部署前确认无执行中的后台和访客任务；停服后再次用 SQLite backup API 备份，再替换容器。
- 线上对照：后台 runs 1479 条、节点 2 个、管理员认证 1 条、公开访客 8 条，均与切换前一致；系统设置哈希一致。
- 新后台历史完整迁入 **8 条**旧记录；执行异常筛选为 **7 条**。旧记录明确显示缺失的诊断字段，没有补造协议、错误码或重试明细。
- 公网 HTTPS 实测：健康接口 200、后台页面 200、未登录历史接口 401。真实管理员经公网浏览器登录后读取 8 条、筛选 7 条、展开旧记录说明、手机显示和退出登录清空均通过，页面脚本异常 0。
- 最终容器状态 running/healthy，重启次数 0，数据库、调度器和 renderer 自检均正常，recovery_pending=0。
- 公开 API 没有新增 `_diagnostics` 私有字段。新诊断字段通过隔离测试验证；本次没有提交虚假线上访客记录，也没有主动调用付费模型复测。

## 备份和回退

- 修改源码前的一致性备份：`/www/manxue-ai/backups/visitor-history-20261016T182947Z`，含数据库、原 Compose、原源码归档及清单。
- 停服切换时备份：`/www/manxue-ai/backups/visitor-history-release-20261016T183317Z`，含数据库、原 Compose 与 release-result.json。
- 原镜像 `manxue-ai:liulixin-lex-4ec1402474ce` 和原发布目录保留。原提交为 `4ec1402474ceed06186d0934fffe5aeb862aebe9`。
- 新增表属于兼容性扩展。若恢复旧版本，先停止服务并备份当前数据库；恢复旧 Compose 后，旧程序可继续使用旧表。需要在停服期间从 guest_results.result 中移除新私有 `_diagnostics` 字段，避免旧版公开序列化器把这些诊断附带返回；guest_history 表保留即可。重新启动时旧程序忽略该表。不要直接覆盖最新数据库而丢失上线后的记录。
- 本轮没有在线执行回退，也不将备份存在表述为真实回退演练。

工作区证据保存于 `/lex/manxue-visitor-20261017`，其中包含完整测试日志、部署回读、界面复核及桌面/手机截图。服务器上的测试截图位于 `/opt/manxue-ai/candidates/visitor-history-20261017/evidence`。
