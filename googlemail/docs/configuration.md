# 配置参考

## 环境变量

所有配置通过环境变量控制，在运行命令前设置即可。

> **注意：** 不同脚本适用的环境变量不同，详见下表"适用范围"列。

### 浏览器运行时配置

| 变量 | 类型 | 默认值 | 适用范围 | 说明 |
|------|------|--------|----------|------|
| `FLASK_ENV` | string | - | 全部浏览器入口 | 忽略大小写等于 `production` 时启用生产浏览器门禁 |
| `GOOGLE_MANAGER_CHROME_EXECUTABLE_PATH` | absolute path | - | main/test-login/batch-oauth-worker/startup-check | 指向受控 Chrome 可执行文件；生产环境必填 |
| `CHROME_CHANNEL` | string | - | 全部浏览器入口的非生产开发 | 可选本地 Chrome channel；不得与固定路径同时使用 |

生产环境要求：

- `GOOGLE_MANAGER_CHROME_EXECUTABLE_PATH` 必须是绝对路径，且应指向批准的 Chrome for Testing 153.0.8010.52。
- 缺少路径、路径为相对路径，或同时设置 `CHROME_CHANNEL` 时，浏览器启动前即失败。
- 所有入口强制 `chromiumSandbox: true`；调用方不能覆盖路径、channel、sandbox，也不能传入 `--no-sandbox` 或 `--disable-setuid-sandbox`。
- `npm run test:startup` 会在生产模式精确核对浏览器版本；其他正式入口不重复版本断言，因此必须在同一不可变镜像和同一路径上把启动检查作为发布门禁。最终镜像还须完成 sandbox、架构、共享库和完整流程验收，不能仅凭该检查宣布可上线。

非生产环境未配置固定路径时可使用 Playwright 捆绑浏览器，供本地开发使用；它不满足生产安全基线。

### 运行模式（适用于 `main.mjs`）

| 变量 | 类型 | 默认值 | 适用范围 | 说明 |
|------|------|--------|----------|------|
| `ACCOUNTS_FILE` | string | 项目默认账号文件 | main/test-login/verify-2fa | 账号文件路径，可为相对或绝对路径 |
| `OUTPUT_DIR` | string | `output` | main.mjs | 结果、进度、日志与调试截图目录 |
| `USER_DATA_DIR` | string | `browser-data` | main.mjs | Playwright 持久化浏览器数据目录 |
| `HEADLESS` | boolean | `true` | main.mjs | 设为 `false` 显示浏览器窗口 |
| `SLOW_MO` | number | `200` | main.mjs | 调试用操作间延迟（毫秒） |
| `ACCOUNT_DELAY` | number | `5000` | main.mjs | 账号之间的等待间隔（毫秒） |
| `PROXY` | string | - | main.mjs/batch-oauth-worker.mjs | 代理服务器地址，如 `http://127.0.0.1:7890` |

### 恢复邮箱配置（适用于 `main.mjs`）

| 变量 | 类型 | 默认值 | 适用范围 | 说明 |
|------|------|--------|----------|------|
| `RECOVERY_EMAIL_POOL` | string | `""` | main.mjs | 恢复邮箱池，逗号分隔，如 `a@x.com,b@x.com` |
| `ACCOUNTS_PER_RECOVERY` | number | `5` | main.mjs | 每N个账号共享一个恢复邮箱 |

### 测试配置（适用于 `test-login.mjs`）

| 变量 | 类型 | 默认值 | 适用范围 | 说明 |
|------|------|--------|----------|------|
| `TEST_EMAIL` | string | 第一个账号 | test-login.mjs | 指定测试账号邮箱 |
| `TEST_RECOVERY_EMAIL` | string | 恢复邮箱池第一个 | test-login.mjs | 指定测试用的目标恢复邮箱 |

### 注意

- `test-login.mjs` 固定以**非无头模式**（`headless: false`）、`slowMo: 300` 运行，不接受 `HEADLESS`、`SLOW_MO`、`PROXY` 等行为覆盖；浏览器选择仍遵循共享运行时规则。
- `verify-2fa.mjs` 是纯离线验证脚本，会使用 `ACCOUNTS_FILE`；默认输出脱敏。
- `SLOW_MO`、`ACCOUNT_DELAY` 必须是非负整数，`ACCOUNTS_PER_RECOVERY` 必须是大于或等于 1 的整数；非法值会在启动时直接报错。
- `HEADLESS` 接受 `true`、`false`、`1`、`0`（不区分大小写）；其他值会报错。
- `RECOVERY_EMAIL_POOL` 中每一项都必须是有效邮箱格式，错误信息不会回显原值。

## 配置示例

### 基础批量运行

```powershell
# Windows PowerShell
$env:HEADLESS="false"
$env:SLOW_MO="300"
node src/main.mjs
```

### 生产浏览器启动检查

以下示例只加载本地页面，不读取账号文件。路径必须替换为服务器上经过制品校验的实际绝对路径：

```bash
export FLASK_ENV=production
export GOOGLE_MANAGER_CHROME_EXECUTABLE_PATH=/opt/google-manager/chrome/chrome
unset CHROME_CHANNEL
npm run test:startup
```

`CHROME_CHANNEL` 仅用于非生产本地调试，不能作为生产路径的替代方案。

### 启用恢复邮箱修改

```powershell
$env:RECOVERY_EMAIL_POOL="myrecovery@gmail.com,backup@gmail.com"
$env:ACCOUNTS_PER_RECOVERY="5"
node src/main.mjs
```

### 使用代理

```powershell
$env:PROXY="http://127.0.0.1:7890"
$env:HEADLESS="false"
node src/main.mjs
```

### 单账号测试

```powershell
$env:TEST_EMAIL="target@gmail.com"
$env:TEST_RECOVERY_EMAIL="myrecovery@gmail.com"
node src/test-login.mjs
```

### 2FA 密钥验证

```powershell
node src/verify-2fa.mjs
```

## 配置文件常量

配置文件位于 `src/config.mjs`，包含以下导出常量：

| 常量 | 值 | 对应环境变量 | 说明 |
|------|------|-------------|------|
| `ACCOUNTS_FILE` | `宝贝信息-114260514183055495.txt` | `ACCOUNTS_FILE` | 账号信息文件路径 |
| `OUTPUT_DIR` | `output/` | `OUTPUT_DIR` | 运行输出目录 |
| `PROGRESS_FILE` | `output/progress.json` | - | 进度文件路径 |
| `RESULT_FILE` | `output/result.txt` | - | 结果输出路径 |
| `MANUAL_REVIEW_FILE` | `output/manual-review.jsonl` | - | 关键流程中断后的人工复核记录 |
| `USER_DATA_DIR` | `browser-data/` | `USER_DATA_DIR` | 浏览器数据持久化目录 |
| `HEADLESS` | `true` | `HEADLESS` | 无头模式开关 |
| `SLOW_MO` | `200` | `SLOW_MO` | 操作间延迟(ms) |
| `ACCOUNT_DELAY` | `5000` | `ACCOUNT_DELAY` | 账号间延迟(ms) |
| `RECOVERY_EMAIL_POOL` | `[]` | `RECOVERY_EMAIL_POOL` | 恢复邮箱池（已解析为数组） |
| `ACCOUNTS_PER_RECOVERY_EMAIL` | `5` | `ACCOUNTS_PER_RECOVERY` | 每N个账号共享一个恢复邮箱 |

浏览器路径、版本和 sandbox 不属于 `config.mjs` 的业务常量，由 `src/browser-runtime.mjs` 统一解析和校验。完整安全依据见[浏览器运行时安全基线](../../docs/browser-security-baseline-2026-09-20.md)。
