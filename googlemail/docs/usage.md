# 使用指南

## 快速开始

`package.json` 的兼容下限仍为 Node.js 20.19.0；浏览器入口应使用 Playwright 1.63.0 支持的 Node.js 版本，生产部署固定使用 Node.js 24.x。Node.js 20 不作为本次生产浏览器验收环境。

### 1. 安装依赖

```powershell
npm ci
```

### 2. 准备账号文件

确保项目根目录下有 `宝贝信息-*.txt` 文件，格式为每行 4 个字段用 `----` 分隔：

```
email@gmail.com----password----recovery@email.com----BASE32SECRETKEY
```

### 3. 本地启动检查

```powershell
npm run test:startup
```

该命令只启动 Chromium 并加载本地页面，不读取账号文件或触发账号操作。

本地非生产环境未指定浏览器路径时会使用 Playwright 捆绑浏览器，仅用于开发检查。生产候选必须显式固定受控浏览器：

```bash
export FLASK_ENV=production
export GOOGLE_MANAGER_CHROME_EXECUTABLE_PATH=/opt/google-manager/chrome/chrome
unset CHROME_CHANNEL
npm run test:startup
```

生产启动检查分别覆盖普通 `launch()` 和临时持久化 `launchPersistentContext()`，仅访问本地页面，并验证浏览器精确版本、JavaScript、`zh-CN` locale、内存截图与退出清理。临时 profile 不会复用业务 `browser-data/`。正式入口不重复版本断言，所以该命令必须针对待发布的同一不可变镜像和同一浏览器路径执行。

### 4. 验证 2FA 密钥

运行前先验证所有密钥是否有效：

```powershell
node src/verify-2fa.mjs
```

输出示例：
```
══════════════════════════════════════════════════════════════════════
  Google 账号 2FA 密钥验证报告
══════════════════════════════════════════════════════════════════════
  总账号数: 20
  验证时间: 2026-06-01T12:00:00.000Z
══════════════════════════════════════════════════════════════════════

  [01] ✅ em***@gmail.com                          | TOTP: <TOTP_CODE> | 解码字节: 20
  [02] ✅ em***@gmail.com                          | TOTP: <TOTP_CODE> | 解码字节: 20
  ...

══════════════════════════════════════════════════════════════════════
  结果: ✅ 20 通过 | ❌ 0 失败
══════════════════════════════════════════════════════════════════════
```

### 5. 单账号测试 (推荐)

此步骤会真实登录并修改账号安全设置，只能在已授权账号范围内执行。`FLASK_ENV=production` 时须先设置经过校验的 `GOOGLE_MANAGER_CHROME_EXECUTABLE_PATH`，且不能设置 `CHROME_CHANNEL`。

先测试一个账号确认流程正常：

```powershell
# 仅测试登录和 2FA 修改
node src/test-login.mjs

# 指定测试账号
$env:TEST_EMAIL="your-account@gmail.com"
node src/test-login.mjs

# 同时测试恢复邮箱修改
$env:TEST_EMAIL="your-account@gmail.com"
$env:TEST_RECOVERY_EMAIL="target-recovery@gmail.com"
node src/test-login.mjs
```

测试脚本固定以非无头模式（`headless: false`、`slowMo: 300`）运行，自动执行完整登录+2FA修改流程后，浏览器窗口保持打开以便手动检查结果。按 Ctrl+C 退出。

### 6. 批量运行

该入口会登录并修改账号安全设置。执行前确认账号范围、输入文件与恢复邮箱配置，并先完成单账号验证。

生产运行必须使用与启动检查相同的固定浏览器绝对路径；本地默认捆绑浏览器或 `CHROME_CHANNEL` 不能替代生产制品审批。

```powershell
# 基础运行 (仅 2FA 修改)
node src/main.mjs

# 启用恢复邮箱修改 (每5个账号共享一个恢复邮箱)
$env:RECOVERY_EMAIL_POOL="recovery@gmail.com"
node src/main.mjs

# 多个恢复邮箱 (负载均衡)
$env:RECOVERY_EMAIL_POOL="recovery1@gmail.com,recovery2@gmail.com"
$env:ACCOUNTS_PER_RECOVERY="5"
node src/main.mjs
```

## 运行脚本说明

| 脚本 | 命令 | 用途 |
|------|------|------|
| 语法/单元测试 | `npm test` | 运行合成数据单元测试 |
| 覆盖率 | `npm run test:coverage` | 生成 V8 覆盖率报告到 `coverage/` |
| 启动检查 | `npm run test:startup` | 通过两类启动 API 验证本地页面、JS、locale、内存截图、版本门禁与清理 |
| 2FA 验证 | `node src/verify-2fa.mjs` | 验证所有账号的 2FA 密钥有效性（无对应 npm script） |
| 单账号测试 | `npm run test-login` | 自动化测试单个账号，浏览器保持打开供手动检查 |
| 批量处理 | `npm start` | 批量处理所有账号 |

## 断点续传

工具会自动记录处理进度到 `output/progress.json`。如果运行中断，重新运行时会自动跳过已处理的账号。

进度文件存在但 JSON 或结构损坏时，程序会停止启动并保留原文件，不会按空进度重新处理账号。

进度文件结构：
```json
{
  "completed": ["done1@gmail.com", "done2@gmail.com"],
  "failed": ["failed1@gmail.com"],
  "lastIndex": 2
}
```

重置进度前先关闭程序并备份 `output/progress.json`，再将其移出 `output/`。

## 输出文件

| 文件 | 说明 |
|------|------|
| `output/result.txt` | 成功处理的账号结果，格式为 `email----password----recoveryEmail----newSecret`（与输入格式一致，`oldSecret` 替换为 `newSecret`） |
| `output/manual-review.jsonl` | 旧 2FA 已关闭但新设置未得到页面确认时的复核记录 |
| `output/progress.json` | 进度记录，用于断点续传 |
| `output/logs/run-*.log` | 带时间戳的详细运行日志 |
| `output/debug-*.png` | 出错时自动生成的调试截图 |

`output/`、`browser-data/`、账号源文件和根目录日志均已加入忽略规则；其中结果、复核记录和截图仍属于本地敏感数据。

## 恢复邮箱功能

### 工作原理

1. 配置 `RECOVERY_EMAIL_POOL` 环境变量
2. 工具按 `ACCOUNTS_PER_RECOVERY` (默认5) 个账号一组，每组绑定到恢复邮箱池中的一个邮箱
3. 在 2FA 修改完成后，自动导航到恢复邮箱设置页面进行修改

### 分组示例

配置：`RECOVERY_EMAIL_POOL=a@x.com,b@x.com`, `ACCOUNTS_PER_RECOVERY=5`

| 账号索引 | 目标恢复邮箱 |
|----------|-------------|
| 0-4 | a@x.com |
| 5-9 | b@x.com |
| 10-14 | a@x.com |
| 15-19 | b@x.com |

### 恢复邮箱修改流程

1. 导航到 `https://myaccount.google.com/recovery/email`
2. 如需密码再验证，自动填入密码
3. 检测当前恢复邮箱是否已为目标邮箱 -> 已匹配则跳过
4. 查找编辑入口按钮 -> 修改文本框
5. 填入新邮箱 -> 保存
6. 如果 Google 要求向新邮箱发送验证码，等待 60 秒手动输入

### 恢复邮箱状态

| 状态 | 含义 |
|------|------|
| ✅ 已修改 | 恢复邮箱修改成功 |
| ⏭ 已设为目标 | 恢复邮箱已为目标邮箱，无需修改 |
| ⚠️ 待复核 | 恢复邮箱已提交但验证码或最终页面状态未确认 |
| ❌ 错误 | 恢复邮箱修改失败，详见错误信息 |

## 常见问题

### 账号登录失败 (signin/rejected)

可能原因：账号不存在或已被 Google 删除。建议联系卖家确认账号质量。

### TOTP 验证码被拒绝

工具每次生成当前时间窗口的验证码；失败后等待下一个窗口再试，最多尝试 3 次。如果全部失败，请核对系统时间和账号端当前密钥状态。

### 生产启动检查提示浏览器配置错误

依次检查 `FLASK_ENV`、`GOOGLE_MANAGER_CHROME_EXECUTABLE_PATH` 和 `CHROME_CHANNEL`：生产路径必须为绝对路径，且不能同时选择 channel；浏览器版本必须精确为当前批准的 `153.0.8010.52`。不要通过关闭 sandbox 绕过启动失败。

浏览器制品获取、摘要固定、容器 sandbox 和最终镜像验收要求见[浏览器运行时安全基线](../../docs/browser-security-baseline-2026-09-20.md)。当前代码与文档同步不代表最终生产镜像已经验收通过。
