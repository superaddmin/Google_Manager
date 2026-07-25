# Googlemail 项目上下文图谱

## 摘要

Googlemail 是一个 Node.js ESM + Playwright 自动化项目，用于在授权范围内批量处理 Google 账号安全设置：读取账号文件，使用旧 TOTP 登录，通过浏览器自动化关闭并重新设置 2FA，可选修改恢复邮箱，并记录进度、结果、日志和调试截图。

## 技术栈

| 领域 | 技术 |
| --- | --- |
| 运行时 | Node.js 20.19+ |
| 模块系统 | ESM (`type: module`, `.mjs`) |
| 浏览器自动化 | Playwright Chromium persistent context |
| TOTP | `otplib` 13.4.1 |
| 密钥生成 | `generateBase32Secret()` + Node.js `crypto.randomBytes(20)` |
| 配置 | `src/config.mjs` + 环境变量 |
| 文档 | Markdown 中文文档 |

## 核心依赖

- `playwright`：浏览器自动化、Chromium 控制、截图、持久化上下文。
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
- `PROXY`、`CHROME_CHANNEL` 仅在 main 中生效。
- 风险点：`output/result.txt` 包含密码与新 TOTP 密钥。

### `src/test-login.mjs`

- 单账号调试入口，固定 headed、slowMo 300，执行真实流程后保持浏览器打开。
- 风险点：不是普通单元测试，会触发真实账号登录/修改。

### `src/verify-2fa.mjs`

- 离线读取账号文件并验证密钥格式和 TOTP 生成。
- 始终脱敏输出邮箱、当前验证码和密钥，不提供明文输出开关。

### `src/startup-check.mjs`

- 启动无头 Chromium 并加载本地 `data:` 页面。
- 不读取账号文件、不使用持久化 profile、不访问 Google 页面。

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
node --check src/account-parser.mjs
node --check src/redaction.mjs
node --check src/totp.mjs
node --check src/google-automator.mjs
node --check src/main.mjs
node --check src/test-login.mjs
node --check src/verify-2fa.mjs
node --check src/startup-check.mjs
```
