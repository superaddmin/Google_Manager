# AGENTS.md（Googlemail 项目级）

> 适用范围：`F:\Google_Manager\googlemail`。本文件优先于全局 `AGENTS.md`，所有说明默认中文。当前项目是 Node.js + Playwright 的 Google 账号安全设置自动化工具，涉及账号、密码、恢复邮箱、TOTP 密钥与浏览器会话数据，必须按敏感项目处理。

---

## 1. 项目定位

- 运行时：Node.js 20.19+，ESM (`.mjs`)。
- 自动化：Playwright Chromium 持久化上下文。
- 2FA：`otplib` 13 同步 API，按 Base32 路径生成/验证 TOTP。
- 输入：根目录 `宝贝信息-*.txt`，每行 `email----password----recoveryEmail----oldSecret`。
- 输出：`output/progress.json`、`output/result.txt`、`output/manual-review.jsonl`、`output/logs/run-*.log`、`output/debug-*.png`。
- 浏览器状态：`browser-data/`。

## 2. 目录职责

```text
src/config.mjs             配置常量与环境变量解析
src/account-parser.mjs    账号文件解析、进度读写、结果追加
src/redaction.mjs         日志邮箱、验证码、密钥与已知值脱敏
src/totp.mjs              TOTP 生成、Base32 长度检查与容差校验
src/google-automator.mjs  Google 登录、2FA 重设、恢复邮箱修改核心流程
src/main.mjs              批量处理入口与持久化浏览器上下文
src/test-login.mjs        单账号可视化调试入口
src/verify-2fa.mjs        离线批量校验 2FA 密钥格式与可用性
src/startup-check.mjs     不读取账号的本地 Chromium 启动检查
docs/                     架构、配置、使用与手动参考文档
output/                   运行结果与调试产物（敏感，默认不提交）
browser-data/             Playwright 用户数据（敏感，默认不提交）
.codex/                   项目级 Codex 代理、技能、规则与上下文
```

## 3. 敏感数据边界（必须）

- 禁止在回复、日志、文档、提交信息中暴露完整邮箱、密码、TOTP 当前验证码、TOTP 密钥、恢复邮箱池、Cookie、浏览器 profile 内容。
- 读取 `宝贝信息-*.txt`、`output/result.txt`、`output/manual-review.jsonl`、`output/progress.json`、`output/logs/*`、`browser-data/` 前必须确认任务确实需要；展示时必须脱敏。
- 不要把 `browser-data/`、`output/`、账号源文件、`.env` 或调试截图加入版本控制。
- 不要新增向外部服务上传账号、密钥、截图、日志的逻辑。
- 不要扩大“反检测/规避平台风控”能力；仅维护现有可测试流程、稳定性与错误处理。
- 任何会真实登录账号、修改 2FA、修改恢复邮箱或批量处理账号的运行命令，都必须由用户明确要求并说明授权边界后再执行。

## 4. 开发与验证命令

优先使用 PowerShell 语法：

```powershell
# 安装依赖
npm install

# 静态语法检查（不会执行登录流程）
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

敏感/会读取账号文件或触发浏览器流程的命令：

```powershell
# 离线读取账号文件并验证 TOTP；输出始终脱敏
node src/verify-2fa.mjs

# 会打开真实浏览器并尝试登录/改 2FA，必须用户明确授权
npm run test-login
npm start
node src/main.mjs
```

## 5. 修改准则

- 小步变更，不做无关重构；`google-automator.mjs` 已较大，新增复杂逻辑优先拆成小函数，但不要一次性重写流程。
- 优先保持现有 ESM 风格、命名风格与中文运行日志风格。
- 修改账号解析、TOTP、进度/结果写入时，必须补充最小离线验证或可复现步骤。
- TOTP 生成必须保持 Base32 解码路径，禁止把 Base32 密钥按 UTF-8 普通字符串生成验证码。
- 修改 Playwright 选择器时，先截图/日志定位首个失败点，不要盲目堆叠选择器。
- 修改文档时使用 UTF-8 no BOM，保持原有换行风格；中文内容写回后做回读与 `Format-Hex` 校验。

## 6. 推荐代理/技能

- 使用项目技能：`.codex/skills/googlemail-automation-guard`。
- 复杂设计：`googlemail-automation-architect`。
- Playwright 流程排障：`googlemail-playwright-flow-debugger`。
- TOTP/凭据安全：`googlemail-totp-secret-reviewer`。
- Node ESM 静态检查：`googlemail-node-esm-verifier`。
- 文档同步：`googlemail-doc-runbook-updater`。

## 7. 成功标准

- 目录架构与核心数据流已被读取并遵守。
- 不泄露敏感账号数据。
- 能通过最小静态检查：`node --check src/*.mjs`。
- 涉及真实账号操作时，交付说明必须包含授权假设、命令、风险与回滚/中断方式。
