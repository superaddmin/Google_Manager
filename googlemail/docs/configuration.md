# 配置参考

## 环境变量

所有配置通过环境变量控制，在运行命令前设置即可。

> **注意：** 不同脚本适用的环境变量不同，详见下表"适用范围"列。

### 运行模式（适用于 `main.mjs`）

| 变量 | 类型 | 默认值 | 适用范围 | 说明 |
|------|------|--------|----------|------|
| `ACCOUNTS_FILE` | string | 项目默认账号文件 | main/test-login/verify-2fa | 账号文件路径，可为相对或绝对路径 |
| `OUTPUT_DIR` | string | `output` | main.mjs | 结果、进度、日志与调试截图目录 |
| `USER_DATA_DIR` | string | `browser-data` | main.mjs | Playwright 持久化浏览器数据目录 |
| `HEADLESS` | boolean | `true` | main.mjs | 设为 `false` 显示浏览器窗口 |
| `SLOW_MO` | number | `200` | main.mjs | 操作间延迟(毫秒)，模拟人类操作速度 |
| `ACCOUNT_DELAY` | number | `5000` | main.mjs | 账号间等待时间(毫秒)，避免触发频率限制 |
| `CHROME_CHANNEL` | string | - | main.mjs | 指定 Chrome 版本，如 `chrome`、`chromium` |
| `PROXY` | string | - | main.mjs | 代理服务器地址，如 `http://127.0.0.1:7890` |

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

- `test-login.mjs` 固定以**非无头模式**（`headless: false`）、`slowMo: 300` 运行，不接受 `HEADLESS`、`SLOW_MO`、`CHROME_CHANNEL`、`PROXY` 等环境变量。
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
