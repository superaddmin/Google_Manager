# 充值中心上线前全链路审查（2026-09-21）

## 审查边界与放行结论

审查基线为 `9489530`，初始工作树干净。本次核查当前完整实现，并修复确认问题，不是仅审查某个历史提交的差异。需求来源为本次全链路审查要求；规范来源为全局 AGENTS.md、README、现有发布门禁和代码约定。

**本地回归通过不等于生产 live 放行。** 现有系统是 CDK 履约与订阅管理工作台，完整路径为：校验卡密、确认凭证与邮箱、签发挑战、创建履约任务、查询上游、更新本地任务与页面。仓库没有现金支付订单、支付渠道、充值金额输入、优惠赠送、钱包余额、支付签名回调或资金账本。不能宣称已验证“支付到账/余额增加”，也不应在本次审查中臆造这些业务。

真实上游授权 sandbox、上游鉴权/幂等保证、最终 Linux 制品、目标 TLS/反代配置及告警送达尚无本轮实测证据。启用 live 前仍需完成这些验收；未完成时保持 `RECHARGE_MODE=disabled`。

## 本轮发现与修复

以下问题均由源码和新增回归确认。位置使用稳定的文件路径与函数名；本轮没有修改数据库 schema、依赖版本或真实配置。

| 级别 | 问题、代码位置 | 复现条件与影响 | 修复及回归依据 |
| --- | --- | --- | --- |
| P1 | 批量查询绕过卡密限流，`app/services/request_security.py:79` | 传入 51 个相同卡密；旧逻辑因原始数组长度大于 50 跳过卡密桶，但业务去重后接受。跨 IP 可持续查询同一卡密。 | 按去重后的集合检查与计费；`test_recharge_release_routes.py::test_duplicate_padded_batch_still_consumes_per_card_rate_limit`，旧实现 200，新实现 429 且不调用业务查询。 |
| P1 | 上游缺少身份仍更新任务，`RechargeService._reconcile_task` | 已建立上游编号映射后收到仅含 `status:completed` 的响应，旧逻辑仍可完成本地任务并释放门禁。 | 必须匹配上游任务号，或可核验的本地任务号/卡密；冲突编号拒绝。新测试验证缺身份不更新，正确 client_task_no 的既有操作响应仍接受。 |
| P1 | 迟到异常回退已核对结果，`app/models/recharge_mutation.py:66` | 撤回请求超时前，另一查询已将 mutation 核对为 done；旧异常处理再次将其改回 unknown，阻塞后续任务。 | finish 使用操作号及允许来源状态的条件更新，done 不退回 unknown；`test_late_timeout_cannot_regress_reconciled_mutation_to_unknown`。 |
| P1 | 非布尔成功标识被当作成功，`RechargeService.validate_redeem_code/lookup_task/get_task_by_no/lookup_batch_tasks/billing_query` | 上游返回 `ok:1` 或 `ok:"true"`，旧 truthy 判断放行，与严格 JSON 布尔契约不一致。 | 所有上游成功条件使用 `is True`；新增参数化回归拒绝整数/字符串标识，保持本地状态。 |
| P1 | 低速响应占用后端线程，`RechargeService._read_upstream_json/_upstream_get/_upstream_post` | 上游连续发送少量数据，间隔小于 socket 空闲超时；旧单次 read 持续等待，浏览器超时无法终止后端查询。 | 连接建立后，状态行、响应头和分块正文共用 monotonic deadline；本地低速 HTTP 回归覆盖响应头与正文超时。DNS 仍由操作系统解析器控制，不宣称该 deadline 可硬中断 DNS。 |
| P2 | 模拟任务收据缺少所有权校验，`app/routes/recharge.py:413` | 另一个浏览器持有卡密即可下载任务收据，与任务变更的会话权限边界不一致；live 下载本来已禁用。 | 复用 RechargeTaskAccess，创建会话成功、陌生会话 403、管理员可兜底；新增真实 Flask 路由回归。 |
| P2 | 下载参数被忽略或伪造默认值，`app/routes/recharge.py:369`、`:507` | 同传两个标识静默择一；pdf 仍返回 txt；空 slug 返回默认描述；控制字符延迟到下载才拒绝，Content-Type 重复 charset。 | 拒绝冲突标识/不支持类型/空或控制字符 slug，明确 txt Content-Type；新增下载与描述接口回归。 |
| P2 | 重验失败保留旧有效卡密状态，`RechargeView.jsx::handleValidateCdk` | 同一卡密先验证成功，再次校验报错，页面仍显示有效并允许进入原套餐表单。 | 每次校验开始清空 cdkInfo 与续费选择；UI 红绿回归覆盖重验失败。后端仍重新校验，旧问题不等同绕过服务端授权。 |
| P2 | 账单查询失败保留旧订阅操作，`RechargeView.jsx::handleQueryBilling` | 同凭证先查成功，再返回 401/网络失败，旧订阅卡片仍显示，可误判当前续费状态。 | 开始查询即清空旧结果，保留变更到刷新完成期间的锁；UI 覆盖过期凭证、先变更再刷新以及刷新期间不可再次操作。 |
| P2 | 切换卡密保留上一笔成功摘要，`RechargeView.jsx` CDK onChange | 创建成功后改填另一张卡，旧“任务提交成功”仍显示，容易把摘要归到新卡。 | 清空 createdTask；新增 `editing the CDK clears the previous task success summary`，修复前明确失败，修复后通过。 |
| P2 | live 展示虚构的近 7 天统计，`RechargeService.get_avg_processing_time`、`RechargeView.jsx` | 固定样例被生产页面标记为近期实测平均耗时，误导履约预期。 | live 返回空列表且前端不请求/展示样例；mock 明确“沙箱参考耗时（非实时）”，空数据正常显示。 |
| P2 | 批量与长度边界不一致，`RechargeView.jsx::handleBatchLookup` | 51 行重复卡密被前端拒绝，但后端先去重后允许；输入提示 16–32 与后端 4–120 不符，畸形批量 data 会导致渲染异常。 | 前端去重保序后限制 50 个、逐项校验 4–120，结果必须数组；同步关键输入上限与提示，覆盖重复边界及错误状态。 |
| P3 | 非 JSON 媒体类型错误码不准确，`app/routes/recharge.py:125` | text/plain 写请求原先和坏 JSON 一起返回 400。 | 非 JSON 返回 415 unsupported_media_type；JSON 格式/对象错误维持 400；路由回归固定契约。 |

### 后台对账任务长期得不到处理（P1，已修复）

- 位置：`app/worker.py:165` 的 `maintenance`。
- 原因：按 `updated_at` 升序每轮只取 20 个任务，查询失败不会更新时间，下一轮仍重复选中同一批。
- 复现：创建 21 个待处理 live 任务，使前 20 个持续上游异常；连续两轮维护均不查询第 21 个。
- 影响：后续订单即使上游已经完成，本地仍长期停留在处理中；用户关闭页面后尤其依赖后台恢复。
- 修复：在现有持久化维护状态中保存任务游标，每批按主键循环遍历，保留 20 项上限、错误计数和模式/状态过滤。查询失败也推进遍历位置，不修改业务状态或伪造更新时间。
- 测试：`tests/test_recharge_maintenance.py` 覆盖失败、无状态变化、循环回到首批、同轮去重、终态未知操作、mock/disabled/空队列。修复前两项失败，修复后四项通过。

## 前后端契约核对

统一 JSON 成功结构为 `{success:true,data,message}`；失败结构为 `{success:false,data:null,message,error_code}`。前端请求保持同源 `/api`，不读取 Vite 注入的支付密钥或上游地址。二进制收据单独处理，不能当作 JSON。

| 环节 | 请求及字段 | 服务端约束与返回 | 核对依据 |
| --- | --- | --- | --- |
| 页面入口 | `/`、`/recharge`、`/recharge/` | 公共门户；`/admin` 与 `/admin/*` 为管理入口 | `frontend/src/App.jsx`、`app/routes/main.py` |
| 模式与功能开关 | `GET /api/recharge/config` | `data.mode` 与 `data.features`；disabled 不允许提交 | `get_config`、`RechargeService.get_features`、充值页元数据加载 |
| CDK 校验 | `POST /redeem-codes/validate`，`redeem_code:string` | 去首尾空白后 4–120 字符；套餐、状态、续费能力从上游白名单取得 | `validate_redeem_code`、`_public_validation_result` |
| 挑战令牌 | `POST /submission-challenges` | 卡密、凭证、套餐、续费布尔值与当前会话/模式绑定，300 秒，一次性消费 | `generate_challenge`、`OneTimeToken` |
| 创建履约任务 | `POST /tasks` | 包含 challenge、卡密、凭证、邮箱、套餐、续费标志与三个显式确认布尔值；成功 HTTP 201 | `validate_task_creation_contract`、`create_task`、`handleSubmitTask` |
| 按任务号查进度 | `GET /tasks/<task_no>` | 公共脱敏摘要；TK 标识不等于管理员权限 | `get_task_by_no`、`RechargeTask.to_public_dict` |
| 按卡密查进度 | `POST /tasks/lookup` | 完整卡密放 JSON body；未提交卡密可返回 idle | `lookup_task`、前端单项查询 |
| 批量查询 | `POST /tasks/lookup-batch`，`redeem_codes:string[]` | 去重后最多 50 个，保序；上游缺项、重复、错配拒绝 | `lookup_batch_tasks`、共享限流、批量查询 UI |
| 撤回/关闭 | `POST /tasks/recall`、`/tasks/close` | task_no、卡密、邮箱、confirmed=true；创建会话所有权或有效管理员会话 | `authorize_task_action`、`RechargeTaskAccess` |
| 账单查询 | `POST /billing/query`，`token_input:string` | 仅白名单订阅/账单字段；不计算余额，不据此确认付款 | `_public_billing_result`、账单查询 UI |
| 取消/恢复续费 | `POST /billing/cancel-subscription`、`/billing/resume-subscription` | 凭证与确认；持久化操作号、租约、未知结果核对；成功后重新查状态 | `RechargeBillingMutation`、`_billing_live_mutation` |
| 收据 | JSON POST 标识与 txt 类型 | live 禁止未验收下载；mock 文本只用于模拟，任务收据须通过所有权校验 | `download_invoice`、`billing_invoice_file` |
| 人工对账 | `POST /admin/tasks/<task_no>/reconcile` | 可吊销的管理员会话、明确证据与唯一证据指纹；不能匿名解除门禁 | `reconcile_unknown_task`、`RechargeReconciliation` |

路径表中省略的公共前缀均为 `/api/recharge`。错误码依据 `app/routes/recharge.py::error_response`：400 invalid_request、401 unauthorized、403 forbidden、404 not_found、409 conflict、413 request_entity_too_large、415 unsupported_media_type、429 rate_limited、500 internal_error、502 upstream_error、503 service_unavailable。普通业务契约拒绝仍使用 400，只有显式对账冲突等接口返回 409，不将所有重复请求描述成 409。前端本地超时通过异常对象的 408 status 表达，并非服务端返回了 HTTP 408。

## 状态、幂等与异常场景

| 场景 | 实现与验证结论 |
| --- | --- |
| 充值方式/套餐切换 | 只有 CDK 交付；套餐由验证结果决定；不支持续费的卡密禁用并清空续费选择。FINISHED 可无凭证，其他套餐受服务端契约限制。 |
| 金额、优惠、赠送、渠道、余额 | 未实现，不适用。账单 amount 为展示数据，不能作为可结算金额或余额依据；若新增资金能力，需要独立定义最小货币单位、币种、小数精度、资金流水与事务。 |
| 支付回调 | 未实现。当前完成状态来自后端对上游的主动查询及人工对账，前端 URL/浏览器回跳不能改变状态。 |
| 状态流转 | live 先持久化 pending，再根据可信上游变为 processing/completed 等；不确定结果进入 unknown，保留卡密活动门禁。终态为 completed、failed、recalled、closed，拒绝旧响应回退。 |
| 重复提交、并发 | 前端提交锁减少重复请求；后端 challenge 原子消费和数据库唯一 active_key 才是跨进程保证。上游使用本地任务号作为 idempotency_key，真实上游是否兑现此保证仍须验收。 |
| 超时、失败重试 | 创建请求不盲目重放；unknown 先查询或人工核对。撤回/关闭及账单变更保存操作号；租约恢复不能产生第二个业务意图。 |
| 查询轮询 | 单个活跃任务使用有界定时查询；终态停止；失败退避，权限失败或连续失败停止；切换页面/查询模式取消旧查询，旧响应不得覆盖新目标。 |
| 页面刷新 | 数据库任务不丢失；页面不存储原始凭证，刷新后通过任务号或卡密重新查询。创建会话 Cookie 保留时可继续操作；Cookie 丢失后不能凭任务号获得变更权限。 |
| 权限失效 | 客户门户不依赖管理员登录；撤回/关闭检查创建会话或有效管理员会话，清 Cookie/切浏览器/密钥轮换后的操作应失败关闭。账单凭证过期由上游错误返回，不伪装成已更新。 |
| 网络/接口异常 | JSON 与 blob 的网络及响应体读取都有前端超时和取消；业务 success=false、HTTP 错误、非 JSON 错误均转换为错误对象；后端异常日志仅记录异常类型。 |
| 空/非法数据 | 拒绝非对象 JSON、非法字段类型、未知套餐、非法状态；协议加载失败不能提交。批量缺项和错配不能按成功处理。 |
| 数据安全 | 原始凭证不写入 RechargeTask，也不存 localStorage；challenge 保存摘要。生产敏感列加密，匿名任务响应剔除卡密/邮箱；日志 URL 省略查询字符串。 |

证据入口包括 `tests/test_recharge.py`、`test_recharge_safety.py`、`test_shared_recharge_state.py`、`test_billing_idempotency.py`、`test_recharge_reconciliation.py`、`test_upstream_task_identity.py`、`test_recharge_task_access.py` 和 `tests/api-service.test.mjs`。这些测试使用合成凭证与隔离数据库，不执行真实支付。

## 配置、部署与可观测性

- 后端仅从部署环境取得 live 地址；应用启动和运行时校验 HTTPS、显式地址及主机白名单，禁止上游重定向。生产禁止 mock；本机 `.env` 只核对非敏感开关和配置存在性，当前是 development，未设置 RECHARGE_MODE/上游地址，实际默认为 disabled。
- 当前上游适配器请求头仅见 POST `Content-Type` 及通用 `User-Agent`，没有仓库已约定的 API Key、HMAC 或 mTLS 实现。由于缺少真实上游契约，本轮不凭空添加鉴权头；live 前必须在授权 sandbox 确认鉴权方式、签名范围、密钥轮换和凭证托管。
- 前端 `/api` 为相对同源地址；开发代理为 localhost:8002，保留原 Origin。生产依靠 Nginx 同域反代，不需要开放跨域 credentials。写请求要求 X-Requested-With、JSON 和可信 Origin/Fetch 来源。
- 生产 Cookie 为 HttpOnly、Secure、SameSite=Lax；反代协议/IP 只信任配置的 CIDR。TLS 或可信代理配错会导致 Secure Cookie 不回传、挑战与所有权失效，应在目标域名真实验收。
- API/健康响应 no-store；HTML 由 Flask 条件缓存/重新验证，哈希静态资源可长期缓存。生产 CSP 限制脚本、连接和表单同源。协议 HTML 经过组件白名单渲染。
- Vite 正式入口 `npm --prefix frontend run build`，输出 static；Docker 内用锁文件安装并构建。无独立 lint/format/typecheck 命令，不能编造通过记录；使用既有语法检查、生产构建和 git diff --check。
- worker 定期处理未结任务；维护失败计数、worker heartbeat 和 readiness 相互关联；monitor 检测超期 unknown、mutation、账单操作和队列。监控内容为数量与固定错误类别，不输出卡密或凭证。
- 未核验目标服务器容量、告警接收端、真实上游网络/TLS、生产数据库迁移或制品安全扫描；本次静态检查不覆盖这些部署事实。

## 本轮验证与复现

新增测试已接入 `.github/workflows/release-gate.yml`，使用正式 Vite static 产物，不需要增加依赖。全链路 fixture 从浏览器发真实 HTTP 到隔离 Flask，再由实际 urllib 适配器访问本机合成上游；使用临时 SQLite，结束关闭服务并删除该测试目录。

浏览器链路验证：进入页面 → 校验 CDK → 填凭证并确认 → 签发 challenge → HTTP 201 创建任务 → 查询 processing → 自动轮询 completed → 刷新页面后按任务号查到 completed。额外核验数据库仅有一笔任务、上游仅创建一次、幂等号与本地编号一致、challenge 不转发上游。375px 手机和 1280px 桌面没有横向溢出，按钮可见，没有未处理的页面异常。截图存于 `.test-tmp/recharge-flow-mobile.png` 与 `.test-tmp/recharge-flow-desktop.png`，均为合成数据，不是线上支付证据。

`tests/test_recharge_transport.py` 另使用临时自签证书与真实本机 HTTPS 服务验证三类场景：受信证书的 GET/POST 成功，未受信证书被拒绝，主机名不匹配被拒绝。实测中同步修正了 Python 3.14 兼容性：自定义 HTTPS handler 不再引用该版本已移除的 `_check_hostname`，仅传入 SSL context，证书链与主机名校验仍由 context/标准库执行。

验证环境为 Windows、Python 3.14.7、Node 22.23.2；CI/生产声明 Python 3.11、Node 24。Windows 通过不能替代 Linux/Python 3.11、固定生产 Chromium 与目标网络验收。全量测试仍有既有 datetime.utcnow 弃用与 SQLite 测试连接 ResourceWarning，未据此宣称生产无资源风险。

| 验证项 | 命令 | 结果 |
| --- | --- | --- |
| Python 全量测试 | `.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'` | 375 项：371 通过、4 项平台专属跳过，76.217 秒，exit 0 |
| 充值 UI 与真实 HTTP 链路 | `node --test tests/frontend-ui.test.mjs tests/recharge-ui.test.mjs tests/recharge-flow.test.mjs` | 53/53 通过，60.931 秒 |
| 共享 Node 回归 | `node --test tests/account-import.test.mjs tests/googlemail-local-copy.test.mjs tests/api-service.test.mjs` | 41/41 通过 |
| 前端生产构建 | `npm --prefix frontend run build` | 成功；产物包含 `static/assets/index-DzHbbLEl.js` 与 `static/assets/AdminApp-Br_i1S0u.js` |
| Python 语法/字节码 | `.\.venv\Scripts\python.exe -m compileall app deploy run.py` | 通过 |
| Node 测试语法 | 对本轮新增/修改的 `.mjs` 测试执行 `node --check` | 通过 |
| 依赖一致性 | `.\.venv\Scripts\python.exe -m pip check` | 通过，无损坏依赖 |
| CI 工作流 | `actionlint .github/workflows/release-gate.yml` | 通过 |
| 补丁格式 | `git diff --check` | 通过 |

仓库未定义独立 lint、format 或 typecheck 脚本，因此本报告不宣称这三项已执行。上述结果只证明隔离的 CDK 履约链路与当前回归集通过；在真实上游 sandbox、Linux 制品、目标 TLS/反代、容量与告警验收完成前，不给出生产 live 无条件放行结论。

独立代码复核未发现新的确定性 P0/P1/P2 问题，并确认状态行/响应头/正文共享 deadline、TLS 证书链与主机名校验仍启用，SSL context 调用方式符合 Python 3.11/3.12/3.14 标准接口。最终静态核验还确认本轮 20 个新增/修改文本文件为严格 UTF-8 无 BOM、LF 且无 U+FFFD，`static/index.html` 引用的哈希 JS/CSS 产物均存在。这些复核仍不替代 Linux 制品和真实上游实测。

## 回滚与发布注意

本轮不修改真实 `.env`、业务数据库或密钥，不进行提交、推送和部署。新增维护游标使用现有 RuntimeState 表，无 schema 迁移。回滚时应成套回退后端、前端与相应构建产物，并保留业务数据及未决操作记录；不能靠删除幂等锁或 unknown 记录恢复充值。真实上游未验收前维持 disabled。
