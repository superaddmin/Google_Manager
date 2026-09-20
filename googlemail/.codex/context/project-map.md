# Googlemail 项目上下文图谱

## 摘要

Googlemail 是一个 Node.js ESM + Playwright 自动化项目，用于在授权范围内批量处理 Google 账号安全设置：读取账号文件，使用旧 TOTP 登录，通过浏览器自动化关闭并重新设置 2FA，可选修改恢复邮箱，并记录进度、结果、日志和调试截图。

## 技术栈

| 领域 | 技术 |
| --- | --- |
| 运行时 | package 下限 Node.js 20.19+；生产浏览器运行时 Node.js 24.x |
| 模块系统 | ESM (`type: module`, `.mjs`) |
| 浏览器自动化 | Playwright 1.63.0；生产固定 Chrome for Testing 153.0.8010.52 |
| TOTP | `otplib` 13.4.1 |
| 密钥生成 | `generateBase32Secret()` + Node.js `crypto.randomBytes(20)` |
| 配置 | `src/config.mjs` + 环境变量 |
| 文档 | Markdown 中文文档 |

## 核心依赖

- `playwright` 1.63.0：浏览器自动化、Chromium 控制、截图、持久化上下文。生产环境通过受控绝对路径选择 Chrome for Testing 153.0.8010.52。
- `otplib`：使用 v13 同步 API 与默认 Base32 解码路径生成/验证 TOTP。
- Node 内置：`fs`、`path`、`crypto`。

## 数据流

```text
宝贝信息-*.txt
  └─ parseAccounts(file)
      └─ accounts[]
          └─ main.mjs 逐账号处理
              ├─ loginAndChange2FA(page, account, log, targetRecoveryEmail)
              │   ├─ 登录邮箱/密码
│   ├─ oldSecret -> generateTOTP()
              │   ├─ 关闭旧 2FA
              │   ├─ 生成 newSecret 并尝试设置新 2FA
              │   └─ 可选 changeRecoveryEmail()
              ├─ saveProgress(output/progress.json)
              ├─ appendResult(output/result.txt)
              ├─ 关键中断 -> output/manual-review.jsonl
              └─ append log(output/logs/run-*.log)
```

## 模块边界

### `src/config.mjs`

- 默认账号文件：`宝贝信息-114260514183055495.txt`，可由 `ACCOUNTS_FILE` 覆盖。
- 运行输出：`output/progress.json`、`output/result.txt`、`output/manual-review.jsonl`。
- 环境变量：`HEADLESS`、`SLOW_MO`、`ACCOUNT_DELAY`、`RECOVERY_EMAIL_POOL`、`ACCOUNTS_PER_RECOVERY`。
- 浏览器 profile：`browser-data/`。

### `src/browser-runtime.mjs`

- 统一生成 `chromium.launch()` 与 `chromium.launchPersistentContext()` 的浏览器启动参数。
- 导出 `EXPECTED_CHROME_VERSION`、`getBrowserLaunchOptions()`、`assertExpectedChromeVersion()` 与启动检查清理函数 `cleanupBrowserStartup()`。
- `FLASK_ENV=production` 时要求 `GOOGLE_MANAGER_CHROME_EXECUTABLE_PATH` 为绝对路径；缺失或与 `CHROME_CHANNEL` 同时设置时拒绝启动。
- 强制 `chromiumSandbox: true`，拒绝调用方覆盖浏览器路径、channel、sandbox 或传入 `--no-sandbox`/`--disable-setuid-sandbox`。
- 非生产开发环境未固定路径时仍可使用 Playwright 捆绑浏览器，但该回退不构成生产安全验收证据。

### `src/account-parser.mjs`

- 每行必须有 4 段：`email----password----recoveryEmail----oldSecret`。
- 负责进度 JSON 读写与结果追加。
- 风险点：直接处理密码和 TOTP 密钥，日志/错误输出必须脱敏。

### `src/redaction.mjs`

- 统一遮蔽邮箱、6 位验证码、Base32 密钥和调用方提供的已知敏感值。
- 用于批量日志、单账号调试与自动化流程异常信息。

### `src/totp.mjs`

- `generateSync`/`verifySync`：30 秒、6 位、SHA1，验证使用 `epochTolerance: 60`。
- Base32 密钥解码后必须至少 16 字节；固定时间测试使用秒级 `epoch`。
- `generateBase32Secret()` 输出无填充 RFC4648 Base32 密钥，用于新 2FA 密钥生成。
- 风险点：不要在普通日志中输出完整 token 或完整 secret。

### `src/google-automator.mjs`

- 最大核心模块，包含登录、2FA、恢复邮箱流程。
- 主要函数：`loginAndChange2FA()`、`changeRecoveryEmail()`、`handlePasswordReverify()`。
- 风险点：真实账号状态变更、截图可能含账号信息、选择器易随 Google 页面变化。

### `src/main.mjs`

- 批量入口，负责运行日志、进度、结果与浏览器上下文。
- 通过共享浏览器运行时启动持久化上下文；支持 `PROXY`，非生产环境可选 `CHROME_CHANNEL`。
- 生产环境必须使用 `GOOGLE_MANAGER_CHROME_EXECUTABLE_PATH`，且不得同时设置 `CHROME_CHANNEL`。
- 风险点：`output/result.txt` 包含密码与新 TOTP 密钥。

### `src/test-login.mjs`

- 单账号调试入口，固定 headed、slowMo 300，执行真实流程后保持浏览器打开。
- 使用共享浏览器运行时；生产模式同样要求固定浏览器绝对路径并启用 sandbox。
- 风险点：不是普通单元测试，会触发真实账号登录/修改。

### `src/batch-oauth-worker.mjs`

- 由后端任务管理器启动的批量 OAuth worker，使用 `chromium.launch()` 并为每个账号创建独立 context。
- 使用共享浏览器运行时，生产模式要求固定浏览器绝对路径；不允许通过启动参数关闭 Chromium sandbox。
- 风险点：任务文件、结果与浏览器页面均涉及授权账号和 OAuth 状态，不得作为普通本地 smoke 执行。

### `src/verify-2fa.mjs`

- 离线读取账号文件并验证密钥格式和 TOTP 生成。
- 始终脱敏输出邮箱、当前验证码和密钥，不提供明文输出开关。

### `src/startup-check.mjs`

- 分别覆盖 `chromium.launch()` 与临时目录中的 `chromium.launchPersistentContext()`，仅加载本地页面。
- 校验 JavaScript、`zh-CN` locale、内存截图和资源清理；生产模式还精确校验浏览器版本为 `153.0.8010.52`。
- 不读取账号文件、不复用业务 profile、不访问 Google 页面；临时 profile 在退出时清理。

## 敏感路径

- `宝贝信息-*.txt`：账号、密码、恢复邮箱、旧 TOTP 密钥。
- `output/result.txt`：账号、密码、恢复邮箱、新 TOTP 密钥。
- `output/manual-review.jsonl`：账号、待确认的新 TOTP 密钥与中断原因。
- `output/progress.json`：账号处理状态。
- `output/logs/*.log`：运行阶段、邮箱、错误信息。
- `output/debug-*.png`：Google 页面截图。
- `browser-data/`：浏览器会话、Cookie、缓存。

## 非敏感优先验证

```powershell
node --check src/config.mjs
node --check src/browser-runtime.mjs
node --check src/account-parser.mjs
node --check src/redaction.mjs
node --check src/totp.mjs
node --check src/google-automator.mjs
node --check src/main.mjs
node --check src/test-login.mjs
node --check src/verify-2fa.mjs
node --check src/startup-check.mjs
node --check src/batch-oauth-worker.mjs
```

浏览器制品来源、版本依据和仍待完成的最终镜像动态验收见[浏览器运行时安全基线](../../../docs/browser-security-baseline-2026-09-20.md)。当前实现变更不等于生产浏览器已验收通过。
