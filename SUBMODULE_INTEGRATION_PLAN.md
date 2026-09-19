# Googlemail 本地复制集成执行计划

> **主项目**：`F:\Google_Manager`（Flask + React）
> **本地源目录**：`F:\Googlemail`（Node.js + Playwright）
> **集成目录**：`F:\Google_Manager\googlemail`
> **集成方式**：受控本地复制（vendored source），不是 Git submodule
> **修订日期**：2026-09-16（完成全量项目审核、依赖补齐与集中防盗中控集成）

---

## 1. 决策与范围

### 1.1 集成决策

- `F:\Googlemail` 是本机已有源码快照，不为其创建远端仓库。
- Googlemail 以普通目录复制到主项目的 `googlemail/`，由主项目 Git 直接跟踪。
- 不创建 `.gitmodules`，不执行 `git submodule add/init/update`。
- 不把 `googlemail/` 整体加入父仓库 `.gitignore`。
- 每次同步都从 `F:\Googlemail` 受控复制，并审查主项目中的 Git 差异。

### 1.2 本阶段范围

本阶段完成源码级集成：

1. 复制 Googlemail 的源码、测试、文档、锁文件和项目级规则。
2. 排除账号源文件、浏览器会话、运行输出、日志、覆盖率和依赖目录。
3. 让复制后的 Googlemail 在主项目内独立安装、测试和启动检查。
4. 提供父仓库级本地复制集成测试。

首阶段仅完成源码复制与独立 CLI 验证。追加阶段已经实现 Flask 子进程适配器、主页操作入口和任务状态协议，同时保留 CLI 作为独立运维入口。

### 1.3 成功标准

- `googlemail/` 是普通 Git 目录，不是 gitlink。
- 敏感路径和本地运行产物不进入 Git 索引。
- `npm ci`、47 项现有单元测试、覆盖率命令和 Chromium 启动检查通过。
- 父仓库集成测试可在 Windows 下正确导入 Googlemail ESM 模块。
- 新机器只需克隆主项目，不需要递归初始化子模块。

---

## 2. 当前基线

| 检查项 | 当前状态 |
| --- | --- |
| 主项目 Git | 已初始化，分支为 `main` |
| 主项目远端 | `superaddmin/main` |
| 本地源目录 | `F:\Googlemail`，不是 Git 仓库 |
| Node.js | `v24.15.0`，满足 `>=20.19.0` |
| npm | `11.12.1` |
| Googlemail 单元测试 | 6 个文件、47 项通过 |
| Chromium 启动检查 | 通过，仅加载本地 `data:` 页面 |
| 源锁文件 SHA-256 | `4C8148FD863BC597AE290A0C6D666FC49E4B520B53635EA7D7E4E157249C9569` |

---

## 3. 复制边界

### 3.1 纳入主项目

| 路径 | 用途 |
| --- | --- |
| `src/` | Googlemail 实现 |
| `tests/` | Vitest 单元测试 |
| `docs/` | 架构、配置和使用文档 |
| `package.json` | Node.js 项目定义 |
| `package-lock.json` | 依赖锁定 |
| `vitest.config.mjs` | 测试配置 |
| `.gitignore` | Googlemail 自身忽略规则 |
| `README.md`、`AGENTS.md` | 使用说明和项目规则 |
| `.codex/` | Googlemail 项目级规则、代理和技能配置 |

### 3.2 必须排除

| 路径或模式 | 原因 |
| --- | --- |
| `node_modules/` | 可重建依赖 |
| `browser-data/` | Cookie、会话和浏览器缓存 |
| `output/` | 账号处理结果、TOTP 密钥、日志和截图 |
| `coverage/` | 测试产物 |
| `test-results/`、`playwright-report/`、`test-temp-*/` | 测试产物 |
| `宝贝信息-*.txt` | 账号、密码、恢复邮箱和旧 TOTP 密钥 |
| `.env`、`.env.*` | 本地环境变量 |
| `*.log` | 运行日志 |
| `*.sqlite*` | 本地状态数据库 |
| 子项目 `.git/` | 避免嵌套仓库 |

父仓库和 `googlemail/.gitignore` 都应保留上述保护规则。敏感文件即使被忽略，也不应复制到集成目录。

---

## 4. 执行步骤

### 4.1 预检

```powershell
cd F:\Google_Manager

git status --short --branch
node --version
npm --version

$source = (Resolve-Path -LiteralPath 'F:\Googlemail').Path
$root = (Resolve-Path -LiteralPath 'F:\Google_Manager').Path
$destination = Join-Path $root 'googlemail'

if (-not $destination.StartsWith(
    $root + [System.IO.Path]::DirectorySeparatorChar,
    [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "目标目录越界: $destination"
}
```

### 4.2 首次受控复制

首次复制前目标目录应不存在。命令不使用 `/MIR` 或 `/PURGE`，避免隐式删除目标文件。

```powershell
if (Test-Path -LiteralPath $destination) {
    throw "目标目录已存在，请按第 8 节执行增量同步"
}

New-Item -ItemType Directory -Path $destination | Out-Null

robocopy $source $destination /E /COPY:DAT /DCOPY:DAT /R:2 /W:1 /NFL /NDL /NP `
    /XD '.git' 'node_modules' 'browser-data' 'output' 'coverage' `
        'test-results' 'playwright-report' 'test-temp-*' `
        '.codex\tmp' '.codex\.tmp' `
    /XF '.env' '.env.*' '宝贝信息-*.txt' '*.log' '*.sqlite' '*.sqlite-*'

$copyExitCode = $LASTEXITCODE
if ($copyExitCode -ge 8) {
    throw "robocopy 失败，退出码: $copyExitCode"
}
```

Robocopy 的 `0-7` 均属于成功或存在可接受差异，`8` 及以上才是失败。

### 4.3 调整复制后的本地路径

Googlemail 的项目级配置和文档必须使用集成后的路径：

```text
F:\Google_Manager\googlemail
F:/Google_Manager/googlemail
```

修改后检查旧路径已经清除：

```powershell
rg -n 'F:\\Googlemail|F:/Googlemail|f:\\Googlemail' googlemail --hidden
```

预期：无输出。

### 4.4 敏感路径检查

本检查应在首次复制完成、执行 `npm ci` 之前运行，用于确认复制命令没有带入源目录中的运行产物。安装和测试完成后，`node_modules/` 与 `coverage/` 可以在本地存在，但必须保持忽略且不进入 Git 索引。

```powershell
$unexpectedCopiedPaths = @(
    'googlemail\node_modules',
    'googlemail\browser-data',
    'googlemail\output',
    'googlemail\coverage'
)

foreach ($path in $unexpectedCopiedPaths) {
    if (Test-Path -LiteralPath $path) {
        throw "首次复制带入了运行时路径: $path"
    }
}

$sensitiveFiles = Get-ChildItem -Force googlemail -Recurse -File |
    Where-Object {
        $_.Name -match '^\.env' -or
        $_.Name -match '\.log$' -or
        $_.Name -match '\.sqlite' -or
        $_.Name -match '^宝贝信息-'
    }

if ($sensitiveFiles) {
    $sensitiveFiles | Select-Object FullName
    throw '集成目录包含敏感或运行时文件'
}
```

---

## 5. 本地操作界面

### 5.1 安装和验证

从父仓库执行时必须把工作目录切换到 `googlemail/`，因为当前实现使用 `process.cwd()` 定位账号文件、`output/` 和 `browser-data/`。

```powershell
cd F:\Google_Manager
Push-Location .\googlemail
try {
    npm ci
    npm test
    npm run test:coverage
    npm run test:startup
} finally {
    Pop-Location
}
```

### 5.2 独立 CLI 运行

除主页集成入口外，Googlemail 仍保留独立 CLI，供本地维护和排障使用：

```powershell
Push-Location F:\Google_Manager\googlemail
try {
    npm start
} finally {
    Pop-Location
}
```

`npm start` 和 `npm run test-login` 会读取本地账号文件并执行浏览器流程，不属于安装验证命令，也不应进入自动化 CI。

### 5.3 Flask 与主页集成（已实现）

`app/services/googlemail_service.py` 负责子进程适配，接口事实如下：

- 输入：主页选择的本地账号 ID 和非敏感运行选项；服务端生成任务 ID 与独立账号文件。
- 输出：仅返回任务状态、总数、完成/失败/待处理/同步/人工复核计数和脱敏错误码。
- 工作目录：固定为 `F:\Google_Manager\googlemail`。
- 运行目录：`googlemail/runtime/tasks/<task-id>/`，由 Git 忽略。
- 生命周期：启动、查询、取消、最长运行时间、Windows 进程树终止、进程退出码和最近任务内存状态。
- 并发：同一 Flask 进程只允许一个 Googlemail 任务运行，避免共用浏览器数据冲突。
- 结果同步：进程退出后先同步所有合法的完整或部分结果，再发布完成、取消、超时或失败终态；同步成功后清理包含密码与密钥的结果文件，同步失败时保留本地恢复文件并返回脱敏错误码。
- 敏感数据：密码、TOTP 密钥、Cookie、截图、子进程输出和结果文件不通过 HTTP 或 Flask 日志返回。
- testing 配置：`GOOGLEMAIL_EXECUTION_ENABLED=False`，用于本地审核时阻止真实任务。

主页使用以下接口：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/googlemail/status` | 查询依赖、执行开关和当前任务 |
| `POST` | `/api/googlemail/tasks` | 选择账号并启动任务 |
| `GET` | `/api/googlemail/tasks/<task-id>` | 查询任务状态与计数 |
| `POST` | `/api/googlemail/tasks/<task-id>/cancel` | 请求取消任务 |

---

## 6. 验证方案

### 6.1 Node.js 静态检查

```powershell
Get-ChildItem .\googlemail\src\*.mjs | ForEach-Object {
    node --check $_.FullName
    if ($LASTEXITCODE -ne 0) {
        throw "语法检查失败: $($_.FullName)"
    }
}
```

### 6.2 Googlemail 自身测试

```powershell
Push-Location .\googlemail
try {
    npm ci
    npm test
    npm run test:coverage
    npm run test:startup
} finally {
    Pop-Location
}
```

通过标准：

- 6 个测试文件、47 项测试全部通过。
- 覆盖率命令正常生成报告；覆盖率数值作为基线记录，不写成“100% 覆盖”。
- Chromium 启动检查只加载本地页面。

### 6.3 父仓库集成测试

父仓库使用 Node.js 内置测试器，不额外安装 Vitest：

```powershell
node --test .\tests\googlemail-local-copy.test.mjs
```

该测试负责验证：

- 必要源码、锁文件、文档和测试文件存在。
- Googlemail 忽略规则包含运行时与敏感路径。
- Windows ESM 使用 `pathToFileURL()` 正确导入。
- 使用有效的 20 字节 Base32 固定样例生成 6 位 TOTP。
- 使用临时合成文件调用实际导出的 `parseAccounts(filePath)`。

### 6.4 Flask 与适配器测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
```

该测试覆盖账号 API、登录会话、testing 执行保护、Googlemail 成功同步、注册进程前取消、超时、非零退出清理和 Windows 进程树终止。

---

## 7. Git 检查与提交

### 7.1 暂存前检查

```powershell
git status --short
git check-ignore -v --no-index `
    googlemail/node_modules/ `
    googlemail/browser-data/ `
    googlemail/output/ `
    googlemail/coverage/
```

### 7.2 暂存和索引检查

```powershell
git add .gitignore README.md SUBMODULE_INTEGRATION_PLAN.md googlemail tests\googlemail-local-copy.test.mjs

$trackedSensitive = git ls-files googlemail |
    Select-String -Pattern '(^|/)(node_modules|browser-data|output|coverage)/|(^|/)\.env|\.log$|\.sqlite|宝贝信息-'

if ($trackedSensitive) {
    $trackedSensitive
    throw 'Git 索引包含禁止提交的 Googlemail 文件'
}

git diff --cached --check -- ':!README.md'

$readmeBytes = [System.IO.File]::ReadAllBytes((Resolve-Path 'README.md'))
$readmeText = [System.Text.UTF8Encoding]::new($false, $true).GetString($readmeBytes)
$readmeTrailingSpaces = [regex]::Matches(
    $readmeText,
    '[ \t]+(?=\r?$)',
    [System.Text.RegularExpressions.RegexOptions]::Multiline
)

if ($readmeTrailingSpaces.Count -gt 0) {
    throw 'README.md 包含行尾空格或制表符'
}

git diff --cached --stat
git status --short
```

### 7.3 提交和推送

```powershell
git commit -m "feat: 以本地快照集成 Googlemail"
git push superaddmin main
```

---

## 8. 后续同步

Googlemail 没有远端仓库，后续更新仍从 `F:\Googlemail` 同步。

1. 确认父仓库工作区干净。
2. 执行下方增量复制命令。
3. 不使用 `/MIR` 或 `/PURGE`；源目录已删除的文件由维护者根据差异逐个 `git rm`。
4. 重新应用第 4.3 节路径适配。
5. 执行第 4.4、6、7 节全部检查。
6. 在提交信息中记录源锁文件 SHA-256。

建议每次同步先记录：

```powershell
Get-FileHash -Algorithm SHA256 F:\Googlemail\package-lock.json
git status --short --branch

$source = (Resolve-Path -LiteralPath 'F:\Googlemail').Path
$destination = (Resolve-Path -LiteralPath 'F:\Google_Manager\googlemail').Path

robocopy $source $destination /E /COPY:DAT /DCOPY:DAT /R:2 /W:1 /NFL /NDL /NP `
    /XD '.git' 'node_modules' 'browser-data' 'output' 'coverage' `
        'test-results' 'playwright-report' 'test-temp-*' `
        '.codex\tmp' '.codex\.tmp' `
    /XF '.env' '.env.*' '宝贝信息-*.txt' '*.log' '*.sqlite' '*.sqlite-*'

$copyExitCode = $LASTEXITCODE
if ($copyExitCode -ge 8) {
    throw "robocopy 失败，退出码: $copyExitCode"
}
```

---

## 9. 回滚

### 9.1 已提交后的回滚

优先使用可审计的 Git 回滚：

```powershell
git log --oneline -5
git revert <LOCAL_COPY_INTEGRATION_COMMIT>
git push superaddmin main
```

### 9.2 提交前中止

先查看将被清理的文件，不直接执行递归删除：

```powershell
git status --short -- googlemail tests\googlemail-local-copy.test.mjs
git clean -nd -- googlemail tests\googlemail-local-copy.test.mjs
```

确认目标严格等于 `F:\Google_Manager\googlemail`、且其中没有需要保留的工作后，再执行明确的清理操作。

---

## 10. 风险与控制

| 风险 | 控制措施 |
| --- | --- |
| 本地源与集成副本漂移 | 每次同步记录锁文件哈希并审查 Git 差异 |
| 敏感数据进入仓库 | 复制排除、双层 `.gitignore`、Git 索引负向检查 |
| Windows ESM 路径导入失败 | 集成测试统一使用 `pathToFileURL()` |
| CLI 在错误目录写入数据 | 所有运行命令显式切换到 `googlemail/` |
| 更新时误删本地文件 | Robocopy 禁用 `/MIR` 和 `/PURGE`，删除逐项审查 |
| 回滚破坏工作区 | 使用 `git revert`，清理前先 dry-run 和路径核验 |

---

## 11. 本次执行记录

- [x] 已确认主项目为现有 Git 仓库，不重复初始化。
- [x] 已采用本地复制，不创建 `.gitmodules`。
- [x] 已排除依赖、账号文件、浏览器数据、输出、日志和环境变量。
- [x] 已把复制后的项目级路径调整到 `F:\Google_Manager\googlemail`。
- [x] 已安装复制目录依赖。
- [x] 已通过 Googlemail 单元测试、覆盖率与启动检查。
- [x] 已通过父仓库本地复制集成测试。
- [x] 已实现 Flask 子进程适配器和主页 Googlemail 操作视图。
- [x] 已覆盖启动、查询、取消、超时、结果同步和 testing 执行保护。
- [x] 已完成 Git 索引敏感文件检查。
- [x] 已完成提交前审核；提交与推送结果以 Git 历史和交付记录为准。
- [x] 2026-09-16 全量审计复核：47 项单元测试全部通过，已与集中邮箱防盗中控台完成联动验证。

> 文件名 `SUBMODULE_INTEGRATION_PLAN.md` 为兼容历史引用而保留；当前方案明确不使用 Git submodule。
>
> **审核结论**：通过，可以按本计划执行和维护本地复制集成。
