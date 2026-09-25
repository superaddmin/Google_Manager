# 浏览器运行时安全基线（2026-09-20）

## 1. 范围与结论

本文记录 Google Manager 的 Playwright/Chromium 运行时、浏览器制品供应链和升级验收门槛。事实快照时间为 2026-09-20（Asia/Shanghai）；第 1–10 节中的仓库证据是整改前的研究快照，当时未下载 CfT、修改依赖或执行候选动态测试。后续源码整改和实际验证进度见[部署前验收状态](predeployment-status-2026-09-20.md)，不能把研究结论或 Windows 测试写成最终 Linux 制品已经通过。

结论：当前浏览器基线不满足生产发布要求，必须保持发布阻塞。

- **整改前事实**：仓库将 `playwright` 固定为 `1.60.0`，该版本的官方 `browsers.json` 将 Chromium/Chrome for Testing 固定为 `148.0.7778.96`。当时镜像构建执行 `playwright install chromium`，因此没有额外覆盖时会安装该捆绑版本。[Playwright 1.60.0 browsers.json](https://raw.githubusercontent.com/microsoft/playwright/v1.60.0/packages/playwright-core/browsers.json)
- **事实**：Google 于 2026-09-17 将 Linux Chrome Stable 更新为 `153.0.8010.52`。该补丁公告包含 16 项安全修复，其中 2 项 Critical、7 项 High、6 项 Medium、1 项 Low。[Chrome Stable 官方公告](https://chromereleases.googleblog.com/2026/09/stable-channel-update-for-desktop_0194356994.html)
- **推断**：`148.0.7778.96` 早于 `153.0.8010.52`，无法包含上述 153 补丁；当前浏览器制品不能因操作系统扫描为 0 就被视为无浏览器漏洞。浏览器版本审计必须独立于 OS/应用依赖扫描。
- **事实**：当前最新稳定 Playwright `1.63.0` 捆绑 Chromium `153.0.8010.12`，并声明测试过 Google Chrome 153；但捆绑补丁号仍低于 `153.0.8010.52`。[Playwright 1.63.0 发布说明](https://github.com/microsoft/playwright/releases/tag/v1.63.0)；[Playwright 1.63.0 browsers.json](https://raw.githubusercontent.com/microsoft/playwright/v1.63.0/packages/playwright-core/browsers.json)
- **推断**：只把依赖从 Playwright `1.60.0` 升级到 `1.63.0`，不能宣称 2026-09-17 公告中的 Critical/High 漏洞已关闭。
- **事实**：Chrome for Testing（CfT）官方 API 在 `2026-09-18T21:16:57.295Z` 给出的 Stable 是 `153.0.8010.52`，且 Linux x64/arm64 的 Chrome 与 headless shell 制品均可用。[CfT last-known-good 官方 JSON](https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json)

## 2. 整改前仓库证据

| 检查项 | 仓库证据 | 结论 |
| --- | --- | --- |
| Playwright 依赖 | [`googlemail/package.json`](../googlemail/package.json) 固定 `1.60.0`，锁文件同版本 | 不通过：捆绑 Chromium 148 |
| 浏览器安装 | [`Dockerfile`](../Dockerfile) 执行 `playwright install chromium` | 默认安装 Playwright 捆绑版本 |
| 运行平台 | [`Dockerfile`](../Dockerfile) 运行阶段为固定 digest 的 Ubuntu 26.04 | 当前 Playwright 1.60 官方支持矩阵不含 Ubuntu 26.04 |
| 自定义 channel | [`googlemail/src/main.mjs`](../googlemail/src/main.mjs) 可从 `CHROME_CHANNEL` 传入 channel | 仅覆盖单账号入口，不能代表所有 worker |
| sandbox | [`googlemail/src/batch-oauth-worker.mjs`](../googlemail/src/batch-oauth-worker.mjs) 显式传入 `--no-sandbox`、`--disable-setuid-sandbox` | 存在浏览器攻陷后的隔离面缩小风险 |

### 2.1 平台支持边界

- **事实**：Playwright 1.60.0 的官方系统要求是 Node.js 20/22/24，Debian 12/13、Ubuntu 22.04/24.04（x86-64 或 arm64），不包括 Ubuntu 26.04。[1.60.0 安装文档](https://raw.githubusercontent.com/microsoft/playwright/v1.60.0/docs/src/intro-js.md)
- **事实**：Playwright 1.63.0 要求 Node.js 22/24/26，并支持 Debian 12/13、Ubuntu 22.04/24.04/26.04（x86-64 或 arm64）。其注册表也为 Ubuntu 26.04 x64/arm64 定义了 CfT 下载路径。[1.63.0 安装文档](https://raw.githubusercontent.com/microsoft/playwright/v1.63.0/docs/src/intro-js.md)；[1.63.0 registry 源码](https://raw.githubusercontent.com/microsoft/playwright/v1.63.0/packages/playwright-core/src/server/registry/index.ts)
- **推断**：项目的 Node.js 24 与 Playwright 1.63.0 的 Node 要求相容；升级后仍必须在最终 Ubuntu 26.04 镜像中动态验证共享库、字体、sandbox、持久化上下文及退出清理，不能以支持矩阵代替实测。

## 3. 已知安全修复基线

`153.0.8010.52` 的 2026-09-17 官方公告列出的 Critical/High 如下。该表仅说明补丁公告内容，不表示本项目当前镜像已经包含修复。

| 严重度 | CVE | 组件/类型 |
| --- | --- | --- |
| Critical | CVE-2026-93374 | Dawn use-after-free |
| Critical | CVE-2026-93372 | WebGL buffer overflow |
| High | CVE-2026-93375 | Tracing incorrect reference resolution |
| High | CVE-2026-93382 | PDFium use-after-free |
| High | CVE-2026-93387 | Skia improper state validation |
| High | CVE-2026-93373 | Extensions use-after-free |
| High | CVE-2026-93381 | PDFium buffer overflow |
| High | CVE-2026-93379 | ORB incorrect authorization |
| High | CVE-2026-93377 | V8 type confusion |

唯一依据为 [Chrome Releases 官方 153.0.8010.52 公告](https://chromereleases.googleblog.com/2026/09/stable-channel-update-for-desktop_0194356994.html)。Google 说明漏洞细节可能在多数用户完成更新前保持受限，因此不能用“细节不可见”降低严重度。

## 4. 可用安全制品

CfT 官方 API 当前给出的精确 Chrome 制品如下；它们是 Chrome 完整浏览器，不是 `chrome-headless-shell`。

| 架构 | 版本 | 官方不可变版本 URL | 建议固定安装路径 |
| --- | --- | --- | --- |
| Linux x64 | `153.0.8010.52` | [chrome-linux64.zip](https://storage.googleapis.com/chrome-for-testing-public/153.0.8010.52/linux64/chrome-linux64.zip) | `/opt/google-manager/chrome-for-testing/153.0.8010.52/chrome-linux64/chrome` |
| Linux arm64 | `153.0.8010.52` | [chrome-linux-arm64.zip](https://storage.googleapis.com/chrome-for-testing-public/153.0.8010.52/linux-arm64/chrome-linux-arm64.zip) | `/opt/google-manager/chrome-for-testing/153.0.8010.52/chrome-linux-arm64/chrome` |

建议业务专用环境变量名为 `GOOGLE_MANAGER_CHROME_EXECUTABLE_PATH`。不要把外置 CfT 解压到 Playwright 的 `/ms-playwright/chromium-*` 注册表目录，也不要手工修改 `browsers.json`；这会伪造 Playwright 对浏览器 revision 的所有权并使缓存清理、升级和故障诊断失真。

### 4.1 amd64/arm64 边界

- `linux/amd64` 必须使用 `linux64/chrome-linux64.zip` 和 `chrome-linux64/chrome`，`linux/arm64` 必须使用 `linux-arm64/chrome-linux-arm64.zip` 和 `chrome-linux-arm64/chrome`，二者不得交叉复用。
- CfT 官方仓库说明 Linux arm64 从 `153.0.8001.0` 开始提供，因此 `153.0.8010.52` 是本次可用的首批 arm64 Stable 系列；x64 和 arm64 下载可用性均以 [CfT 官方 API](https://github.com/GoogleChromeLabs/chrome-for-testing) 为准。
- 两个架构的归档内容不同，必须分别计算和审批 SHA-256，分别生成 SBOM/制品证明，并对各自最终 OCI manifest digest 执行第 8 节测试。amd64 通过不能替代 arm64 验收。
- 如果本次只交付 `linux/amd64`，镜像 manifest 和发布清单必须明确只含 amd64；不得仅因 Dockerfile 使用多架构基础镜像就宣称 arm64 已支持。

## 5. 兼容性边界与升级路线

### 5.1 官方兼容事实

- Playwright 明确说明每个版本需要特定浏览器二进制；升级 Playwright 后通常需要重新执行浏览器安装。[Browsers 文档](https://playwright.dev/docs/browsers)
- Playwright 1.63.0 的发布说明声明捆绑 Chromium `153.0.8010.12`，并测试过 Google Chrome 153 Stable channel。[1.63.0 发布说明](https://github.com/microsoft/playwright/releases/tag/v1.63.0)
- Playwright 文档说明当前版本支持 Chrome/Edge Stable 和 Beta channel；但 `executablePath` 指向非捆绑浏览器时“不保证工作”，应谨慎使用。[Browsers 文档](https://playwright.dev/docs/browsers)；[BrowserType API](https://playwright.dev/docs/api/class-browsertype)

### 5.2 唯一推荐方案

本次发布唯一推荐的候选是：**Playwright `1.63.0` + 固定 CfT `153.0.8010.52` + 自行计算并门禁 SHA-256 + `GOOGLE_MANAGER_CHROME_EXECUTABLE_PATH`/`executablePath` 显式选择**。

1. 先升级 Playwright 到稳定的 `1.63.0`，以获得 Ubuntu 26.04 官方平台支持和 Chrome 153 协议兼容基础。
2. 按目标架构从第 4 节的官方不可变版本 URL 获取 CfT `153.0.8010.52`，安装到独立固定目录，不覆盖 Playwright registry。
3. 首次受控获取时自行计算 SHA-256；后续构建对摘要 fail closed。构建后必须验证浏览器 `--version` 精确等于批准版本。
4. 由全部生产入口读取 `GOOGLE_MANAGER_CHROME_EXECUTABLE_PATH` 并传给 `executablePath`；不能只改当前支持 `CHROME_CHANNEL` 的单账号入口。
5. 该组合主版本同为 153，且 Playwright 1.63.0 声明测试过 Chrome 153，具有合理兼容基础；但它仍属于 `executablePath` 例外。“合理兼容”是推断，不是官方对该精确组合的保证，必须完成第 8 节全量验收并由发布负责人签署。
6. 长期应升级到首个官方 `browsers.json` 捆绑 Chromium/CfT `>=153.0.8010.52` 的稳定 Playwright 版本，然后删除外置路径例外，回归由同一 Playwright 版本管理浏览器 revision、驱动协议和安装器。

`channel: 'chrome'` 虽是官方支持方式，但系统 Chrome 安装可能随仓库更新漂移；只设置现有 `CHROME_CHANNEL` 而不固定安装制品和摘要，不是本次推荐方案。任何候选失败都应等待官方捆绑版本，不能修改 `browsers.json`、用软链接覆盖旧浏览器或关闭测试门禁。

不得采用 Playwright alpha/beta、Chrome Beta/Dev/Canary 作为生产修复。预发布版本可用于前置兼容测试，不能替代 Stable 发布制品。

## 6. 供应链固定与校验

### 6.1 npm 包

Playwright `1.63.0` 的 npm 官方 registry 元数据如下；升级时必须由 `package-lock.json` 固定精确版本、resolved URL 和 SRI，不得使用 `^1.63.0`、`latest` 或在镜像构建中临时执行不落锁的安装。

| 包 | npm SRI (`sha512`) | SHA-1（仅 registry 对照，不作为安全摘要） |
| --- | --- | --- |
| `playwright@1.63.0` | `sha512-+7ziBLidS4NaNCdt57SUDT+wYmmd5fmiQejUic/kb+YsYSCPyOOE9sebzMjNmQrsnNpDJqd4WHvV/8lfKfUDUg==` | `99b56f9f69b1b70c44f00bf84b2fe52348ae2511` |
| `playwright-core@1.63.0` | `sha512-rYCsBF/M5HjUch52bbtVONEFjv6Xu8sm8h72dNlR5bzIE1fvC/bxgspzkjSfU+MweEMmPM8KJebG6nnyxo5mCg==` | `e57665bc32846c213ac39a1e4d5bc6228e76b376` |

官方元数据：[playwright 1.63.0](https://registry.npmjs.org/playwright/1.63.0)、[playwright-core 1.63.0](https://registry.npmjs.org/playwright-core/1.63.0)。实际验收以新生成锁文件中的 SRI 与 `npm ci` 校验结果为准。

### 6.2 浏览器归档

- **事实**：CfT `last-known-good-versions-with-downloads.json` 发布版本和下载 URL，但不发布 SHA-256 字段。[CfT API 说明](https://github.com/GoogleChromeLabs/chrome-for-testing)
- **事实**：Playwright 1.63.0 下载器源码校验 HTTP 200 和非 chunked 响应的 Content-Length，然后直接解压；代码未传入或比较归档密码学摘要。[下载器源码](https://raw.githubusercontent.com/microsoft/playwright/v1.63.0/packages/playwright-core/src/server/registry/oopDownloadBrowserMain.ts)
- **推断/风险**：仅依赖 TLS、版本 URL 和长度不足以形成独立的内容完整性证明；`PLAYWRIGHT_DOWNLOAD_HOST` 或代理配置漂移也可能改变下载来源。

临时 CfT 方案必须执行以下控制：

1. 在受控、无 TLS 中间人替换的构建环境从上表官方 HTTPS URL 获取一次制品。
2. 计算 SHA-256，将 `版本 + 架构 + URL + Content-Length + SHA-256 + 获取时间` 写入受审发布清单；由第二人复核。
3. 后续构建仅接受该 SHA-256，摘要不一致立即失败；不得自动接受重传制品。
4. 最终镜像再次计算浏览器主可执行文件和原始归档（若保留）的 SHA-256，并写入制品证明/SBOM附件。
5. 固定最终 OCI 镜像 digest；发布与回滚均使用 digest，不使用可变标签。
6. 禁止生产构建设置未经批准的 `PLAYWRIGHT_DOWNLOAD_HOST`、`PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST` 或自定义 npm registry。

上述 SHA-256 是项目在首次受控获取后建立的组织级固定值，并非 Google 发布的厂商签名/摘要。必须在发布证据中保留这一限制。

## 7. Sandbox 与容器基线

- **事实**：Playwright `chromiumSandbox` 默认值为 `false`；自定义参数也可能破坏 Playwright 功能。[BrowserType API](https://playwright.dev/docs/api/class-browsertype)
- **事实**：本项目批量 OAuth worker 还显式传入 `--no-sandbox` 和 `--disable-setuid-sandbox`。
- **事实**：Playwright 官方容器文档对访问不受信网站建议使用独立非 root 用户并配合允许 user namespace 的 seccomp profile；同时建议 Chromium 容器使用合适的 IPC 配置。[Playwright Docker 文档](https://playwright.dev/docs/docker)

生产整改目标：以 UID 10001 非 root 运行，删除禁用 sandbox 的参数，并在目标 Docker/内核/seccomp 组合上验证 `chromiumSandbox: true`。如果目标宿主暂时不能启用 Chromium sandbox，必须形成书面风险接受、严格 URL/出口限制、只读根文件系统、最小 capabilities、独立 worker、资源限制和补丁 SLA；不能仅以“容器已非 root”认定等价隔离。

### 7.1 目标服务器实测记录（2026-09-24）

在 `123.206.210.86` 的 Ubuntu 24.04.4、Docker Engine 29.1.3 环境中执行：

```bash
docker run --rm --init --network none \
  google-manager:online-test \
  node googlemail/src/startup-check.mjs
```

当时结果为失败，日志为 `No usable sandbox`；仅凭提示不能确认是宿主 AppArmor 导致。2026-09-25 同一服务器增加 `--security-opt seccomp=deploy/chromium-seccomp.json --shm-size=256m` 后检查通过；宿主 AppArmor/sysctl 未变更，没有增加 capabilities、特权或关闭 sandbox。该 profile 来自固定 Playwright 版本，来源、摘要和命令见 [人工配置手册](production-manual-configuration.md)。B02 的目标测试运行缺陷已修复；正式不可变镜像 digest 仍需重跑并保存证据。

## 8. 必须实测的发布门禁

以下项目均需针对最终同一个不可变镜像 digest 执行。本文未执行这些集成测试，状态均为“待验证”；历史镜像、构建中间层或本地主机的结果不能替代最终候选证据。

| 门禁 | 验证内容 | 通过标准 |
| --- | --- | --- |
| 版本一致性 | `npm ls playwright playwright-core`、CLI version、`browsers.json`、浏览器 `--version` | 依赖和浏览器均为批准的精确版本；浏览器不低于当前批准安全版本 |
| 平台/架构 | Ubuntu 26.04 x64；如交付 arm64，需独立构建和运行 | 无 unsupported/fallback 警告；`ldd` 无 `not found` |
| 制品完整性 | npm SRI、CfT SHA-256、最终镜像 digest | 与签署清单逐字节一致 |
| 同一 digest 安全证据 | 对最终候选 digest 执行 `trivy image --scanners vuln --severity HIGH,CRITICAL --exit-code 1 <digest>`，并以同一 digest 生成 CycloneDX SBOM、执行浏览器 `--version` | Trivy 严格门禁退出码为 0；SBOM、扫描报告和版本输出均记录相同 OCI digest；浏览器精确等于批准版本 |
| 非 root 与权限 | UID/GID、浏览器目录权限、profile/runtime 目录 | 浏览器制品只读；仅 profile/runtime 可写；无 root 启动 |
| Sandbox | 检查最终浏览器命令行、sandbox 状态及容器 seccomp | 无 `--no-sandbox`/`--disable-setuid-sandbox`；sandbox 可用且任务能完成；否则有批准的例外和补偿控制 |
| 两类启动 API | 分别覆盖批量 worker/启动检查使用的 `chromium.launch()`，以及主流程使用的 `chromium.launchPersistentContext()` | 两类 API 都使用批准的 `executablePath`，都能启动、导航、执行 JavaScript、截图并关闭；不得只验证其中一种 |
| 启动 smoke | 通过两类启动 API 加载本地静态页，执行 JavaScript，截图并退出 | 页面、JS、截图均成功，退出后无残留浏览器/僵尸进程 |
| OAuth mock 与回调 | 执行 OAuth mock 正常、拒绝、超时和异常路径，并验证 callback origin/path、state、成功响应及账号匹配 | 所有 mock 和回调边界测试通过；回调不匹配时 fail closed；日志不泄露 OAuth URL/token |
| Googlemail 测试 | 在最终候选镜像运行 `npm --prefix googlemail test` | 当前 61 项全部通过、0 失败；若测试数增加则新增项也必须全部通过，不能只抽取 61 项 |
| 应用回归 | `startup-check.mjs`、Python 全量、Web/worker smoke | 全部通过；无新增未处理异常/资源泄漏 |
| 状态隔离 | 多任务并发、独立 context、cookie/cache/profile 隔离 | 不串号、不复用其他账号状态，失败任务可清理 |
| Profile 生命周期 | persistent profile 首次创建、重复使用、并发占用拒绝、正常退出和异常/超时清理 | 创建目录权限正确；不串号；锁和临时文件受控清理；不误删其他任务 profile |
| 代理与网络 | 无代理和批准代理两种路径，以及 DNS/IPv4/IPv6、超时/中断 | 批准代理下 OAuth mock/回调可完成；失败受控、日志脱敏、无无限重试；浏览器仅能访问批准目标 |
| locale/timezone | 两类启动 API 下验证 `zh-CN` 与 `Asia/Shanghai` | 页面读取值与配置一致，日期/时间和选择器行为无回归 |
| 字体渲染 | 中文、英文和业务页面常用字符截图及字体加载检查 | 无缺字、乱码、替代方框或布局溢出；截图留作同 digest 证据 |
| 下载能力 | 正常下载、取消、超时、重名和任务退出清理 | 文件落在受控目录，权限正确；失败不遗留敏感临时文件；任务之间不串文件 |
| 页面能力 | OAuth 页面、弹窗/重定向、PDF/WebGL（如业务触达） | 与旧版本行为一致，关键选择器和回调无回归 |
| 信号与容量 | SIGTERM/SIGINT、worker 超时、并发上限、`/dev/shm`/内存/CPU | 优雅退出，无孤儿进程；容量达到签署阈值 |
| 安全复核 | 查询发布日最新 Chrome Releases/CfT JSON | 没有比批准版本更新且尚未处置的 Stable Critical/High 公告 |

真实 Google OAuth/账号流程必须使用专用验收账号并由获授权人员执行；本地静态页 smoke 不能替代该验收。不得在 CI 日志中输出账号、密码、恢复信息、Cookie、OAuth URL 或 token。

## 9. 更新触发、SLA 与自动门禁

### 9.1 触发条件

- Chrome Releases 发布 Stable 安全更新；
- CfT Stable 版本发生变化；
- Playwright 发布新的 stable 或修改 `browsers.json`；
- Node/Ubuntu 基础镜像 digest、目标架构或 seccomp 配置变化；
- 浏览器启动、OAuth、选择器、WebGL/PDF、下载或退出清理出现回归。

### 9.2 时限建议

| 情形 | 负责人建议 | 时限 |
| --- | --- | --- |
| Critical 或已知在野利用 | 安全负责人 + 平台负责人 | 4 小时内评估，24 小时内完成补丁候选和验收；未完成则停止发布/暂停相关自动化 |
| High | 安全负责人 + Googlemail 模块负责人 | 1 个工作日内评估，3 个工作日内升级并验收 |
| Playwright stable/平台变更 | 模块负责人 + CI 负责人 | 5 个工作日内完成兼容分支验证 |

### 9.3 自动化规则

安全监测任务可以读取官方 API 产生升级 PR，但正式构建不得在未审查时自动追随 `latest`。建议提交一个经审核的浏览器批准清单，并执行：

1. 比较官方 Stable 与清单版本；出现新版本或新 Critical/High 时令发布门禁失败并创建待办。
2. 检查 `package-lock.json` 精确版本/SRI、Playwright `browsers.json` 和最终浏览器版本；任一不一致即失败。
3. 检查最终进程参数没有禁用 sandbox 的 flag，并校验运行用户不是 root。
4. 对浏览器制品和 OCI 镜像计算 SHA-256/digest，关联 SBOM、测试报告和批准人。
5. 浏览器 CVE 基线单独判定，不以 Trivy 的 OS/Node/Python `0` 告警代替。

## 10. 上线关闭条件与回滚

只有以下证据齐全才能关闭浏览器安全阻塞：

- 最终镜像中的浏览器版本等于已批准版本，且不低于 `153.0.8010.52`；发布当天没有未处置的更新 Stable Critical/High 公告。
- Playwright/浏览器属于官方捆绑组合，或临时 CfT `executablePath` 例外已由安全、模块、发布三方签署。
- npm SRI、CfT 组织级 SHA-256、OCI digest、SBOM 和构建日志可互相追溯。
- 第 8 节门禁全部通过，包含 Ubuntu 26.04 最终镜像、目标架构、sandbox、并发、退出清理和获授权真实流程。
- 已准备上一份“仍满足当前安全基线”的镜像 digest。已知低于安全基线的 Chromium 148 镜像不能作为常规回滚目标；候选失败时应停止发布，而不是回退到已知脆弱浏览器。

当前签署状态：**不通过，暂缓发布**。可立即实施的最小候选是“Playwright 1.63.0 + 固定 CfT 153.0.8010.52 + 受控摘要 + 全门禁验收”，但在动态验收和例外签署完成前不得写成“已修复”。
