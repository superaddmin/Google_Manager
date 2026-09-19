# 充值交付缺陷修复说明（2026-09-19）

## 结论与范围

本轮按用户“对项目存在的问题执行修复”的要求，修复四类已复现问题和直接相关前端缺陷。完整离线回归 204 项通过，隔离浏览器联调通过，已同步 Flask 使用的静态产物。**这不是全项目生产验收，整体维持 No-Go。**

- 基线：修复前工作区已包含 Antigravity 大量未提交修改；本轮不 reset、不整文件恢复、不提交 Git、不修改真实 `.env`。
- 仅使用内存/临时 SQLite、虚构账号/卡密/凭据及桩化上游；未调用真实充值、Google 授权、订阅或生产部署。
- 证据目录：`C:\Users\www\AppData\Local\Temp\google-manager-fixes-20260918-234421`。包含修复前文件、`baseline.json`、失败基线、回归日志和构建备份；日期按 Asia/Shanghai 记录。

## 修复内容

| 问题 | 修复与已验证范围 | 主要实现 |
| --- | --- | --- |
| live 可下载伪造的 Mock 账单 | live 下载明确 503；删除固定账单编号兜底。mock 只读取模拟任务；账单必须是当前会话签发的随机标识，跨会话不可下载 | `app/routes/recharge.py`、`app/services/recharge_service.py` |
| challenge 可省略套餐、跨会话换凭据复用 | 套餐必填；HMAC 绑定会话、模式、卡密、凭据、套餐和续费布尔值；五分钟有效、数据库条件更新一次消费；新任务不保存原始挑战，提交上游时移除本地挑战 | `app/models/one_time_token.py`、`app/services/recharge_service.py` |
| 普通 OAuth 成功后 state 仍可重放 | 普通与无 session 回调统一消费共享数据库 state；不匹配会话拒绝；取消/缺授权码也消费匹配状态；过期和并发单次消费有回归 | `app/routes/api.py`、`app/services/gmail_service.py` |
| unknown 查到成功但本地不回写 | 保留请求前 pending；不确定响应保持 unknown；查询核对并落库，通过旁表关联上游编号，校验响应身份/状态；查询失败不返回本地假成功，迟到处理状态/超时不覆盖终态 | `app/models/recharge_operation.py`、`app/services/recharge_service.py` |
| 同一卡密跨进程创建竞态 | 同模式卡密 HMAC 对应唯一 active_key，与任务同事务写入；failed/recalled 释放，成功/关闭保留门禁；两个独立进程竞争只产生一条记录 | `app/models/recharge_operation.py`、`tests/test_shared_recharge_state.py` |
| 页面查询/凭据生命周期 | 修复 click event 被当作查询字符串；unknown 纳入非重叠轮询，失败退避并限次，终态/切页停止；中止旧查询，拒绝旧账单响应覆盖新凭据；创建成功清空凭据，离开账单页清空输入和结果 | `frontend/src/components/RechargeView.jsx`、`frontend/src/services/api.js` |
| 页面下载与说明不准确 | 模拟任务下载只传任务编号，不在链接传卡密；真实凭证未开放时不展示下载按钮；修正未经证实的加密落盘和自动生成真实凭证文案 | `frontend/src/components/RechargeView.jsx` |

新建的 `OneTimeToken` 保存随机 token 的 SHA-256 摘要、HMAC 绑定及最小授权上下文，不保存原始充值凭据。Mock 账单缓存改用会话绑定摘要键，避免以原始 token 作为字典键；此缓存仍仅用于单进程模拟。

## 可复现验证

在仓库根目录、PowerShell 中运行：

```powershell
$env:PYTHONIOENCODING = 'utf-8'
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'
node --test tests/account-import.test.mjs tests/googlemail-local-copy.test.mjs tests/api-service.test.mjs
npm --prefix googlemail test
npm --prefix frontend run build
node --test tests/frontend-ui.test.mjs
.\.venv\Scripts\python.exe -m pip check
```

默认 build 会清空生成目录 `static`，执行前确认其中没有手工维护文件。本轮已盘点其三个生成物并备份。需要避免覆盖时使用既有构建入口的临时 outDir：

```powershell
$env:FRONTEND_TEST_DIST = Join-Path $env:TEMP 'google-manager-review-build'
npm --prefix frontend run build -- --outDir $env:FRONTEND_TEST_DIST
node --test tests/frontend-ui.test.mjs
Remove-Item Env:FRONTEND_TEST_DIST
```

| 验证 | 实测结果 | 证据 |
| --- | --- | --- |
| Python 全套 | 113/113，通过 | `python-final.log` |
| Node 离线用例 | 19/19，通过 | `node-final.log` |
| Googlemail 离线用例 | 47/47，通过 | `googlemail-final.log` |
| 前端浏览器回归 | 临时构建与实际 static 均 25/25，通过，其中新增充值专项 5 项；重复执行不重复计数 | `frontend-final.log`、`frontend-static-final.log` |
| 独立进程共享状态 | 已包含在 Python 113 项中：OAuth 竞争消费、跨进程 challenge、同卡密竞争创建 | `cross-process-tests.log`、`tests/test_shared_recharge_state.py` |
| 前端构建 | 临时构建及默认 static 构建通过，HTML/JS/CSS SHA-256 相同 | `build-reviewed.log`、`build-static.log` |
| Python 依赖一致性 | `No broken requirements found` | `pip-final.log` |

新增 `tests/test_recharge_safety.py`、`tests/test_oauth_state_safety.py`、`tests/test_shared_recharge_state.py`。既有测试改为通过真实挑战接口签发匹配凭据、显式注册 OAuth state，并保留业务成功/失败断言，没有删测或放宽状态断言。

项目没有声明统一 lint/typecheck/formatter 入口，本轮未新增工具链。Python 运行仍有既有 `datetime.utcnow()` 弃用和 SQLite 连接 ResourceWarning；测试退出码为 0，但不能将警告描述为不存在。pip check 仅检查安装依赖一致性，不是漏洞审计。未生成全量分支覆盖率报告。

## 真实浏览器连接隔离后端

使用 `create_app('testing')`、内存 SQLite、随机 loopback 端口、Mock 模式、禁用 Googlemail 执行器，并拦截后端外部充值 HTTP；不使用前端 API 桩替代此轮联调。

- 从登录页面进入充值页，校验虚构卡密、填入虚构 Session、人工勾选确认后创建成功（201）；提交后凭据输入清空、确认勾选复位。
- 通过本地任务编号查询（200）；下载链接仅含 task_no。响应为 TXT/200，包含 MOCK 标识，不出现虚假 CONFIRMED 声明。
- 查询模拟账单后随机 slug 的收据下载为 200；旧固定 `inv_slug_001` 返回 404，未再产生伪造 paid 收据。
- 页面取消/恢复模拟自动续费后查询分别显示关闭/开启；离开账单页再返回时凭据与账单结果已清空。
- 观察到的控制台错误仅为主动访问旧固定收据标识时预期的 404。相关结果保存在 `browser-task-download.json`、`browser-billing-download.json`、`browser-billing-resume.json`。
- 已退出登录、关闭本轮独立浏览器标签，停止本轮 PID 75384；端口 3872 确认不再可达，没有遗留本轮后台服务。

## 部署与回滚注意

1. 新增 `one_time_tokens` 和 `recharge_operations` 表，由现有工厂的建表流程加载。没有修改现有 RechargeTask 列定义，也没有删除历史任务；发布前备份数据库，并在预发布副本验收建表权限、兼容性和恢复。
2. 多 worker 必须连接同一数据库并使用相同、稳定的 SECRET_KEY。HMAC 门禁和会话依赖此密钥；不得在服务运行中随意轮换。密钥轮换需要维护窗口、旧会话失效及业务门禁数据的独立迁移方案。
3. 旧进程内 challenge/OAuth state 不迁移。部署前停止接收新请求并等待在途请求处理完毕；旧链接/挑战应重新生成，不可为了兼容而退回只检查 session 的旧校验。
4. 旧充值任务没有上游旁表映射时按卡密查询核对，再结构化绑定上游编号；遗留数据迁移、多实例部署与真实上游契约仍须独立验收。
5. 已同步静态产物 `index-4e9221d8.js`、CSS 和 HTML，旧生成物保存在证据目录 `static-before-sync`。回滚时一起处理前后端版本；旧版本存在本轮漏洞，若必须回滚应先维持 RECHARGE_MODE=disabled，并暂停 OAuth 授权入口，不建议恢复旧活跃令牌。
6. 本轮不执行生产数据库迁移、镜像发布、真实充值或配置密钥。不得直接将完整工作区回退至 Git HEAD，避免覆盖其他实施方未提交修改。

## 未解决的生产门槛

- 没有正式授权的真实卡密上游/凭证契约和履约验收，live 收据能力明确关闭；前端不再把模拟收据当作真实交付能力。
- unknown 恢复由用户查询/前端轮询触发，并无后台自动对账 daemon；上游幂等键是否被真正执行、撤回/关闭的所有并发顺序仍需授权联调。
- 旧任务的原始卡密与历史 challenge 数据没有清除/加密迁移；本轮禁止新增原始挑战不等于历史隐私问题全部解决。
- 当前 Origin 检查与 challenge 绑定不等于完整的全接口 CSRF token 防护；安全守护权限、恢复、多 worker 工作机制仍有待验范围。
- Mock 订阅状态不是持久的真实订阅系统，跨进程/重启的一致性不在其保证范围内。
- Docker 引擎在 9 月 18 日晚的上轮证据中已可达；本轮没有复测引擎、构建/运行容器，也没有 TLS、权限、健康检查和备份恢复验收，不能将“引擎可达”当作部署通过。
- 非测试源码配置默认 `RECHARGE_MODE=disabled` 未改变，TestingConfig 明确使用 mock。未读取真实部署配置，不能声称所有运行环境实际模式均已核实。

因此，本轮可以交付已修复的代码、静态产物和回归证据，但不能签署生产 Go。
