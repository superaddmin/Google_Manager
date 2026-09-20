# CDK 工作台部署前整改与验收状态

结论：**暂缓上线（NO-GO）**。本次范围仅为 CDK 工作台，现金支付另行排期。本文接续此前的[整改快照](release-remediation-2026-09-20.md)，区分已验证候选、当前源码和最终交付制品，不能互相替代。

## 1. 镜像漏洞修复结果及边界

| 制品 | 已验证结果 | 放行状态 |
| --- | --- | --- |
| 原 Bookworm `google-manager:final`，摘要 `496ae9e80b8cd7078014abaeee3d5d56d676828aa32afb4caabe5400270eeca0` | 6 Critical / 78 High；84 条包与 CVE 匹配，40 个不同 CVE | 不通过 |
| Ubuntu 完整候选 `google-manager:ubuntu26-candidate`，摘要 `f414e76a2a744739175e8338c1d5aedcc55323498067fecd2104b6e6e67e31f3` | 严格 Trivy 扫描 exit 0，0 Critical / 0 High；未使用忽略规则、VEX 或删除包元数据 | 仅该候选的扫描通过 |
| 中间修正版 `google-manager:ubuntu26-candidate-final`，摘要 `da6a0093c2c27a62a0c8c03900172d1b3b68968ccce0b1c449d79dede45ef697` | 构建成功；其后预检、备份等源码仍有整改，且未完成该制品复扫/SBOM/全链路验收 | 不得作为最终交付制品 |
| 当前工作区最终制品 | 尚需源码冻结后重建、同摘要扫描、SBOM、Linux 回归和服务验收 | 待确认 |

候选扫描证据：`.test-tmp/ubuntu26-candidate-audit.json`，SHA-256 `83ffa82a321c12f53c71eecddfcbed9028399c995c857dcc48217189f9491fb7`。扫描使用 Trivy 0.72.0，漏洞数据库更新时间为 `2026-09-20T07:08:21Z`。命令包含 `--scanners vuln --severity HIGH,CRITICAL --exit-code 1`，没有排除未修复漏洞。

[Dockerfile](../Dockerfile) 使用固定摘要的官方 Node 24.21.0 构建层、Python 3.11.16 构建层及 Ubuntu 26.04 LTS 运行层。升级受支持发行版解决原系统包告警；保留 dpkg 元数据，移除不需要的 curl、npm、pip/setuptools/wheel。CPython 中不使用且缺少 Tcl/Tk 的扩展从构建产物移除。该兼容性调整需要最终镜像重新验收。Ubuntu LTS 支持周期及包安全更新机制见[官方发布周期](https://ubuntu.com/about/release-cycle)和[官方安全更新说明](https://documentation.ubuntu.com/security/security-updates/)。

首版候选已验证 Python 原生模块、OpenSSL 3.5.5、SQLite 3.46.1、Node 动态链接、UID/GID 10001、运行目录 0700、SQLite/锁文件 0600，以及 Chromium 页面脚本和截图。**这些不能代替最终源码重建后的验证。**

Trivy 的上述结果也不能代替捆绑浏览器的安全版本审核。首版候选使用 Playwright 1.60.0，携带 Chromium `148.0.7778.96`；[Chrome 官方 2026-09-17 公告](https://chromereleases.googleblog.com/2026/09/stable-channel-update-for-desktop_0194356994.html)已发布 Linux `153.0.8010.52` 的安全修复。不能仅因 npm 包审计和系统包扫描为零，就认定该浏览器已涵盖这些修复。详见[浏览器安全基线](browser-security-baseline-2026-09-20.md)：Playwright 1.63.0 的默认浏览器仍为 `153.0.8010.12`，因此升级依赖之外，还需固定经摘要核验的 CfT 制品、统一全部启动入口，并完成独立兼容性验收。

## 2. 已修复的确定问题

| 优先级 | 位置与代码依据 | 触发条件及影响 | 已实施整改 / 验证 |
| --- | --- | --- | --- |
| P0 | `Dockerfile` 旧发行版运行库 | 原镜像严格扫描有 84 条高危/严重告警，旧包源未给出修复版 | 换受支持运行层；完整候选实扫 0/0，仍要求最终制品复扫 |
| P1 | `deploy/preflight.py::_decode_value` | dotenv 行尾注释在预检与 Compose 解释不同，密码或配置被截断 | 严格 dotenv 子集，拒绝不明确语法；回归覆盖注释、重复字段、展开及脱敏 |
| P1 | `deploy/preflight.py` 代理、数据库和挂载校验 | 公网/宽泛代理信任、内存库、错误凭据路径被误接受 | 规范私网/loopback 单地址 CIDR；只支持默认持久化 SQLite 和固定凭据挂载 |
| P1 | `deploy/preflight.py::_validate_release_manifest` 及制品文件校验 | 最小伪 manifest、错架构、缺证据或 BLOCKED 包被当作可发布 | 完整字段及真实文件哈希校验；目标架构匹配；准备包始终保留人工签字 pending，exit 2 |
| P1 | `deploy/preflight.py::_runtime_permissions` | 已有数据库、锁或任务文件错误属主、宽权限、符号链接导致启动失败或越界访问 | 有界、不跟随链接的目录树校验；拒绝特殊文件、错误 UID、非 0600/0700；Linux 专项验证 |
| P1 | `deploy/compose_release.py::run_operation` | shell 覆盖已验收的镜像/密钥；配置校验后 YAML 被替换 | 受控环境、固定可信 Compose 程序、私有配置快照；逐服务核对实际镜像和应用变量 |
| P1 | `deploy/compose_release.py::verify_approved_bundle` | 使用未签字、篡改或不同镜像的准备包执行启动/迁移 | 必须提供独立 GO 记录中的 manifest 哈希；同一字节做完整校验；核对文件清单及哈希，不自动批准 |
| P1 | `deploy/release_bundle.py` 扫描/SBOM/快照校验 | 仓库/摘要/架构错配、嵌套组件漏检、读后替换、伪 SPDX、敏感模板被打包 | 安全快照、严格身份与结构、递归许可清单、固定官方 SPDX 数据、模板及文件白名单、失败关闭 |
| P1 | `deploy/container-entrypoint.sh`、Gunicorn 配置 | 默认 umask 0022 创建 0644/0755 运行文件，既泄露权限又导致下次预检失败 | 所有容器入口及 Gunicorn 设置 umask 0077；新增真实 SQLite 与子进程权限回归 |
| P1 | `docker-compose.yml` Web 停止宽限期 | 默认约 10 秒强杀短于 Gunicorn 30 秒优雅退出，在途请求可能中断 | Web 与 worker 均设置 60 秒外层停止宽限期；启动使用 `--wait` 等待健康 |
| P1 | `deploy/preflight.py::_validate_oauth_file`、`_validate_pubsub_configuration` | 错误 OAuth 端点、缺失或弱 Pub/Sub token 被接受 | 精确 Google 官方端点；topic/token 成对及格式、强度校验；CLI runtime UID 固定 10001 |
| P1 | `deploy/backup_database.py::_load_key` | 宽权限、链接、非普通或超大 key 文件被读取 | 文件描述符及文件身份校验；Linux 必须 0600 且当前有效 UID；错误脱敏及回归 |
| P0 | `googlemail/src/browser-runtime.mjs`、四个浏览器入口、Python 子进程环境白名单 | 只在单个入口指定浏览器，worker 或启动检查仍可能使用旧浏览器；生产路径丢失后回退 | 统一绝对路径配置，生产缺失/冲突时拒绝启动；显式开启 sandbox，禁止两类禁用参数；传递固定路径和生产模式 |
| P0 | `Dockerfile`、`deploy/install_browser.py`、`deploy/browser-artifacts.json` | 默认 Playwright 捆绑浏览器补丁号仍低于安全基线 | 固定 CfT 153.0.8010.52 的两架构官方 URL/大小/SHA-256；构建校验后安装到独立只读路径，保留元数据；最终镜像运行验收仍阻塞 |
| P1 | `app/services/batch_oauth_service.py::_run_worker` | 原先复制全部父环境，后端密码和密钥被无关 Node 子进程继承 | 与 Googlemail 共用环境白名单；合成回归验证 `SECRET_KEY`、管理员密码、Fernet key、`NODE_OPTIONS` 不传递 |
| P1 | `googlemail/src/startup-check.mjs` 清理逻辑 | 浏览器关闭或临时 profile 删除失败仍打印通过并 exit 0 | 顺序尝试全部清理；任一步失败均输出固定脱敏错误并 exit 1；通过日志后移；mock 回归与真实双入口复验 |

恢复准备新增 [recovery_drill.py](../deploy/recovery_drill.py)：仅用临时合成库和随机独立密钥调用实际备份 CLI，验证备份、认证校验、恢复、schema、密文可读、错误 key 拒绝、已有文件拒绝覆盖及源库不变。原有 `tests/test_backup.py` 同时覆盖备份篡改拒绝，不以 mock 代替备份链路。

## 3. 验收证据

| 检查 | 结果 | 证据与限制 |
| --- | --- | --- |
| 当前源码 Python 全量测试 | 通过，Linux 专项待最终镜像复验 | `python -m unittest discover -s tests -p 'test_*.py'` 共 356 项：352 通过、4 项 Linux 专属检查跳过，耗时 86.378 秒；Windows Python 3.14.7，不能代替最终镜像 Python 3.11.16 验收 |
| 部署合同测试 | 通过 | 真实生成包与预检跨模块合同、Compose 配置核对、制品篡改、权限和密钥拒绝路径 |
| Linux 预检 | 通过 | 当前预检 25 项在 WSL 原生临时目录执行；它是 POSIX 行为证据，不是最终镜像验收 |
| Node 共享测试 | 通过 | `node --test tests/account-import.test.mjs tests/googlemail-local-copy.test.mjs tests/api-service.test.mjs`：41 项 |
| Googlemail 测试 | 通过但覆盖存在风险 | `npm --prefix googlemail run test:coverage`：71 项全通过；整体行覆盖 28.72%，共享浏览器模块行覆盖 92.15%；门禁通过，不代表真实账号流程已验收 |
| 前端构建与集成 | 通过 | Vite 生产构建基线成功；本轮使用 Playwright 1.63.0 + 显式固定 CfT 153.0.8010.52 / production 配置重跑 `node --test tests/frontend-ui.test.mjs`：43 项全通过，含取消、轮询、竞态、CSP、权限失效；固定路径失败时禁止回退其他浏览器 |
| 浏览器整改 Python 相关回归 | 通过 | Googlemail 适配器 14 项、Batch OAuth 9 项全通过；发布包/预检/Compose 同步增加浏览器材料后合同测试 52 项中 50 通过、2 项 POSIX 跳过；安装器 16 项全通过 |
| Googlemail npm 依赖审计 | 通过 | 官方 registry 下 `npm --prefix googlemail audit --json --audit-level=high` exit 0，105 个依赖的总告警数为 0；不代替浏览器和 OS 漏洞扫描 |
| 固定浏览器本地运行 | 通过，Linux 待确认 | Windows CfT `153.0.8010.52` 下 production 启动检查 exit 0：普通/临时持久化上下文、精确版本、JS=42、zh-CN、非空截图与清理；源码请求 `chromiumSandbox:true`，不据此推定 Linux sandbox 已生效 |
| CI 语法 / 差异检查 | 通过 | actionlint、Python 语法检查、`git diff --check`；CI 新增最终镜像内权限/部署工具回归和无网络恢复演练 |
| 源码秘密/配置扫描 | 通过 | Trivy secret/misconfig HIGH/CRITICAL exit 0；只扫项目源码，显式排除真实 `.env`、凭据、数据库、运行目录和依赖缓存 |
| 远端 CI、干净候选提交 | 待确认 | 未擅自提交/推送；当前工作树包含多轮未提交整改，不能生成正式已验收候选 |
| 最终镜像、SBOM、许可证 | 待确认 / 阻塞 | 最终制品仍需重建；旧镜像 SBOM 的 29 项缺许可元数据不能沿用为新镜像结论；元数据完整也不等于人工合规批准 |
| 目标服务器配置/TLS/容量 | 待确认 | 尚无 OS/CPU、域名、容量和受控配置证据；模板保留占位值，不能上线 |
| 真实上游、数据恢复、告警 | 待确认 | 授权 sandbox、目标数据副本迁移、RPO/RTO、异地备份及告警送达均需实际证据 |

浏览器整改前的源码扫描 JSON：`.test-tmp/predeploy-repository-audit-final.json`，SHA-256 `938e64c5c971c9e0f930f8f605c0826d91ec85966c686595814b6488f830f999`。此前 339 项 Python 全量基线日志 `.test-tmp/predeploy-windows-tests-final.log` 的 SHA-256 为 `c12e26592edd08eec40ae576bbf63d2a22c08f9f5a3be4c12d5a5ddfa55c71f6`；不要将它当作最新 356 项的日志。临时目录证据在正式发布前应移入受控制品库；不能仅保存本地路径。

本地浏览器验证使用[官方 win64 CfT 归档](https://storage.googleapis.com/chrome-for-testing-public/153.0.8010.52/win64/chrome-win64.zip)，SHA-256 `df4854428c509fcf2f790ac448bca3f994151bcff22008236fa7c4587294fbe1`，文件 ProductVersion 与实际 `browser.version()` 均为 `153.0.8010.52`。摘要为本次下载后自算；Windows Authenticode 检查显示该测试版可执行文件 `NotSigned`，没有将其表述为已通过数字签名验证。此归档仅用于本地兼容性验证，不放入 Linux 交付镜像。

Linux 制品已从官方 URL 完整下载，并由根代理独立复算摘要，匹配[固定清单](../deploy/browser-artifacts.json)：amd64 为 `195708470` 字节、SHA-256 `e66f66d4802a46d4a022667e668aa950e277cadbfbed4b3777915b47413a0ef9`；arm64 为 `195886869` 字节、SHA-256 `794441f3254273eb30d710b3eaab3cd1c7bbf1c88a40275470f6cee350881ce5`。下载和 ZIP 校验不代表相应架构已经运行通过。固定清单与浏览器安全基线已加入发布包和预检的共同文件白名单。

本轮新增代码与配置的增量 Trivy 检查均使用 HIGH/CRITICAL、`--exit-code 1`，实际退出 0、匹配数 0，未读取真实配置或账号数据。`app`、`googlemail/src`、`.github`、`deploy` 执行 secret/misconfig；Dockerfile 单独执行 config 扫描（识别到 1 个配置文件），CI YAML 语义另由 actionlint 验证。JSON 证据如下，不能替代最终镜像漏洞扫描：

| `.test-tmp/` 内证据 | SHA-256 |
| --- | --- |
| `browser-app-source-audit.json` | `393c4b6ede9931962b4bc63bebcdbd099e85e27aae12feac5b759cd756cde135` |
| `browser-node-source-audit.json` | `380b92304ad0b85a960421ee5bbe7bd3724b57777ca8dc1eeaf8923fcdb938dc` |
| `browser-ci-source-audit.json` | `86497ddf42bdad158a26c3312923b7a503f994eb4216d514ed6c830b188215ce` |
| `browser-deploy-source-audit.json` | `b705e607a16fe5fdae0dc9945e94d24798aa9c40a54ad48c566ead94692c6abc` |
| `browser-docker-config-audit.json` | `8caf56745c8a97baf769ec761b3fb02ef397ed8eaaee83ef93ee5a73c638651f` |

独立供应链安全审查已签收浏览器安装器、固定清单、Dockerfile 和对应测试范围，未发现剩余确定 P0/P1。其边界包括：自算摘要需保留采集来源；60 秒网络超时针对连接/单次 I/O，CI 总时限负责限制整体执行，本地构建仍应配置总超时；最终 Ubuntu 镜像内的动态链接、sandbox 与完整业务验证尚未完成。

独立代码审查也已签收四个启动入口、两类子进程环境隔离、依赖锁定、CI 及发布包清单的本轮增量，最初发现的清理失败误报成功问题已复验关闭。该签收限定于补丁范围，不等于项目总体 GO；CfT 精确组合例外、使用/分发条款和最终制品仍需完成验收。

测试日志仍有 SQLAlchemy `utcnow()` 弃用提示及测试连接释放的 `ResourceWarning`；没有据此判定生产已发生连接泄漏，也没有隐藏这些提示。建议维护者补齐相应测试 fixture 的连接清理并安排日期 API 迁移，作为非阻塞维护项跟踪。

## 4. 环境阻塞与恢复条件

本机 C 盘在构建时耗尽，Docker BuildKit 出现只读元数据错误。已原样迁移本任务的 Trivy 缓存约 1.417 GB：`C:/Users/www/AppData/Local/trivy` → `F:/Google_Manager/.test-tmp/trivy-cache`；后续扫描显式使用新 `--cache-dir`。

第二次构建后 C 盘再次耗尽。已仅清理本任务两层可重建的 BuildKit 缓存，各约 671.7 MB；可以重新构建恢复，未删除镜像、卷、源码、用户数据或 Codex 状态。Docker 数据 VHD 非稀疏文件，内部释放未返还宿主空间。当前进程缺少 Windows 管理员权限，不能执行 VHD 压缩；Docker 已停止，避免继续增长。已请求本机管理员先释放 C 盘至少 10 GB。不要手动删除 VHD、数据库或会话文件。

本轮最后复查 C 盘约剩 `1.09 GiB`，仍低于恢复构建要求；因此没有重启 Docker。新增下载、临时 profile、测试及 npm/Trivy 缓存都使用 F 盘，未将已有 Windows 动态测试替代 Linux 制品验收。

## 5. 上线前必须完成

| 优先级 | 行动及验收标准 | 负责人建议 | 时限 |
| --- | --- | --- | --- |
| P0 | 浏览器源码整改与 Windows 回归已完成；发布当天复查补丁基线，在最终 Linux 制品验证共享库、sandbox、两类启动及获授权完整流程 | 安全 + 自动化维护者 | 发布前 |
| P0 | 释放构建盘空间，冻结源码后生成同一最终制品，复扫 0/0、生成 SBOM、运行 Linux/Web/worker/恢复及权限回归 | 本机管理员 + 构建负责人 | 恢复构建前 / 发布前 |
| P0 | 候选提交与远端 CI 全绿，镜像按摘要交付；完整正式准备包和独立 GO 审批绑定原 manifest 哈希 | 技术负责人 + 发布负责人 | 发布前 |
| P0 | 若启用 live，提交 CDK 上游鉴权、幂等、unknown 对账、撤回/关闭的授权测试证据；否则保持 disabled | 后端 + QA + 上游方 | live 启用前 |
| P1 | 提供服务器 OS/架构、域名/TLS、受控配置和目录权限，预检仅剩人工签字 pending | 运维 | 发布前 |
| P1 | 新 SBOM 的许可/NOTICE/分发义务逐项审查，不将 SPDX 解析通过视为合规批准 | 安全/法务 + 维护者 | 分发前 |
| P1 | 目标库副本迁移/恢复演练、原密钥托管、RPO/RTO、回滚制品、unknown 副作用核对 | 数据库 + 运维 | 数据迁移前 |
| P1 | 按实际任务量压测，验收探活时限、磁盘/队列阈值、告警及恢复通知送达 | QA + SRE | 发布前 |

执行文档：[部署准备](deployment-preparation.md)、[服务器部署指南](server-deployment-guide.md)、[技术说明](deployment-technical-guide.md)、[发布签字模板](release-signoff-template.md)。这些材料已经落盘；外部证据缺失及最终制品未验收时，不生成或宣称可直接部署的正式发布包。
