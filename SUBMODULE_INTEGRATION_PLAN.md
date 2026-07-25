# Googlemail 子模块集成执行计划

> **主项目**: Google_Manager (Flask + React 账号管理系统)  
> **子模块**: Googlemail (Node.js + Playwright Google 账号安全自动化工具)  
> **目标路径**: `F:\Google_Manager\googlemail`  
> **制定日期**: 2026-07-25

---

## 目录

1. [环境准备与兼容性检查](#1-环境准备与兼容性检查)
2. [子模块添加流程](#2-子模块添加流程)
3. [版本控制策略](#3-版本控制策略)
4. [集成测试方案](#4-集成测试方案)
5. [异常处理与回滚机制](#5-异常处理与回滚机制)
6. [文档记录要求](#6-文档记录要求)

---

## 1. 环境准备与兼容性检查

### 1.1 前置条件确认

| 检查项 | 要求 | 验证命令 |
|--------|------|----------|
| Git | >= 2.20 | `git --version` |
| Node.js | >= 20.19.0（子模块要求） | `node --version` |
| npm | >= 9.x（随 Node.js 自带） | `npm --version` |
| Python | >= 3.8（主项目后端） | `python --version` |
| 磁盘空间 | >= 2GB（含 node_modules） | 检查 `F:\` 可用空间 |

### 1.2 兼容性检查步骤

```powershell
# 步骤 1：检查 Git 版本
git --version
# 预期输出: git version 2.xx.x 或更高

# 步骤 2：检查 Node.js 版本（子模块要求 >= 20.19.0）
node --version
# 预期输出: v20.19.0 或更高

# 步骤 3：检查 npm 版本
npm --version

# 步骤 4：检查 Python 版本（主项目后端）
python --version
# 预期输出: Python 3.8.x 或更高

# 步骤 5：验证子模块依赖可安装（在 F:\Googlemail 目录下）
cd F:\Googlemail
npm ci --dry-run 2>&1
# 检查是否有依赖冲突错误

# 步骤 6：验证子模块测试可运行
npx vitest run --reporter=verbose 2>&1
```

### 1.3 兼容性检查清单

| # | 检查项目 | 状态 | 备注 |
|---|---------|------|------|
| 1 | Git 版本 >= 2.20 | ☐ | |
| 2 | Node.js >= 20.19.0 | ☐ | 子模块 `package.json` `engines` 字段要求 |
| 3 | Python >= 3.8 | ☐ | 主项目后端依赖 |
| 4 | npm ci 依赖安装无报错 | ☐ | |
| 5 | vitest 测试全部通过 | ☐ | |
| 6 | 磁盘空间充足 | ☐ | |

---

## 2. 子模块添加流程

### 2.1 阶段一：初始化 Git 仓库

两个项目目录当前均**未初始化**为 Git 仓库，需要先分别初始化。

#### 2.1.1 初始化子模块仓库 (F:\Googlemail)

```powershell
cd F:\Googlemail

# 1. 初始化 Git 仓库
git init

# 2. 配置用户信息（如未全局配置）
git config user.name "GoogleManager-Bot"
git config user.email "bot@google-manager.local"

# 3. 确认 .gitignore 已存在且内容正确
# 关键忽略项：node_modules/、browser-data/、output/、.env、.env.*、*.log、coverage/
# 当前 .gitignore 已包含以上所有敏感路径，无需修改

# 4. 添加所有非忽略文件到暂存区
git add .

# 5. 确认暂存区不包含敏感文件
git status
# 检查：不应包含 browser-data/、output/、.env、node_modules/ 等

# 6. 创建初始提交
git commit -m "feat: initial commit - Google 2FA automation tool

- Node.js + Playwright 自动化修改 Google 账号两步验证
- 支持 TOTP 验证码生成与验证
- 包含完整的单元测试套件（vitest）
- 要求 Node.js >= 20.19.0"

# 7. 创建主分支（如默认不是 main）
git branch -M main
```

#### 2.1.2 初始化主项目仓库 (F:\Google_Manager)

```powershell
cd F:\Google_Manager

# 1. 初始化 Git 仓库
git init

# 2. 配置用户信息
git config user.name "GoogleManager-Bot"
git config user.email "bot@google-manager.local"

# 3. 创建 .gitignore 文件（如不存在）
# 见下方 .gitignore 模板

# 4. 添加所有文件
git add .

# 5. 创建初始提交
git commit -m "feat: initial commit - Google account manager system

- Flask + React 前后端分离架构
- 账号批量导入/管理/搜索
- TOTP 2FA 验证码生成
- 出售状态管理与修改历史追踪"

# 6. 创建主分支
git branch -M main
```

#### 主项目 .gitignore 模板

若 `F:\Google_Manager` 下尚无 `.gitignore`，需创建：

```gitignore
# Python
__pycache__/
*.py[cod]
*.pyo
*.egg-info/
.eggs/
venv/
.venv/

# 数据库
instance/*.db

# 环境变量
.env
.env.*

# Node
node_modules/

# IDE
.vscode/
.idea/

# 日志
*.log

# 子模块（将由 Git 自动管理）
googlemail/
```

### 2.2 阶段二：添加子模块

```powershell
cd F:\Google_Manager

# 1. 添加 Googlemail 作为子模块
# 使用本地路径（因为当前无远程仓库）
git submodule add F:\Googlemail googlemail

# 该命令会：
# - 克隆 F:\Googlemail 到 F:\Google_Manager\googlemail\
# - 在仓库根目录创建 .gitmodules 文件
# - 将子模块信息记录到 .git/config

# 2. 验证子模块添加成功
git submodule status
# 预期输出: <commit-hash> googlemail (heads/main)

# 3. 查看生成的 .gitmodules 文件
cat .gitmodules
```

### 2.3 阶段三：子模块初始化与验证

```powershell
# 1. 初始化子模块（克隆后首次需要）
git submodule init

# 2. 更新子模块到记录的提交
git submodule update

# 3. 进入子模块目录，安装依赖
cd googlemail
npm ci

# 4. 运行子模块测试验证功能正常
npm test

# 5. 返回主项目目录
cd ..
```

### 2.4 阶段四：提交子模块配置

```powershell
cd F:\Google_Manager

# 1. 查看变更
git status
# 预期变更：
# - .gitmodules (新文件)
# - googlemail (新子模块)

# 2. 提交子模块配置
git add .gitmodules googlemail
git commit -m "feat: add Googlemail as a git submodule

- 集成 Google 2FA 自动化工具作为 googlemail/ 子模块
- 提供 TOTP 验证码生成与账号安全设置自动化能力
- 子模块路径: googlemail/"
```

### 2.5 完整操作流程总结

```
┌─────────────────────────────────────────────────────────────┐
│  阶段一：初始化 Git 仓库                                        │
│  ├─ F:\Googlemail:   git init → git add → git commit         │
│  └─ F:\Google_Manager: git init → git add → git commit       │
├─────────────────────────────────────────────────────────────┤
│  阶段二：添加子模块                                            │
│  └─ git submodule add F:\Googlemail googlemail              │
├─────────────────────────────────────────────────────────────┤
│  阶段三：子模块初始化                                          │
│  ├─ git submodule init                                       │
│  ├─ git submodule update                                     │
│  └─ cd googlemail && npm ci && npm test                      │
├─────────────────────────────────────────────────────────────┤
│  阶段四：提交配置                                              │
│  └─ git add → git commit                                     │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. 版本控制策略

### 3.1 分支策略

#### 主项目分支

| 分支名 | 用途 | 说明 |
|--------|------|------|
| `main` | 稳定发布分支 | 始终保持可运行状态 |
| `develop` | 开发分支 | 日常开发集成 |
| `feature/*` | 功能分支 | 新功能开发，合并到 develop |
| `hotfix/*` | 紧急修复 | 修复线上问题，合并到 main 和 develop |

#### 子模块分支

| 分支名 | 用途 | 说明 |
|--------|------|------|
| `main` | 稳定版本 | 主项目 submodule 跟踪此分支 |
| `develop` | 开发版本 | 功能开发与测试 |

### 3.2 子模块跟踪策略

```powershell
# 让子模块跟踪特定分支（推荐跟踪 main 稳定分支）
cd F:\Google_Manager
git config -f .gitmodules submodule.googlemail.branch main
```

配置后 `.gitmodules` 内容示例：

```ini
[submodule "googlemail"]
    path = googlemail
    url = F:\Googlemail
    branch = main
```

### 3.3 提交规范

采用 [Conventional Commits](https://www.conventionalcommits.org/) 规范：

| 类型 | 说明 | 示例 |
|------|------|------|
| `feat` | 新功能 | `feat: 集成 Googlemail 子模块自动化能力` |
| `fix` | Bug 修复 | `fix: 修复子模块路径引用错误` |
| `chore` | 维护性工作 | `chore(submodule): 更新 googlemail 到 v1.1.0` |
| `docs` | 文档更新 | `docs: 更新子模块使用说明` |
| `test` | 测试相关 | `test: 添加子模块集成测试` |
| `refactor` | 重构 | `refactor: 调整子模块调用接口` |

**子模块更新提交格式**：

```
chore(submodule): update googlemail to <commit-hash>

- 更新到 googlemail@v1.1.0
- 修复 TOTP 验证超时问题
- 新增批量账号处理支持
```

### 3.4 子模块更新机制

#### 3.4.1 更新子模块到最新版本

```powershell
# 方式一：更新到子模块 main 分支最新提交
cd F:\Google_Manager\googlemail
git pull origin main
cd ..
git add googlemail
git commit -m "chore(submodule): update googlemail to latest main"

# 方式二：更新到特定提交
cd googlemail
git checkout <commit-hash>
cd ..
git add googlemail
git commit -m "chore(submodule): pin googlemail to <commit-hash>"
```

#### 3.4.2 更新频率

| 场景 | 频率 | 说明 |
|------|------|------|
| 安全修复 | 即时 | 子模块安全漏洞修复后立即更新 |
| 功能更新 | 按需 | 主项目需要新功能时更新 |
| 定期同步 | 每月 | 检查子模块是否有重要更新 |

#### 3.4.3 克隆含子模块的项目

```powershell
# 首次克隆（含子模块）
git clone --recurse-submodules <repo-url>

# 或克隆后初始化子模块
git clone <repo-url>
cd Google_Manager
git submodule init
git submodule update
```

---

## 4. 集成测试方案

### 4.1 测试层次

```
┌─────────────────────────────────────┐
│          集成测试 (Integration)       │
│  主项目调用子模块的接口正确性验证       │
├─────────────────────────────────────┤
│         功能测试 (Functional)         │
│  子模块在嵌入路径下的功能完整性验证     │
├─────────────────────────────────────┤
│         冒烟测试 (Smoke)              │
│  子模块基本可运行性验证               │
└─────────────────────────────────────┘
```

### 4.2 冒烟测试（快速验证）

```powershell
# 测试 1：子模块目录完整性
cd F:\Google_Manager\googlemail
ls src/       # 应包含所有 .mjs 源文件
ls tests/     # 应包含所有 .test.mjs 测试文件
ls package.json  # 应存在

# 测试 2：依赖安装
npm ci
# 预期：无错误退出

# 测试 3：启动检查
npm run test:startup
# 预期：Chromium 启动成功，不访问 Google 页面

# 测试 4：基本功能验证
npm test
# 预期：所有 vitest 单元测试通过
```

### 4.3 功能测试（子模块完整性）

```powershell
# 测试 1：单元测试覆盖率
npm run test:coverage
# 预期：覆盖率报告正常生成

# 测试 2：TOTP 模块功能
node -e "
import('./googlemail/src/totp.mjs').then(m => {
  const code = m.generateTOTP('JBSWY3DPEHPK3PXP');
  console.log('TOTP generated:', code);
  console.log('Length valid:', code.length === 6);
})"

# 测试 3：账号解析模块
node -e "
import('./googlemail/src/account-parser.mjs').then(m => {
  const result = m.parseAccountLine('test@gmail.com--password123--recovery@mail.com--SECRETKEY--note');
  console.log('Parse result:', JSON.stringify(result, null, 2));
})"

# 测试 4：配置加载
node -e "
import('./googlemail/src/config.mjs').then(m => {
  console.log('Config loaded successfully');
})"
```

### 4.4 集成测试（主项目与子模块交互）

在 `F:\Google_Manager\` 下创建集成测试脚本 `test_submodule_integration.mjs`：

```javascript
// test_submodule_integration.mjs
// 主项目与 Googlemail 子模块集成测试

import { describe, it, expect } from 'vitest';
import { readFileSync, existsSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SUBMODULE_PATH = resolve(__dirname, 'googlemail');

describe('Googlemail 子模块集成测试', () => {

  it('子模块目录存在且结构完整', () => {
    expect(existsSync(SUBMODULE_PATH)).toBe(true);
    expect(existsSync(resolve(SUBMODULE_PATH, 'package.json'))).toBe(true);
    expect(existsSync(resolve(SUBMODULE_PATH, 'src'))).toBe(true);
    expect(existsSync(resolve(SUBMODULE_PATH, 'tests'))).toBe(true);
  });

  it('子模块 package.json 可解析', () => {
    const pkg = JSON.parse(
      readFileSync(resolve(SUBMODULE_PATH, 'package.json'), 'utf-8')
    );
    expect(pkg.name).toBe('google-2fa-tool');
    expect(pkg.type).toBe('module');
  });

  it('子模块 Node.js 版本要求与当前环境兼容', () => {
    const pkg = JSON.parse(
      readFileSync(resolve(SUBMODULE_PATH, 'package.json'), 'utf-8')
    );
    const required = pkg.engines?.node || '>=18.0.0';
    const current = process.version;
    // 简单版本检查（生产环境建议使用 semver 库）
    const requiredVer = required.replace(/[>=~\s]/g, '').split('.')[0];
    const currentVer = parseInt(current.replace('v', '').split('.')[0]);
    expect(currentVer).toBeGreaterThanOrEqual(parseInt(requiredVer));
  });

  it('子模块核心源文件存在', () => {
    const requiredFiles = [
      'main.mjs', 'account-parser.mjs', 'config.mjs',
      'google-automator.mjs', 'totp.mjs', 'verify-2fa.mjs',
      'startup-check.mjs', 'test-login.mjs', 'redaction.mjs'
    ];
    for (const file of requiredFiles) {
      expect(existsSync(resolve(SUBMODULE_PATH, 'src', file))).toBe(true);
    }
  });

  it('子模块测试文件存在', () => {
    const requiredTests = [
      'account-parser.test.mjs', 'config.test.mjs',
      'google-automator.test.mjs', 'totp.test.mjs',
      'verify-2fa.test.mjs', 'redaction.test.mjs'
    ];
    for (const file of requiredTests) {
      expect(existsSync(resolve(SUBMODULE_PATH, 'tests', file))).toBe(true);
    }
  });

  it('子模块 .gitignore 包含敏感路径', () => {
    const gitignore = readFileSync(
      resolve(SUBMODULE_PATH, '.gitignore'), 'utf-8'
    );
    expect(gitignore).toContain('browser-data');
    expect(gitignore).toContain('output');
    expect(gitignore).toContain('.env');
    expect(gitignore).toContain('node_modules');
  });

  it('子模块文档目录存在', () => {
    expect(existsSync(resolve(SUBMODULE_PATH, 'docs'))).toBe(true);
    expect(existsSync(resolve(SUBMODULE_PATH, 'docs', 'README.md'))).toBe(true);
  });

  it('TOTP 模块可导入且正常工作', async () => {
    const totp = await import(resolve(SUBMODULE_PATH, 'src/totp.mjs'));
    expect(typeof totp.generateTOTP).toBe('function');
    const code = totp.generateTOTP('JBSWY3DPEHPK3PXP');
    expect(code).toMatch(/^\d{6}$/);
  });

  it('账号解析模块可导入且正常工作', async () => {
    const parser = await import(
      resolve(SUBMODULE_PATH, 'src/account-parser.mjs')
    );
    expect(typeof parser.parseAccountLine).toBe('function');
    const result = parser.parseAccountLine(
      'test@gmail.com--password123--recovery@mail.com--SECRETKEY--note'
    );
    expect(result).toBeDefined();
  });
});
```

运行集成测试：

```powershell
cd F:\Google_Manager
npx vitest run test_submodule_integration.mjs
```

### 4.5 测试通过标准

| 测试类别 | 通过标准 |
|---------|---------|
| 冒烟测试 | 全部 4 项通过 |
| 功能测试 | 全部 4 项通过，子模块单元测试 100% 通过 |
| 集成测试 | 全部 9 项通过 |

---

## 5. 异常处理与回滚机制

### 5.1 异常场景与处理方案

#### 场景 A：Git 初始化失败

| 错误现象 | 可能原因 | 解决方案 |
|---------|---------|---------|
| `git: command not found` | Git 未安装 | 安装 Git for Windows，添加到 PATH |
| `fatal: not a git repository` | 路径错误 | 确认 `cd` 到正确目录 |
| 权限不足 | 目录权限问题 | 以管理员身份运行 PowerShell |

#### 场景 B：子模块添加失败

| 错误现象 | 可能原因 | 解决方案 |
|---------|---------|---------|
| `'F:\Googlemail' already exists in the index` | 已存在同名路径 | 删除已有目录，清理 git 缓存 |
| `not a git repository` | 子模块仓库未初始化 | 先执行 `git init` 在 Googlemail 目录 |
| 网络/路径不可达 | 源路径不存在 | 确认 `F:\Googlemail` 目录存在且可访问 |

**恢复步骤**：

```powershell
# 如果子模块添加失败，清理残留
cd F:\Google_Manager
git submodule deinit -f googlemail        # 取消子模块注册
git rm -f googlemail                       # 从索引中移除
Remove-Item -Recurse -Force .git/modules/googlemail  # 清理 .git 缓存
Remove-Item -Recurse -Force googlemail -ErrorAction SilentlyContinue  # 删除目录
```

#### 场景 C：版本冲突

| 冲突类型 | 表现 | 解决策略 |
|---------|------|---------|
| 子模块提交不一致 | `git status` 显示 `modified: googlemail (new commits)` | 确认是否需要更新，执行 `git submodule update` 或提交新指针 |
| Node.js 版本不兼容 | `npm ci` 报 engines 错误 | 升级 Node.js 到 >= 20.19.0，或使用 nvm 切换版本 |
| 依赖冲突 | `npm ci` 报依赖冲突 | 删除 `node_modules` 和 `package-lock.json`，重新 `npm ci` |
| 合并冲突 | `.gitmodules` 或子模块指针冲突 | 手动解决冲突，选择正确的子模块提交哈希 |

#### 场景 D：子模块运行异常

```powershell
# 1. 检查子模块状态
cd F:\Google_Manager
git submodule status
# 预期：开头无 '-' 号（已初始化），无 '+' 号（提交一致）

# 2. 强制重新初始化子模块
git submodule deinit -f googlemail
git submodule update --init --recursive

# 3. 重新安装依赖
cd googlemail
Remove-Item -Recurse -Force node_modules -ErrorAction SilentlyContinue
npm ci
npm test

# 4. 如仍有问题，检查 Playwright 浏览器
npx playwright install chromium
```

### 5.2 回滚机制

#### 5.2.1 完全回滚（移除子模块）

```powershell
cd F:\Google_Manager

# 步骤 1：取消子模块注册
git submodule deinit -f googlemail

# 步骤 2：从 Git 索引中移除
git rm -f googlemail

# 步骤 3：清理 .git/modules 中的缓存
Remove-Item -Recurse -Force .git/modules/googlemail

# 步骤 4：删除物理目录
Remove-Item -Recurse -Force googlemail -ErrorAction SilentlyContinue

# 步骤 5：提交回滚
git add .gitmodules
git commit -m "revert: remove googlemail submodule"
```

#### 5.2.2 部分回滚（回退子模块版本）

```powershell
cd F:\Google_Manager\googlemail

# 查看子模块提交历史
git log --oneline -10

# 回退到指定提交
git checkout <target-commit-hash>

# 返回主项目，更新指针
cd ..
git add googlemail
git commit -m "chore(submodule): rollback googlemail to <target-commit-hash>"
```

#### 5.2.3 回滚检查清单

| # | 检查项 | 状态 |
|---|--------|------|
| 1 | 确认回滚原因（功能异常/版本不兼容/安全漏洞） | ☐ |
| 2 | 备份当前状态（`git stash` 或创建备份分支） | ☐ |
| 3 | 执行回滚操作 | ☐ |
| 4 | 验证回滚后状态（`git submodule status`） | ☐ |
| 5 | 运行冒烟测试确认功能正常 | ☐ |
| 6 | 提交回滚记录 | ☐ |

---

## 6. 文档记录要求

### 6.1 子模块结构说明

在 `F:\Google_Manager\docs\` 目录下创建 `submodule-googlemail.md`：

```markdown
# Googlemail 子模块结构说明

## 基本信息
- **名称**: googlemail (google-2fa-tool)
- **版本**: 1.0.0
- **类型**: Node.js ESM 模块
- **运行时**: Node.js >= 20.19.0
- **路径**: googlemail/

## 目录结构
googlemail/
├── src/                    # 源代码
│   ├── main.mjs           # 主入口，自动化修改 2FA
│   ├── account-parser.mjs  # 账号信息解析
│   ├── config.mjs         # 配置管理
│   ├── google-automator.mjs # Playwright 自动化核心
│   ├── totp.mjs           # TOTP 验证码生成
│   ├── verify-2fa.mjs     # 2FA 验证
│   ├── startup-check.mjs   # 启动环境检查
│   ├── test-login.mjs     # 登录测试
│   └── redaction.mjs      # 敏感信息脱敏
├── tests/                  # 单元测试
├── docs/                   # 文档
├── output/                 # 输出目录（.gitignore 忽略）
├── browser-data/           # 浏览器数据（.gitignore 忽略）
├── package.json
└── vitest.config.mjs

## 主要依赖
- playwright: 1.60.0 — 浏览器自动化
- otplib: 13.4.1 — TOTP 验证码
- vitest: 4.1.10 — 测试框架

## 与主项目的关系
- 主项目提供账号数据管理（导入/搜索/状态管理）
- 子模块提供账号 2FA 自动化修改能力
- 主项目通过子模块暴露的 API 接口调用自动化功能
```

### 6.2 更新日志

在 `F:\Google_Manager\CHANGELOG.md` 中记录子模块相关变更：

```markdown
# Changelog

## 子模块变更

| 日期 | 变更类型 | 描述 | 操作人 |
|------|---------|------|--------|
| 2026-07-25 | 新增 | 添加 googlemail 子模块 @ 初始提交 | - |
| | | | |

### 变更记录格式
每次子模块更新时，按以下格式记录：

| 日期 | 旧版本 | 新版本 | 变更内容 | 操作人 |
|------|--------|--------|---------|--------|
| YYYY-MM-DD | <old-hash> | <new-hash> | 变更说明 | 姓名 |
```

### 6.3 维护责任人

| 角色 | 职责 | 联系方式 |
|------|------|---------|
| 主项目负责人 | 主项目整体维护，子模块集成决策 | TBD |
| 子模块负责人 | Googlemail 功能开发与维护 | TBD |
| 集成负责人 | 子模块版本管理、更新与测试 | TBD |

### 6.4 文档清单

| 文档 | 路径 | 内容 | 状态 |
|------|------|------|------|
| 执行计划 | `SUBMODULE_INTEGRATION_PLAN.md` | 本文档 | ☐ 已创建 |
| 子模块结构说明 | `docs/submodule-googlemail.md` | 子模块架构与使用说明 | ☐ 待创建 |
| 更新日志 | `CHANGELOG.md` | 子模块变更记录 | ☐ 待创建 |
| 子模块 README | `googlemail/README.md` | 子模块自述（已存在） | ☑ 已有 |
| 子模块文档 | `googlemail/docs/` | 架构/使用/配置指南（已存在） | ☑ 已有 |

### 6.5 主项目 README 更新

在 `README.md` 中新增子模块相关章节：

```markdown
## 📦 子模块

本项目包含以下 Git 子模块：

| 子模块 | 路径 | 说明 |
|--------|------|------|
| [Googlemail](googlemail/) | `googlemail/` | Google 账号 2FA 自动化修改工具 |

### 克隆含子模块的项目

```bash
git clone --recurse-submodules <repo-url>
```

### 更新子模块

```bash
git submodule update --remote
```
```

---

## 附录

### A. 命令速查表

| 操作 | 命令 |
|------|------|
| 查看子模块状态 | `git submodule status` |
| 初始化子模块 | `git submodule init` |
| 更新子模块 | `git submodule update` |
| 更新到远程最新 | `git submodule update --remote` |
| 递归更新所有子模块 | `git submodule update --init --recursive` |
| 移除子模块 | 见 §5.2.1 |
| 查看子模块日志 | `cd googlemail && git log` |
| 查看子模块差异 | `git diff --submodule` |

### B. 风险矩阵

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|------|---------|
| Node.js 版本不兼容 | 子模块无法运行 | 中 | 预先版本检查，使用 nvm 管理 |
| 子模块依赖安装失败 | 功能不可用 | 低 | npm ci 代替 npm install，锁定版本 |
| 子模块与主项目路径冲突 | 构建失败 | 低 | 独立子目录，不共享依赖 |
| 敏感文件泄露 | 安全风险 | 低 | .gitignore 多重检查，pre-commit hook |
| 子模块更新引入 Breaking Change | 集成失败 | 中 | 锁定版本，更新前在测试分支验证 |

### C. 执行时间线

| 阶段 | 预计操作 | 依赖 |
|------|---------|------|
| 环境检查 | 10 分钟 | 无 |
| Git 初始化 | 5 分钟 | 环境检查通过 |
| 子模块添加 | 10 分钟 | Git 初始化完成 |
| 集成测试 | 15 分钟 | 子模块添加完成 |
| 文档更新 | 10 分钟 | 集成测试通过 |
| **总计** | **约 50 分钟** | |

---

> **文档版本**: v1.0  
> **最后更新**: 2026-07-25  
> **审核状态**: 待审核