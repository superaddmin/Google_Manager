# Chat GPT充值中心与 Googlemail 入口说明

更新日期：2026-09-26。测试服务器继续使用 `123.206.210.86`。

CDK 首期功能已更新到上述服务器，工作台已启用；具名账号获取和在线验收见 [CDK 服务器部署记录](cdk-server-deployment-2026-09-25.md)。真实充值仍为 disabled，SMTP 未配置。库存发行、核销、配置及剩余门禁见 [CDK 实现与操作手册](cdk-implementation-and-operations-2026-09-25.md)。完整设计与后续范围见 [CDK 端到端方案](cdk-end-to-end-product-technical-plan-2026-09-25.md)。

## 1. 入口与职责

| 路径 | 页面名称 | 职责 |
| --- | --- | --- |
| `/`、`/recharge` | Chat GPT充值中心 | 默认显示平台 CDK 验证、兑换与本人核销记录 |
| `/Googlemail` | Googlemail | Google 邮箱账号库、Gmail、批量授权与现有邮箱运维功能 |
| `/admin` | Chat GPT充值中心 · 管理后台 | 默认显示 CDK 卡密系统：具名员工、库存、制卡、审批、分发、核销与审计 |
| `/admin?tab=orders` | 原充值订单 | 非托管订单统计、查询、履约与人工对账 |
| `/recharge?service=legacy` | 原卡密订单与订阅服务 | 保留旧供应商卡密协议及订阅工具 |
| `/admin/cdk`、`/recharge/cdk` | 兼容地址 | 分别加载整合后的管理后台、用户充值中心 |

`/Googlemail/` 及子路径同样加载邮箱应用；原邮箱管理书签需要更新。管理后台顶部可切换“CDK 卡密管理”和“原充值订单”。CDK 使用独立具名员工、角色和渠道范围；原充值订单与邮箱后台保留原共享管理员认证，原管理员会话不能取得 CDK 权限。OAuth 回调仍为 `/api/gmail/oauth/callback`。

## 2. CDK 生成与前端使用

1. 访问 `http://123.206.210.86/admin`，使用 CDK 具名员工登录。当前管理员为 `admin@cattoken.vip`（2026-09-26 按负责人要求由 `cdk-admin` 更名并重置密码），复核员仍为 `cdk-reviewer`。管理员使用负责人本次指定的密码，仓库不记录明文；由服务器管理员在受控终端执行 `sudo cat /opt/google-manager-online-test-keys/cdk-staff-initial-20260925.json` 可获取当前受控凭据，文件名虽保留初始化日期，内容已更新。旧管理员名称不可继续登录，旧 CDK 管理员会话已注销。
2. 在“渠道”创建交付渠道，在“权益”创建 PLUS/PRO 权益版本。
3. 在“供应商库存”导入合法供应商原码，检查预览后提交并逐份验证。每张平台卡必须绑定一份已验证、有效期足够的库存。
4. 在“发行批次”新建批次，填写权益、渠道、数量（1–100）、起止时间；点击该批次的“生成 CDK 卡密”，再申请激活。
5. 另一位具名复核员在“审批”批准激活。发行员工在“卡密”分发、勾选后申请导出，复核员批准后在“导入与导出”下载平台码。不得将供应商原码交给客户。
6. 用户打开 `http://123.206.210.86/`，输入生成的 GM1 卡密并验证，核对权益、Session JSON 对应邮箱与协议后确认核销。使用“查询最新状态”和“加载记录”查看结果；网络中断保留请求编号并找回提交结果。

测试服务器目前真实充值通道关闭，页面会明确提示并禁用确认核销。可配置渠道、权益与批次，但无已验证库存时不能生成可履约卡；不要在持久业务库中伪造库存。启用通道及真实库存的步骤见 [CDK 操作手册](cdk-implementation-and-operations-2026-09-25.md#5-启用前人工配置)。完整生成和核销链路通过临时数据库及合成上游测试验证。

## 3. 原充值订单操作

1. 访问 `http://123.206.210.86/admin?tab=orders`，或在管理后台点击“原充值订单”，使用服务器已配置的原管理员密码登录。顶部可前往用户充值页或邮箱管理。
2. 概览显示全部、处理中、已完成及待核对订单；待核对按订单去重，包括创建状态 unknown 或操作状态 unknown。
3. 可按订单号片段、完整邮箱或完整卡密搜索，并按状态和真实/模拟来源筛选，默认每页 20 条。邮箱搜索适用于已规范化存储的邮箱。列表只显示卡密尾号。
4. 点击“查看详情”检查订单状态、上游任务号、最近操作和对账历史。页面查询只读本地数据库，不隐式请求上游。
5. 在启用且来源匹配的模式下，点击“同步上游状态”触发服务查询；待处理/处理中订单可确认撤回，已完成订单可确认关闭。后端重新检查状态和并发操作，不根据浏览器传入的卡密/邮箱决定操作对象。
6. 创建结果不确定时，先在上游查询并归档证据，再提交“已创建”或“确认未创建”。撤回/关闭结果不确定时，可提供上游当前任务和明确的未执行依据，解除本次未知操作。必须提交证据来源、至少 8 字符引用编号、64 位 SHA-256、带时区转换的观测时间及确认。不能将超时或一次查无结果当作未执行证据。
7. 请求失败显示服务返回的错误；操作后重新读取本地详情和列表。会话过期返回登录页。跨后台共用会话，退出后两个后台的后续受保护请求均需重新登录。

`RECHARGE_MODE=disabled` 时可查询历史记录，履约及对账按钮禁用；本次测试服务器保持此模式。`mock` 仅允许模拟订单操作；`live` 仅允许真实订单操作。启用真实履约仍需按 [人工配置手册](production-manual-configuration.md) 完成真实上游验收，不能仅改模式跳过验收。

## 4. 接口与数据边界

本节旧订单 API 使用现有管理员会话，写操作继续要求 JSON 与 `X-Requested-With: XMLHttpRequest`。CDK 具名会话与 `/api/cdk/*` 接口见 CDK 操作手册。

| 方法与路径 | 行为 |
| --- | --- |
| GET `/api/recharge/admin/overview` | 本地统计、模式与功能开关 |
| GET `/api/recharge/admin/tasks` | `q/status/source/page/page_size` 查询；每页 1–100 条 |
| GET `/api/recharge/admin/tasks/<task_no>` | 本地订单、上游绑定、当前操作、创建对账及最近 50 条操作对账记录 |
| POST `/api/recharge/admin/tasks/<task_no>/refresh` | 显式同步订单状态 |
| POST `/api/recharge/admin/tasks/<task_no>/recall` 或 `/close` | `confirmed: true`，复用原履约、幂等和并发约束 |
| POST `/api/recharge/admin/tasks/<task_no>/reconcile` | 复用创建 unknown 的证据对账接口 |
| POST `/api/recharge/admin/tasks/<task_no>/mutations/<operation_id>/reconcile` | 对指定未知操作提交未执行证据 |

旧协议充值前后台使用同一 `RechargeTask`、`RechargeOperation`、`RechargeMutation` 和对账审计表，单独拆分充值/邮箱入口时没有新增迁移。当前工作区的 CDK 版本另有 `20260925_08_cdk_catalog` 迁移及独立核销协调器，托管订单不从本节旧接口操作。已有非托管用户 API 保持兼容。账单、订阅等用户工具仍通过原会话凭据请求上游，本项目未保存这些客户凭据供管理员代操作。卡密发行见新工作台手册；现金支付、资金账本及退款未实现。

实现入口：`app/routes/main.py`、`app/routes/recharge.py`、`app/services/recharge_admin_service.py`、`frontend/src/App.jsx`、`frontend/src/RechargeAdminApp.jsx`。

## 5. 验证与回滚

本地构建：`npm --prefix frontend run build`。后端回归：`python -m unittest tests.test_recharge_admin tests.test_recharge_reconciliation tests.test_recharge_mutation_reconciliation tests.test_recharge_release_service tests.test_recharge_release_routes tests.test_admin_auth -q`。浏览器/API 回归：`node --test tests/api-service.test.mjs tests/frontend-ui.test.mjs tests/recharge-ui.test.mjs tests/recharge-flow.test.mjs`。

浏览器全链路使用临时 SQLite 和仅 loopback 的合成 HTTP 上游，验证用户创建→上游完成→管理员登录→查询同一订单→同步→退出；不是实际收费充值验收。在线部署结果与镜像身份记录在 [服务器验证记录](server-validation-2026-09-25.md)。

部署后逐项检查三个入口、登录/退出、匿名管理 API 401、充值禁用提示、空订单列表和邮箱账号库。已有真实记录时仅做只读查询；不要为展示数据而切换服务模式。

仅回退此前充值/邮箱入口拆分、且数据库尚无 CDK 发行或核销记录时，可在服务器受控 `.env` 中恢复部署前 `GOOGLE_MANAGER_IMAGE`，执行原 Compose `up -d --no-build --wait --wait-timeout 180`，验证 Web/worker healthy。更早版本的 `/admin` 会恢复邮箱管理、`/Googlemail` 不可用。当前 CDK 版本已包含 schema 变更；一旦发卡或履约，只能回退至理解 CDK 的兼容版本，须按 [CDK 操作手册第 8 节](cdk-implementation-and-operations-2026-09-25.md#8-运维密钥轮换与回滚) 保留在途对账，不能直接套用旧镜像回滚或覆盖数据库。服务器源文件和镜像引用备份位置见部署记录。
