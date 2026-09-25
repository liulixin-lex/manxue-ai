# Manxue AI

自托管的模型接口观察站，通过 SVG 动画生成和逻辑题检测，持续记录模型在特定任务上的表现。支持 OpenAI 兼容的 Responses 和 Chat Completions 协议，提供公开画廊、历史时间线、后台节点管理和访客独立测试。

## 功能

- **多节点管理**：配置多个 API 节点，切换监控节点，手动或定时执行检测。
- **动画生成与审核**：生成鹈鹕骑自行车 SVG 动画，校验 SVG 安全性，并通过多帧截图审核画面与动作。
- **历史记录**：浏览生成结果、检测状态、糖果题通过率和历史时间线。
- **访客测试**：使用自己的接口提交单项或双项检测，支持排队与进度查询。
- **后台管理**：密码登录、会话过期和 API 凭据脱敏展示。

## Docker 部署

需要 Docker Engine / Docker Desktop 和 Docker Compose。Windows 用户请使用 Linux 容器。

```sh
git clone https://github.com/w1196396546/manxue-ai.git
cd manxue-ai
```

首次启动时，按提示输入 12–256 字符的管理密码。

Linux / macOS（Bash）：

```bash
read -r -s -p "Initial admin password: " ADMIN_TOKEN
echo
export ADMIN_TOKEN
docker compose up -d --build
unset ADMIN_TOKEN
```

Windows（PowerShell 7）：

```powershell
$env:ADMIN_TOKEN = Read-Host 'Initial admin password' -MaskInput
docker compose up -d --build
Remove-Item Env:ADMIN_TOKEN
```

公开页面：<http://127.0.0.1:18765/>；后台：<http://127.0.0.1:18765/admin>。

### 配置节点

1. 使用初始管理密码登录后台。
2. 编辑默认节点，将示例地址替换为实际 API 地址，填写 API Key、模型名称和协议。
3. 执行一次检测，确认接口可用后再开启自动检测。

动画视觉审核默认使用生成节点，也可在“调度与系统配置”中选择独立审核节点；审核模型需要支持图片输入。默认检测间隔为 30 分钟，自动检测初始关闭。

新版区分生成、校验、截图、审核和保存阶段。“证据不足”不会当作请求失败；已生成的 SVG 和已保存的截图不会因后续审核失败而丢失。详情页可查看实际审核帧与时间戳。完整实现与运行限制见 [稳定性与视觉审核说明](docs/reliability-and-evaluation.md)。

### 数据与运行维护

容器数据保存在 Compose 命名卷中，重建容器不会清空记录。`docker compose down -v` 会删除数据卷。

Compose 已启用 `init: true`，用于回收视觉审核截图时 Chromium 遗留的子进程，避免僵尸进程累积耗尽容器的进程配额。自行使用 `docker run` 部署时，也需要传入 `--init`。

从未启用此配置的旧版本升级时，等待当前检测结束，再运行 `docker compose up -d --build --force-recreate`。重建会清理已积累的僵尸进程并应用新配置；仅运行 `docker compose restart` 不会应用 `init` 设置。历史失败记录会保留，新检测将使用修复后的环境。

`ADMIN_TOKEN` 仅用于首次初始化，之后不会覆盖已保存的密码。首次配置完成并清除该环境变量后，可执行 `docker compose up -d --force-recreate`，从容器配置中移除初始密码。

对公网开放时，请在主机上配置 HTTPS 反向代理，转发至 `127.0.0.1:18765`。不要启用请求正文日志。

新版本会增加 SQLite 字段并启用 WAL。升级前使用 SQLite backup API 备份数据库，勿仅复制运行中的主数据库文件。截图保存在数据卷的 `artifacts/`，默认保留最多 7 天、总容量约 1 GiB；结果、时间戳及哈希继续保留，过期图片会显示不可用。`/api/health` 检查调度心跳、数据库读取和最近本地截图自检，不消耗模型额度。

## 本地运行

支持 Linux / macOS、Python 3.11+。Windows 请使用 Docker 或 WSL。

在仓库根目录执行：

```sh
cd app
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
python server.py
```

Linux 如缺少浏览器系统依赖，可执行 `python -m playwright install --with-deps chromium`。

访问 <http://127.0.0.1:8765/admin> 初始化管理密码，然后配置节点。本地数据默认写入 `app/data/`。

## 环境变量

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `HOST` | `127.0.0.1` | 监听地址；容器内为 `0.0.0.0` |
| `PORT` | `8765` | 服务端口 |
| `DATA_DIR` | `app/data`（容器内 `/data`） | 数据库与实例锁目录 |
| `ADMIN_TOKEN` | 空 | 仅首次初始化管理密码，不是 API Key 或会话令牌 |

## 检测说明

新版糖果题按最后一行明确的 `最终答案：21`（或仅回答 `21`）判定，不再把推理、否定句中的 `21` 当作通过。旧记录保留旧规则。24 小时聚合按当前节点、测试定义 v3 统计，避免跨节点及新旧判分混算；时间线和画廊仍可查看历史结果。

后台鹈鹕审核采用 12–16 帧有时间戳的截图，分别检查形态、结构、骑乘、动作、场景、可见校验码及循环连续性。截图忠实保留 SVG 自身背景与尺寸语义；超出采样能力的动作保留“证据不足”。视觉裁判仍可能误判，本版本没有宣称经过人工标注的准确率保证。这些检测不证明模型身份，也不代表模型的整体能力。

生成、视觉审核和重试会消耗对应 API 额度。访客测试使用访客提交的接口配置，不使用后台节点凭据，也不执行视觉审核。

生成与审核分别有超时和重试预算；新增帧数会增加视觉输入用量。程序仅重试临时请求故障，不会为得到“通过”而自动修改或反复生成作品。访客结果明确标为基础校验。

服务需要持续运行才能执行定时任务；同一数据目录仅支持一个服务进程。

## 开发与验证

```sh
python -m unittest discover -s tests -v
```

真实浏览器与端到端测试需要安装 `app/requirements.txt` 及 Chromium，并设置 `RENDER_TESTS=1`。项目包含 GitHub Actions 回归工作流、隔离容器测试命令和带强制超时的连续渲染测试；见 [验证说明](docs/reliability-and-evaluation.md#自动化验证)。所有默认测试使用假模型，不需要 API Key。

## 隐私与安全

后台 API Key 保存在数据目录的 SQLite 数据库中，采用文件权限保护，**没有数据库加密**。管理密码保存为带随机盐的 PBKDF2-SHA256 哈希。不要发布数据库、数据卷、日志、备份或真实环境配置。

访客完整 API 地址和密钥仅在任务排队及执行期间保留在内存中；公开结果包含脱敏域名、模型、回答和生成的 SVG。访客结果会对其他访问者可见，不要向测试提交机密内容。完成结果保留一小时；重启后未完成任务标记中断。

## 友情链接

- [Linux DO](https://linux.do/)

## 不降智网站推荐

- [fullcupai](https://api.fullcupai.com/)

## 许可证

[Apache License 2.0](LICENSE)
