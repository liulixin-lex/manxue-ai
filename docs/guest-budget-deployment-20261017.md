# 访客检测修复上线记录（2026-10-17）

## 发布

- 线上站点：[访客检测](https://test.gguuai.com/#guest)、[管理后台](https://test.gguuai.com/admin)。
- 功能提交：`0fc42b0fcee687074a9b75bba44d23a6f2c29f9c`，分支 `fix/guest-budget-20261017`，代码 `/opt/manxue-ai`；本轮仅本地提交，未推送远端。
- 运行镜像：`manxue-ai:guest-budget-0fc42b0`，OCI revision 标签与功能提交一致。
- 发布源码：`/opt/manxue-ai/releases/0fc42b0fcee687074a9b75bba44d23a6f2c29f9c`，配置仍为 `/opt/manxue-ai/compose.production.yaml`。
- 原运行镜像 `manxue-ai:visitor-history-fbb8620`、旧发布源码和备份保留。
- 切换备份：`/www/manxue-ai/backups/guest-budget-20261017T044922Z`，包括停服后使用 SQLite backup API 保存的数据库、原 Compose、原源码归档及 release-result.json。数据库 integrity_check 为 ok。
- 切换前无正在执行的访客或后台检测。新版本保留原 1510 条站点检测、2 个节点、管理员认证、8 条访客历史。原配置逐项与节点凭据哈希一致，只新增访客默认预算字段。

## 验收

- 最终发布镜像在隔离、断网、非 root、1 CPU/1 GiB/PID 上限 256 的环境中运行完整回归：**161 项全部通过，104.514 秒**，包含真实 Chromium。
- Python 编译、JavaScript 语法、Git diff 空白检查通过。独立界面复核 SHIP；设计保持见 guest-budget-design-review-20261017.md。
- 真正外部接口复测：候选环境的 medium 糖果 22.949 秒通过；xhigh 鹈鹕 403.566 秒通过基础校验；xhigh 糖果单独复测 51.091 秒通过。首次未启用重试的 xhigh 双项中，糖果曾发生上游生成错误，该失败没有被删除或伪装。
- 公网真实浏览器验证：自动预算 medium/high/xhigh 为 1200/1800/2400 秒；xhigh token 上限 65536；管理员回读默认 1200 秒 / 32768 token；桌面 1440 和手机 390 像素无页面横向溢出，页面脚本异常 0，退出后清空后台记录。
- 线上真实 Chat Completions + effort 不发送 + max_tokens 的糖果检测已通过，含并发名额等待共 70.579 秒。线上真实 Responses + xhigh 双项全部通过，总耗时 382.539 秒，两项各请求 1 次，无重试；最终结果在工作区 online-results.json 中保存。
- 公网健康接口 200，未登录管理员历史接口 401；容器 healthy、重启次数 0，数据库/调度器/renderer 自检正常。
- 初次 Python 运维验证客户端因默认 User-Agent 被 Cloudflare 1010 拒绝，此请求没有进入应用。按浏览器请求特征发送后可正常提交；真实浏览器验收同样成功。本轮没有修改 Cloudflare 规则。

## 线上完整双项复测

任务 ID：`6cba353d8f201500e4f47e4f`，协议 Responses，effort=xhigh，自动预算 2400 秒 / 65536 token，最多重试 2 次。与 Chat 任务均使用站点已有接口凭据，由公网访客提交与轮询链路执行；线上后台会保留这些真实检测记录。

**最终状态 passed**。鹈鹕基础校验通过，生成输入 218 / 输出 18431 token；糖果最终答案校验通过，生成输入 4327 / 输出 1898 token。整轮耗时 382.539 秒（约 6 分 23 秒），两项各仅请求一次。该请求超过旧版 300 秒上限后成功完成；后台访客历史中可直接查看真实记录。验证后历史共 10 条，原 8 条加本次 2 条真实线上任务。

## 回退与边界

回退前先停止服务并备份最新数据库，恢复上述备份内的原 Compose，再用 `docker compose -f /opt/manxue-ai/compose.production.yaml up -d --no-build --force-recreate manxue-ai` 恢复原镜像。无需覆盖最新数据库或删除访客历史；原版本读取现有数据格式，新默认配置字段在旧程序中会被忽略或随保存丢弃。回退将恢复旧 300 秒访客限制，本轮未在线执行回退演练。

时间上限是本地检测能等待的边界，不是对接口服务商可用性的保证。上游 404、鉴权、额度或生成故障仍按实际错误呈现；不自动改变测试模型、effort、协议或判分标准来制造通过。

工作区证据目录：`/lex/manxue-guest-budget-20261017`；服务器验证材料：`/opt/manxue-ai/candidates/guest-budget-20261017/evidence`。
