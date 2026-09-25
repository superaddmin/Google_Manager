# CDK 工作台部署前准备

本轮只准备 CDK 工作台，现金支付另行排期。所有服务器操作都应针对已确认的目标环境执行。模板和本机测试不代表生产验收完成；最终以[发布签字单](release-signoff-template.md)记录 GO / NO-GO。

## 1. 交付物与职责

| 交付物 | 用途 | 验收方式 |
| --- | --- | --- |
| 不可变镜像摘要 | 将同一已测试制品部署到服务器 | 镜像摘要、平台与扫描/SBOM 一致 |
| `deploy/env.production.example` | 完整配置模板，默认 disabled | 替换占位符后通过预检，不包含真实密钥 |
| `deploy/preflight.py` | 只读、脱敏的配置/文件权限检查 | 1 为失败，2 为待确认；正式准备包保留人工签字待确认，不自动退出 0 |
| `deploy/compose_release.py` | 固定配置、可信执行器和审批摘要绑定 | 配置与制品一致；启动等待服务健康 |
| `deploy/recovery_drill.py` | 最终镜像内部合成数据恢复演练 | 无网络运行，退出 0，schema/密文/拒绝覆盖检查通过 |
| `deploy/release_bundle.py` | 收集白名单运维文件、证据与校验和 | 正式模式严格门禁通过；草稿始终 BLOCKED |
| 扫描、SBOM、许可清单 | 安全和分发审核证据 | HIGH/CRITICAL 清零，缺失许可逐项确认 |
| 固定浏览器清单与双入口检查 | 防止默认浏览器版本落后于安全补丁基线 | `deploy/browser-artifacts.json` 的下载摘要、实际版本、普通和持久化启动均核验 |
| [部署指南](server-deployment-guide.md)与[技术说明](deployment-technical-guide.md) | 发布、迁移、运维、回滚步骤 | 实际执行人逐项签收 |

## 2. 配置与服务器预检

预检只读取显式指定文件，不自动加载仓库 `.env`，不连接上游，不创建目录、不迁移数据库、不启动服务。仅打印字段名、固定错误分类和检查状态；不要在参数或问题反馈中贴出密钥值。

```bash
sudo python3 deploy/preflight.py --env-file /opt/google-manager/.env \
  --project-root /opt/google-manager --pretty
```

支持严格 `KEY=VALUE` 与完整单双引号，禁止变量展开、命令替换、重复字段和未知字段。配置中不使用行尾注释，把说明写在独立注释行。禁止将展开后的含密钥配置输出到日志。

预检必须在完整发布准备包目录执行，缺少 `manifest.json` 返回待确认；BLOCKED 包、镜像不匹配、目标 CPU 架构不匹配均不能放行。完整准备包仍为 `AWAITING_MANUAL_SIGNOFF`，预检返回 2，明确保留人工签字项；这不等同配置错误，也不等同允许启动。当前 Compose 预检仅支持默认持久化 SQLite：`DATABASE_URL` 留空或设为 `sqlite:////app/instance/accounts.db`，OAuth 宿主文件固定为包目录的 `credentials.json`。

预检按 Compose 的 UID `10001` 检查 OAuth 文件及 `instance/`、`googlemail/runtime/`、`googlemail/output/` 和其既有子项权限，拒绝符号链接、特殊文件、跨设备项和硬链接。停止写入服务后运行 `deploy/prepare-compose-host.sh`；脚本拒绝覆盖固定 UID/GID，先检查已有目录树，再将目录/普通文件收敛为 `0700/0600`、`10001:10001`，`.env` 为 `root:root/0600`。操作系统错误可能留下部分权限变更，应停服排错重跑。Windows 不能验证 Linux 权限，会返回待确认。静态检查不证明 DNS/TLS、容量、上游、镜像可拉取或告警送达；这些仍须签字单证据。

生产 Compose 不包含构建入口，`GOOGLE_MANAGER_IMAGE` 必须指定已验收镜像。目标机使用受控入口，它清除继承的应用变量、`COMPOSE_*`、远程 Docker 覆盖项及隐式 `.env`，使用明确配置解析值，并在执行前核对三个服务的镜像及所有应用变量。配置快照和实际执行使用同一环境；含密钥的 Compose 输出只在内存解析，失败时只输出错误码。不要叠加 `docker-compose.build.yml`。

```bash
python3 deploy/compose_release.py config --env-file .env --project-root .
python3 deploy/compose_release.py pull --env-file .env --project-root .
# 以下值必须由负责人从独立、已完成 GO 审批的发布记录提供，不能自行从包内取值代替批准
read -r -p '已批准的 manifest SHA-256: ' approved_manifest_sha256
python3 deploy/compose_release.py up --env-file .env --project-root . \
  --approved-manifest-sha256 "$approved_manifest_sha256"
```

`up`、`init-db` 和敏感字段迁移命令要求上述独立摘要，校验 manifest、`SHA256SUMS` 与 manifest 绑定的文件/证据。工具不生成 GO，也不推断人工审批；正式签字记录放在包外，保持原包及 manifest 不变。当前草稿不能运行这些命令。入口仅操作默认本机 Docker，使用远程 daemon/context 的部署必须另行验收。

目标机运维命令由受控 root 会话执行，才能读取 UID 10001、0600 的 OAuth 文件及受限运行目录；不要为普通账号读取而放宽这些文件权限。启动器直接使用系统安装的、root 拥有且不可由组/其他用户改写的 Compose 二进制，支持 `--compose-binary /绝对路径/docker-compose`。它不加载用户级 CLI 插件、继承 PATH、HOME 或 Docker context。私有仓库需要显式 `--docker-config /etc/google-manager/docker`，该目录由 root 管理，`config.json` 为 0600，仅允许 registry auths/credential helpers 配置；不包含 context 或额外插件路径。未指定时使用临时空认证配置，适用于无需认证的本机已有镜像/公开镜像。启动使用 `--wait --wait-timeout 180`，服务未健康则返回失败。

构建环境使用以下入口：

```bash
docker build --pull --tag google-manager:candidate .
```

基础镜像 digest 和锁文件不能冻结 apt 仓库及全部传递依赖，不能承诺重新构建得到相同字节。采用“构建一次，按摘要交付原制品”，保留镜像摘要、平台、构建证据及扫描时间。

浏览器采用 Playwright `1.63.0` 与固定 Chrome for Testing `153.0.8010.52` 的临时组合，来源与验收边界见[浏览器安全基线](browser-security-baseline-2026-09-20.md)。构建按浏览器清单的精确 URL 和 SHA-256 校验后安装，不自动追随最新版本。镜像内 `GOOGLE_MANAGER_CHROME_EXECUTABLE_PATH` 固定为 `/opt/google-manager/chrome/chrome`；它是镜像构建配置，不是生产 `.env` 可任意覆盖的字段。

生产浏览器启动缺少路径、指定相对路径或同时设置 `CHROME_CHANNEL` 时失败关闭。所有入口显式启用 Chromium sandbox；目标宿主若不支持所需 namespace/seccomp，必须修正并重新验收，不能加入 `--no-sandbox`、特权容器或关闭安全检查绕过。最终镜像必须执行以下检查；它只使用本地页面和新建临时 profile，不读取账号文件或触发登录：

```bash
docker run --rm --init --network none --shm-size=256m \
  --security-opt seccomp=deploy/chromium-seccomp.json google-manager:candidate \
  node googlemail/src/startup-check.mjs
```

检查应证明实际版本精确匹配、两类启动 API、JavaScript、截图和 locale 正常，并完成资源清理。本地主机测试不能代替该镜像/目标架构的检查；也不能代替获授权账号的真实 OAuth 验收。安装器的自算 SHA-256 用于锁定已取得的官方制品，不能写成 Google 额外提供的签名或独立安全认证。

2026 年 9 月 24 日目标 Ubuntu 24.04.4 默认容器检查报 `No usable sandbox`。9 月 25 日加入仓库内 Playwright 官方有限 seccomp profile 后，两类启动检查在同一服务器通过；未修改宿主 AppArmor/sysctl，之前仅凭浏览器提示归因 AppArmor 不够准确。Compose Web/worker 和 CI 已统一引用该 profile。正式 registry digest 仍需复验；操作步骤见 [人工配置手册](production-manual-configuration.md)。

## 3. 隔离恢复演练

```bash
docker run --rm --network none google-manager:candidate python -m deploy.recovery_drill
```

演练会在自身临时目录创建完整 schema、合成敏感密文和独立随机备份 key，调用实际备份 CLI 完成 backup → verify → restore，核对 schema、SQLite 完整性、字段可解密、错误 key 拒绝、已有目标拒绝覆盖、源库不变，以及 Linux 0600 权限。临时目录退出时清理，不接触宿主真实数据。输出仅为摘要和检查结果。

该结果是制品功能证明，不是生产 RPO/RTO。实际发布前仍需使用获授权的目标库副本，在目标磁盘/权限环境验证恢复耗时、恢复点、异地副本和未知上游操作核对流程。

## 4. 发布准备包

发布工具只收集白名单文件和明确指定的扫描/SBOM，不收集 `.env`、凭据、数据库、备份密钥、日志或运行目录。正式准备包必须与不可变镜像、干净候选提交和 CI 证据绑定，不能用 tag 或手工修改扫描报告代替。

镜像安全、许可证、候选提交或外部验收尚缺时，只允许生成目录名和 manifest 明确标为 BLOCKED 的草稿。草稿供审核准备，不用于部署。签字单默认 NO-GO，即使准备包完整也必须由负责人核验目标环境后决定放行。

发布前在新目录验证 `SHA256SUMS`，检查 manifest 与所拉镜像摘要一致。扫描报告和 SBOM 应放入受控制品库长期保存，不能只留在 `.test-tmp/`。若需离线镜像传输，另对完整镜像归档计算 SHA-256 并在目标机核验后 load，再核实运行镜像身份；当前工具不自动上传、推送或部署。

## 5. 尚需目标环境提供的输入

| 输入/证据 | 负责人建议 | 放行要求 |
| --- | --- | --- |
| OS/CPU 架构、资源、预计账号量与并发 | 运维 + QA | 对应架构镜像实测；容量与探活超时验收 |
| 域名、DNS、证书、Nginx 完整拓扑 | 运维 | 公网 TLS 正常；健康路径禁止公网；无宽泛代理信任 |
| 生产密钥、OAuth 配置及目录权限 | 安全 + 运维 | 密钥受控保管、与存量一致；preflight 通过 |
| 目标库副本、异地备份、RPO/RTO | 数据库 + 运维 | 实际恢复演练及回滚制品齐备 |
| CDK 上游协议和授权 sandbox | 后端 + 上游方 + QA | 鉴权、幂等、unknown、撤回/关闭全部有记录 |
| 告警渠道、接收人和故障/恢复通知 | SRE | 实际送达证据，不能仅有 journal/timer |
| 候选提交、远端 CI、许可证审核 | 技术负责人 + 安全 | 每项通过；无缺失证据推定通过 |

上述输入未提供时保持 NO-GO 和 `RECHARGE_MODE=disabled`，不得将“部署准备工具已完成”表述为“可以上线”。
