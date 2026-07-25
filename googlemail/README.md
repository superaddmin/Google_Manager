# Googlemail

Node.js + Playwright 的 Google 账号安全设置自动化项目。开发与测试要求 Node.js 20.19.0 或更高版本。

## 本地验证

```powershell
npm ci
npm test
npm run test:coverage
npm run test:startup
```

`test:startup` 只启动 Chromium 并加载本地 `data:` 页面，不读取账号文件、不使用持久化浏览器数据，也不访问 Google 页面。

`npm start` 与 `npm run test-login` 会登录并修改账号安全设置，执行前应确认账号范围、输入文件和恢复邮箱配置，并先完成单账号验证。

## GoogleManager 主页调用

父项目通过 `app/services/googlemail_service.py` 启动 `src/main.mjs`。每个主页任务使用独立的 `runtime/tasks/<task-id>/` 输入和输出目录；该目录、浏览器数据、结果和日志均由 Git 忽略。`OUTPUT_DIR` 可由父进程设置，调试截图会写入对应任务输出目录。

## 文档

- [架构说明](docs/README.md)
- [使用指南](docs/usage.md)
- [配置参考](docs/configuration.md)
- [2026-07-25 项目审计报告](docs/audit-2026-07-25.md)

账号源文件、`output/`、`browser-data/`、`.env*` 与日志均属于本地敏感数据，并已纳入忽略规则。`output/result.txt` 和 `output/manual-review.jsonl` 含账号状态与 TOTP 密钥，不应进入版本控制或共享日志。
