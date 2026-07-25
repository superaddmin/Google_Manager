# Google 2FA 自动修改工具 - 架构文档

## 项目概述

基于 Node.js + Playwright 的浏览器自动化工具，用于批量执行 Google 账号的**两步验证（2FA）关闭与重新设置**，并支持**批量修改恢复邮箱**。

## 技术栈

| 组件      | 技术                    | 版本      |
| ------- | --------------------- | ------- |
| 运行时     | Node.js (ESM)         | >=20.19.0 |
| 浏览器自动化  | Playwright (Chromium) | 1.60.0 |
| TOTP 生成 | otplib                | 13.4.1 |
| 单元测试 | Vitest + V8 Coverage | 4.1.10 |
| 模块类型    | ESM (import/export)   | -       |

## 项目结构

```
F:\Google_Manager\googlemail\
├── src/
│   ├── config.mjs           # 集中配置管理
│   ├── account-parser.mjs   # 账号文件解析 & 进度管理
│   ├── redaction.mjs        # 日志敏感信息脱敏
│   ├── totp.mjs             # TOTP 验证码生成与验证
│   ├── google-automator.mjs # 核心自动化引擎
│   ├── main.mjs             # 批量处理入口
│   ├── test-login.mjs       # 单账号调试入口
│   ├── verify-2fa.mjs       # 2FA 密钥批量验证
│   └── startup-check.mjs    # 本地 Chromium 启动烟雾检查
├── tests/                   # 单元测试目录
│   ├── totp.test.mjs        # TOTP 模块测试
│   └── account-parser.test.mjs  # 账号解析模块测试
├── docs/
│   ├── README.md            # 本文件 - 架构文档
│   ├── configuration.md     # 配置参考
│   ├── usage.md             # 使用指南
│   └── google-account-login-guide.md  # 手动操作参考 (来自飞书)
├── output/                  # 输出目录 (运行时自动生成)
│   ├── logs/                # 运行日志 (run-YYYY-MM-DDTHH-MM-SS.log)
│   ├── progress.json        # 进度文件 (断点续传，首运行自动创建)
│   ├── result.txt           # 成功结果输出 (运行时自动创建)
│   ├── manual-review.jsonl  # 2FA 中断后的人工复核记录
│   └── debug-*.png          # 调试截图 (出错时自动生成，保留最近10张)
├── browser-data/            # 浏览器持久化数据 (Playwright)
├── package.json
└── 宝贝信息-*.txt           # 账号信息文件
```

## 模块依赖关系

```
main.mjs (批量入口)
├── config.mjs ───────────── 配置常量
├── account-parser.mjs ───── 解析账号文件、管理进度
├── google-automator.mjs ─── 核心自动化
│   ├── totp.mjs ─────────── TOTP 生成/验证
│   └── crypto (Node) ────── 生成新密钥
├── playwright (chromium) ─── 浏览器控制
├── path ─────────────────── 路径处理
└── fs ───────────────────── 文件系统

test-login.mjs (单账号测试)
├── config.mjs
├── account-parser.mjs
├── google-automator.mjs
├── playwright (chromium)
└── fs

verify-2fa.mjs (密钥验证)
├── config.mjs
├── account-parser.mjs
└── totp.mjs
```

## 核心数据流

```
┌─────────────────┐
│ 宝贝信息-*.txt   │  (email----password----recoveryEmail----oldSecret)
└────────┬────────┘
         │ parseAccounts()
         ▼
┌─────────────────┐
│ accounts[]      │
└────────┬────────┘
         │ 逐账号处理
         ▼
┌─────────────────────────────────────────────┐
│        loginAndChange2FA()                   │
│                                             │
│  1. 反检测脚本注入 (navigator.webdriver等)    │
│  2. 导航到 Google 登录页                     │
│  3. 填写邮箱 → 下一步                        │
│  4. 填写密码 → 下一步                        │
│  5. 处理 2FA 挑战 (单窗口重试策略)            │
│     ├─ waitForSafeTOTPWindow() 等待安全窗口   │
│     ├─ generateTOTP() 生成当前验证码         │
│     └─ 失败后等待下一个窗口重试（最多3次）     │
│  6. 导航到安全设置                           │
│  7. 关闭旧 2FA → 确认关闭                    │
│  8. 开启新 2FA, 填入新密钥 → 验证 → 完成     │
│  9. [可选] changeRecoveryEmail()             │
│     ├─ 导航到恢复邮箱页面                     │
│     ├─ 处理密码再验证                         │
│     ├─ 检测当前邮箱 → 跳过/编辑               │
│     ├─ 填入新邮箱 → 保存                     │
│     └─ 处理验证码挑战                         │
└────────┬────────────────────────────────────┘
         │
         ▼
┌─────────────────┐     ┌──────────────────┐
│ output/result   │     │ output/progress  │
│ .txt (成功结果)  │     │ .json (断点续传)  │
└─────────────────┘     └──────────────────┘
```

## 模块详细说明

### config.mjs - 配置模块

集中管理所有配置常量，支持环境变量覆盖。

**导出常量：**

| 常量                            | 类型                      | 默认值                           | 环境变量                    | 说明            |
| ----------------------------- | ----------------------- | ----------------------------- | ----------------------- | ------------- |
| `ACCOUNTS_FILE`               | string                  | `宝贝信息-114260514183055495.txt` | `ACCOUNTS_FILE`         | 账号信息文件路径      |
| `OUTPUT_DIR`                  | string                  | `output/`                     | -                       | 运行输出目录          |
| `PROGRESS_FILE`               | string                  | `output/progress.json`        | -                       | 进度文件路径        |
| `RESULT_FILE`                 | string                  | `output/result.txt`           | -                       | 结果输出路径        |
| `MANUAL_REVIEW_FILE`          | string                  | `output/manual-review.jsonl`  | -                       | 关键流程中断后的人工复核记录 |
| `HEADLESS`                    | boolean                 | `true`                        | `HEADLESS`              | 是否无头模式        |
| `SLOW_MO`                     | number                  | `200`                         | `SLOW_MO`               | 操作间延迟(ms)     |
| `ACCOUNT_DELAY`               | number                  | `5000`                        | `ACCOUNT_DELAY`         | 账号间延迟(ms)     |
| `USER_DATA_DIR`               | string                  | `browser-data/`               | -                       | 浏览器数据目录       |
| `RECOVERY_EMAIL_POOL`         | string[] (env: 逗号分隔字符串) | `[]`                          | `RECOVERY_EMAIL_POOL`   | 恢复邮箱池 (逗号分隔)  |
| `ACCOUNTS_PER_RECOVERY_EMAIL` | number                  | `5`                           | `ACCOUNTS_PER_RECOVERY` | 每N个账号共享一个恢复邮箱 |

`ACCOUNTS_FILE` 可由同名环境变量覆盖。数值配置使用严格整数校验：延迟不得为负数，`ACCOUNTS_PER_RECOVERY` 必须大于或等于 1。

### account-parser.mjs - 账号解析模块

**导出函数：**

| 函数              | 签名                                               | 说明                                                            |
| --------------- | ------------------------------------------------ | ------------------------------------------------------------- |
| `parseAccounts` | `(filePath: string) => Account[]`                | 解析账号文件，每行格式 `email----password----recoveryEmail----oldSecret` |
| `loadProgress`  | `(filePath: string) => Progress`                 | 文件缺失时返回空进度；JSON 或结构损坏时抛错停止启动    |
| `saveProgress`  | `(filePath: string, progress: Progress) => Promise<void>` | 通过同目录临时文件原子替换进度 JSON                                         |
| `appendResult`  | `(filePath: string, line: string) => Promise<void>`       | **异步**追加结果行到文件                                                      |

**数据结构：**

```typescript
interface Account {
  email: string;
  password: string;
  recoveryEmail: string;  // 原始恢复邮箱
  oldSecret: string;       // 旧 2FA 密钥 (Base32)
}

interface Progress {
  completed: string[];     // 已完成的邮箱列表
  failed: string[];        // 失败的邮箱列表
  lastIndex: number;       // 上次处理到的索引
}
```

### totp.mjs - TOTP 模块

基于 `otplib` 的 TOTP 工具模块。内部配置：

```javascript
const TOTP_OPTIONS = {
  period: 30,     // 30秒周期
  digits: 6,      // 6位数字验证码
  algorithm: 'sha1',
};
```

使用 v13 主包的 `generateSync`/`verifySync`，字符串密钥走默认 Base32 解码；验证容差为 `epochTolerance: 60`，密钥解码后至少 16 字节。

**导出函数：**

| 函数                   | 签名                                  | 说明                                    |
| -------------------- | ----------------------------------- | ------------------------------------- |
| `generateTOTP`       | `(secret: string, epoch?: number) => string \| null` | 生成当前或指定秒级时间点的 TOTP 验证码                    |
| `generateBase32Secret` | `(input?: number \| Buffer) => string` | 生成指定字节数的随机 Base32 密钥（默认 20 字节）       |
| `getBase32DecodedByteLength` | `(secret: string) => number` | 返回 Base32 密钥解码后的字节数 |
| `getTOTPTimeRemaining` | `() => number`                       | 获取当前 TOTP 时间窗口剩余秒数                      |
| `verifyTOTP`         | `(token: string, secret: string, epoch?: number) => boolean` | 验证 TOTP 令牌（容忍 ±60 秒）         |

### google-automator.mjs - 核心自动化引擎

**内部函数：**

| 函数                                                         | 说明                                      |
| ---------------------------------------------------------- | --------------------------------------- |
| `generateNewSecret()`                                      | 生成 20 字节随机 Base32 密钥（使用 `generateBase32Secret`） |
| `waitForSafeTOTPWindow(page, logger)`                      | 等待安全的 TOTP 时间窗口（剩余时间 ≤8s 时等待）            |
| `handlePasswordReverify(page, password, logger)`           | 处理 Google 修改恢复邮箱时的密码再验证页面               |
| `changeRecoveryEmail(page, targetEmail, password, logger)` | 修改恢复邮箱的完整流程                             |

**导出函数：**

| 函数                  | 签名                                                        | 说明                   |
| ------------------- | --------------------------------------------------------- | -------------------- |
| `loginAndChange2FA` | `(page, account, logger, targetRecoveryEmail?) => Result` | 完整的登录+2FA修改+恢复邮箱修改流程 |

**loginAndChange2FA 返回结构：**

```typescript
interface Result {
  success: boolean;
  newSecret?: string;       // 新生成的 2FA 密钥
  requiresManualReview?: boolean; // 旧 2FA 已关闭但新流程未确认
  recoveryResult?: {        // 恢复邮箱修改结果
    success: boolean;
    skipped?: boolean;      // 已为目标邮箱，跳过
    warning?: string;       // 警告信息
    error?: string;         // 错误信息
  };
  error?: string;           // 失败原因
}
```

**changeRecoveryEmail 内部流程：**

1. 导航到 `https://myaccount.google.com/recovery/email`
2. 处理密码再验证
3. 检测当前恢复邮箱是否已为目标邮箱 → 跳过
4. 查找编辑/添加入口按钮
5. 填入新恢复邮箱
6. 点击保存
7. 如果 Google 要求验证新邮箱，等待手动输入验证码 (60秒超时)
8. 返回结果

### main.mjs - 批量处理入口

**启动命令：** `npm start` 或 `node src/main.mjs`

**执行流程：**

1. 解析账号文件；没有有效账号时停止，不启动浏览器
2. 创建带时间戳的日志文件并加载进度
3. 显示运行配置摘要
4. 启动持久化浏览器上下文 (Chromium)，支持 `PROXY` 和 `CHROME_CHANNEL` 环境变量
5. 逐账号处理：
   - 跳过已完成和失败的账号
   - 计算目标恢复邮箱 (基于 `RECOVERY_EMAIL_POOL` 和 `ACCOUNTS_PER_RECOVERY_EMAIL`)
   - 调用 `loginAndChange2FA()`
   - 成功结果先落盘，再更新进度
   - 关键流程中断时写入 `manual-review.jsonl`
   - 清理浏览器 cookies，准备下一个账号
6. 输出统计摘要
7. 关闭浏览器上下文，清理旧截图

**恢复邮箱分组算法：**

```javascript
// 第 i 个账号的目标恢复邮箱
const poolIndex = Math.floor(i / ACCOUNTS_PER_RECOVERY_EMAIL) % RECOVERY_EMAIL_POOL.length;
targetRecoveryEmail = RECOVERY_EMAIL_POOL[poolIndex];
```

例如：`ACCOUNTS_PER_RECOVERY=5`, `RECOVERY_EMAIL_POOL=[a@x.com, b@x.com]`

- 账号 0-4 → a@x.com
- 账号 5-9 → b@x.com
- 账号 10-14 → a@x.com (循环)

### test-login.mjs - 单账号调试

**启动命令：** `npm run test-login` 或 `node src/test-login.mjs`

**环境变量：**

- `TEST_EMAIL` - 指定测试账号邮箱（默认使用账号文件中第一个）
- `TEST_RECOVERY_EMAIL` - 指定目标恢复邮箱（默认使用 `RECOVERY_EMAIL_POOL` 第一个）

固定以非无头模式（`headless: false`）、`slowMo: 300` 运行，自动执行完整登录+2FA修改流程后，浏览器窗口保持打开以便手动检查结果。按 Ctrl+C 退出。

**注意：** 此脚本不支持 `PROXY`、`CHROME_CHANNEL`、`HEADLESS`、`SLOW_MO` 等环境变量。

### verify-2fa.mjs - 2FA 密钥验证

**启动命令：** `node src/verify-2fa.mjs`

批量验证所有账号的 2FA 密钥有效性：

- 检查 Base32 格式
- 检查 Base32 格式及解码后长度（至少 16 字节）
- 生成并验证 TOTP（`epochTolerance: 60`，容忍 ±60 秒时间偏差）

**注意：** 此脚本没有对应的 `npm run` 命令，需直接用 `node` 运行。脚本为纯离线验证，并始终使用脱敏输出。

### startup-check.mjs - 本地启动检查

**启动命令：** `npm run test:startup`

启动无头 Chromium 并加载本地 `data:` 页面，不读取账号文件、不使用 `browser-data/`，也不访问 Google 页面。

## 反检测机制

Playwright 浏览器启动时注入以下脚本避免被 Google 检测为自动化工具：

```javascript
Object.defineProperty(navigator, 'webdriver', { get: () => false });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
// 修改 permissions.query 通知权限
```

## 资源管理

### 浏览器资源清理

- `main.mjs` 使用 `try/finally` 确保浏览器上下文在任何情况下都能正确关闭
- 每个账号处理完毕后调用 `context.clearCookies()` 清理状态，防止跨账号污染
- `test-login.mjs` 监听 `SIGINT` 和 `SIGTERM` 信号，确保按 Ctrl+C 时正确关闭浏览器

### 截图管理

- 调试截图自动保留最近 10 张，旧截图在程序退出时自动清理
- 截图文件位于 `output/` 目录，命名格式 `debug-*.png`

## 账号文件格式

每行一个账号，字段用 `----` 分隔：

```
email@gmail.com----password----recovery@email.com----BASE32SECRETKEY
```

4 个字段依次为：邮箱、密码、原始恢复邮箱、旧 2FA 密钥 (Base32)。
