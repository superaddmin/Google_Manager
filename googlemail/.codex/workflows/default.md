# Googlemail 默认工作流

## 1. 任务分类

- 文档/配置：读取 `AGENTS.md`、`.codex/context/project-map.md`、`docs/*`。
- Node/ESM 静态问题：运行 `node --check src/*.mjs`。
- 本地启动检查：运行 `npm run test:startup`，只加载本地 `data:` 页面。
- TOTP/账号解析：优先构造脱敏样例或静态审查；读取真实账号文件前确认必要性。
- Playwright 页面问题：先看日志与截图元信息；真实浏览器操作必须用户授权。
- 批量处理：默认不执行；只有用户明确要求并确认授权账号范围时才运行。

## 2. 实施顺序

1. 读取最近项目规则与 `.codex/context/project-map.md`。
2. 明确是否触及敏感数据或真实账号状态。
3. 选择最小验证方式：静态检查 > 脱敏样例 > 单账号授权调试 > 批量运行。
4. 修改最少文件，避免重写 `google-automator.mjs` 大段流程。
5. 回读校验编码、运行相关静态命令、记录结果。

## 3. 禁止默认执行的命令

```powershell
npm start
npm run test-login
node src/main.mjs
node src/test-login.mjs
node src/verify-2fa.mjs
```

这些命令分别可能触发真实账号登录/修改，或读取账号/TOTP 敏感信息；即使脚本默认脱敏，也不要把明文账号文件、结果行、验证码或密钥复制到交付说明中。

## 4. 推荐验证矩阵

| 改动类型 | 最小验证 |
| --- | --- |
| 文档/AGENTS/.codex | `Get-Content -Encoding UTF8 -TotalCount 20 <file>` + `Format-Hex -Count 16` |
| ESM 语法 | `node --check <changed.mjs>` |
| Chromium 启动 | `npm run test:startup`（不读取账号） |
| 账号解析 | 用脱敏临时样例或代码审查，不直接打印真实账号文件 |
| TOTP | 用占位 Base32 测试值，不输出真实密钥 |
| Playwright 选择器 | 静态审查 + 用户授权后 headed 单账号调试 |
| 批量流程 | 用户授权后 `HEADLESS=false` 小批量/单账号先行 |

## 5. TOTP/账号状态排障补充

- 本项目 TOTP 必须使用 `otplib` v13 的默认 Base32 解码路径，禁止把 Base32 密钥转为 UTF-8 普通字符串字节。
- 固定时间测试使用秒级 `epoch`；验证容差使用 `epochTolerance`，当前配置为 60 秒。
- `node src/verify-2fa.mjs` 始终脱敏输出，普通日志与交付说明不得展示源文件内容。
- 单账号真实调试若停在 Google Authenticator 页面并提示验证码错误，先区分：
  - 本地算法问题：离线兼容断言失败、系统时间明显偏移、`node src/verify-2fa.mjs` 失败。
  - 账号端状态问题：离线校验通过但 Google 页面连续拒绝当前窗口验证码，通常说明账号文件中的旧密钥与 Google 端当前 2FA 状态不一致，或账号已被上次流程部分修改。
- 不要在此类失败后直接批量运行 `npm start`，避免把更多账号写入失败进度或触发更多真实账号状态变更。
