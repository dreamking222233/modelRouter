# modelRouter

平台「代理开放接口」的后端封装服务。

平台的每个接口都要求携带固定的代理 API Key（`X-Agent-API-Key: agk_...`）。
把这个 Key 直接放进 Agent 框架既不安全也不好轮换，所以由本服务统一持有 Key，
封装平台接口，再对 Agent 框架暴露一组自己的接口。

```
Agent 框架  ──X-Service-Token──▶  modelRouter  ──X-Agent-API-Key──▶  平台 /api/agent/open/v1
            （用户级接口再带 Authorization: Bearer <用户 JWT>，原样转发）
```

- 技术栈：Python 3.11+、FastAPI、httpx（异步）、pydantic-settings。
- 本服务无状态，不存储用户 JWT、订单或任何业务数据。
- 响应体与平台保持一致：`{"code": ..., "message": ..., "data": ...}`。成功时 `code` 为 200，错误时 `code` 为字符串错误码；平台的 HTTP 状态码和 `Retry-After` 头原样透传。

## 快速开始

```bash
# 1. 安装（推荐 uv，也可以用 pip）
uv venv .venv && uv pip install -p .venv/bin/python -e ".[dev]"
# 或：python -m venv .venv && .venv/bin/pip install -e ".[dev]"

# 2. 配置
cp .env.example .env   # 填入 PLATFORM_API_BASE、PLATFORM_AGENT_API_KEY、SERVICE_API_TOKENS

# 3. 启动
.venv/bin/uvicorn modelrouter.main:create_app --factory \
  --host 0.0.0.0 --port 8000 --no-access-log --no-server-header
```

`GET /healthz` 为存活检查，`GET /readyz` 会探测平台可达且 Key 有效。
Swagger 文档默认关闭，本地调试时设置 `ENABLE_DOCS=true` 后访问 `http://localhost:8000/docs`。

### 配置项

全部通过环境变量（或项目根目录的 `.env`）配置：

| 变量 | 必填 | 默认 | 说明 |
|---|:-:|---|---|
| `PLATFORM_API_BASE` | 是 | | 平台根地址，例如 `https://api.example.com`，不要带 `/api/agent/open/v1` |
| `PLATFORM_AGENT_API_KEY` | 是 | | 平台代理控制台 `/agent/open-api` 创建的 Key |
| `PLATFORM_TIMEOUT_SECONDS` | 否 | `15` | 普通接口超时（秒） |
| `PLATFORM_WAIT_TIMEOUT_SECONDS` | 否 | `150` | 等待支付接口超时（秒），必须大于平台的 120 秒 |
| `PLATFORM_VERIFY_SSL` | 否 | `true` | 是否校验平台证书 |
| `SERVICE_API_TOKENS` | 是* | | Agent 框架调用本服务的令牌，逗号分隔可配多个，便于轮换 |
| `SERVICE_AUTH_DISABLED` | 否 | `false` | 设为 `true` 时跳过令牌校验，仅限本地调试 |
| `ENABLE_DOCS` | 否 | `false` | 是否开放 `/docs` 与 `/openapi.json`，生产环境保持关闭 |
| `WAIT_MAX_CONCURRENCY` | 否 | `32` | 本进程同时进行的等待支付请求上限，超出直接返回 429 |
| `WAIT_RETRY_AFTER_SECONDS` | 否 | `5` | 429 响应里的 `Retry-After` 秒数 |
| `ORDER_LOOKUP_MAX_PAGES` | 否 | `5` | 按订单号查单时最多翻阅的充值记录页数（每页 100 条） |
| `READINESS_CACHE_SECONDS` | 否 | `30` | `/readyz` 探测结果缓存秒数 |
| `LOG_LEVEL` | 否 | `INFO` | 日志级别 |

\* 未配置 `SERVICE_API_TOKENS` 且未显式设置 `SERVICE_AUTH_DISABLED=true` 时服务拒绝启动。

平台 Key 需要的 scope：`auth.register`、`auth.login`、`user.recharge`、`user.api_key.create`、`agent.request_detail`、`agent.recharge_records`。查单接口和 `/readyz` 依赖 `agent.recharge_records`。

## 接口总览

所有 `/api/v1` 接口都要求请求头 `X-Service-Token: <SERVICE_API_TOKENS 中的任一值>`。
标注「用户 JWT」的接口还要求 `Authorization: Bearer <JWT>`，JWT 来自登录或注册接口的 `data.token`，实测有效期 24 小时。

| 本服务 | 平台接口 | 用户 JWT | 说明 |
|---|---|:-:|---|
| `POST /api/v1/auth/register` | `POST /auth/register` | | 注册用户，返回 `data.token`、`data.user` |
| `POST /api/v1/auth/login` | `POST /auth/login` | | 登录，返回 `token`、`user`、`is_first_login`、`balance` |
| `GET /api/v1/user/api-keys/group-options` | `GET /user/api-keys/group-options` | ✓ | 模型分组选项，已裁掉平台内部统计字段 |
| `POST /api/v1/user/api-keys` | `POST /user/api-keys` | ✓ | 创建用户模型 API Key，明文只返回一次 |
| `POST /api/v1/user/recharge-orders` | `POST /users/recharge-orders` | ✓ | 创建充值订单，须带 `Idempotency-Key` 头 |
| `GET /api/v1/user/recharge-orders/{order_no}` | `GET /agent/payments`（按用户筛选） | ✓ | 非阻塞查单，本服务补充的接口 |
| `POST /api/v1/user/recharge-orders/{order_no}/wait` | `POST /users/recharge-orders/{order_no}/wait` | ✓ | 同步等待支付结果，最长 120 秒 |
| `GET /api/v1/agent/requests/{request_id}` | `GET /agent/requests/{request_id}` | | 代理范围内的请求详情 |
| `GET /api/v1/agent/payments` | `GET /agent/payments` | | 代理充值记录，默认 `view=user` 裁掉经营字段 |
| `GET /healthz` | | | 存活检查，无需令牌 |
| `GET /readyz` | `GET /agent/payments?page_size=1` | | 就绪检查，平台不可达或 Key 无效时返回 503 |

请求字段与平台文档完全一致，本服务会在转发前先做一次校验（金额范围与小数位、
渠道与比例码枚举、`group_mode` 与分组字段的搭配、`Idempotency-Key` 格式、日期范围等），
不合法的请求直接返回 400/422，不会到达平台。

### 响应字段裁剪

平台会把一些对终端用户没有意义、甚至不该让他们看到的字段一并返回。本服务对两个接口做了白名单裁剪：

- **分组选项**：每个分组只保留 `id`、`model_series`、`model_series_label`、`code`、`name`、`multiplier`、`enabled`、`is_default`、`sort_order`、`description`，去掉 `key_count`、`mapping_count`、时间戳等内部统计。
- **充值记录**（含查单接口）：默认只保留订单号、金额、到账、用户比例、状态、渠道、时间等用户可见字段，去掉 `agent_name`、`source_host`、`agent_settlement_rate`、`agent_income_cny` 等代理经营数据。`GET /api/v1/agent/payments?view=agent` 可拿到完整记录，仅供内部管理使用，不要原样下发给终端用户。

白名单只作用于成功响应，错误响应原样透传。

### 查单接口

平台没有非阻塞的单笔订单查询，本服务用代理充值记录补了一个：从用户 JWT 的 `sub` 读取用户 ID，
以 `user_id` 筛选充值记录后按订单号匹配，最多翻 `ORDER_LOOKUP_MAX_PAGES` 页，找不到返回 404 `ORDER_NOT_FOUND`。
JWT 已过期返回 401 `USER_TOKEN_EXPIRED`。本服务不验证 JWT 签名（只有平台能验），所以这个接口的调用方是受信任的 Agent 框架，不要把它直接暴露给终端用户。

### 调用示例

```bash
BASE=http://localhost:8000
TOKEN=change-me-to-a-long-random-string

# 登录，拿到用户 JWT
curl -s -X POST "$BASE/api/v1/auth/login" \
  -H "X-Service-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{"username":"alice","password":"password"}'

# 创建充值订单（Idempotency-Key 重试时必须复用）
curl -s -X POST "$BASE/api/v1/user/recharge-orders" \
  -H "X-Service-Token: $TOKEN" -H "Authorization: Bearer $USER_JWT" \
  -H "Idempotency-Key: desktop-order-20261026-0001" -H "Content-Type: application/json" \
  -d '{"amount_cny":100,"payment_channel":"wechat","recharge_rate_code":"e"}'

# 非阻塞查单
curl -s "$BASE/api/v1/user/recharge-orders/ORDER_NO" \
  -H "X-Service-Token: $TOKEN" -H "Authorization: Bearer $USER_JWT"

# 等待支付结果（调用方超时请设置为 160 秒以上）
curl -s --max-time 170 -X POST "$BASE/api/v1/user/recharge-orders/ORDER_NO/wait" \
  -H "X-Service-Token: $TOKEN" -H "Authorization: Bearer $USER_JWT"
```

## 错误约定

错误响应的 `code` 是字符串，可直接用于程序判断。本服务自身的错误码：

| HTTP | `code` | 含义 |
|---|---|---|
| 400 | `IDEMPOTENCY_KEY_MISSING` / `IDEMPOTENCY_KEY_INVALID` | 缺少或格式错误的 `Idempotency-Key` |
| 400 | `INVALID_DATE_RANGE` | `start_date` 晚于 `end_date` |
| 401 | `SERVICE_TOKEN_MISSING` / `SERVICE_TOKEN_INVALID` | `X-Service-Token` 缺失或错误 |
| 401 | `USER_TOKEN_MISSING` / `USER_TOKEN_MALFORMED` | 用户级接口缺少或格式错误的 `Authorization` |
| 401 | `USER_TOKEN_UNREADABLE` / `USER_TOKEN_EXPIRED` | 查单接口无法读取 JWT 用户 ID，或 JWT 已过期 |
| 404 | `NOT_FOUND` / `ORDER_NOT_FOUND` | 路径不存在，或订单不存在 |
| 405 | `METHOD_NOT_ALLOWED` | 方法不允许 |
| 422 | `VALIDATION_ERROR` | 请求字段校验失败，`data` 中给出字段位置 |
| 429 | `WAIT_CONCURRENCY_LIMIT` | 本进程等待支付并发已满，带 `Retry-After` |
| 499 | `CLIENT_DISCONNECTED` | 调用方在等待支付期间断开，本服务已取消对平台的等待 |
| 500 | `INTERNAL_ERROR` | 内部错误 |
| 502 | `UPSTREAM_UNAVAILABLE` / `UPSTREAM_BAD_RESPONSE` | 平台连不上，或返回了非 `{code,message,data}` 结构 |
| 503 | `PLATFORM_UNAVAILABLE` | 仅 `/readyz`：平台不可达或 Key 无效 |
| 504 | `UPSTREAM_TIMEOUT` | 平台响应超时 |

其他状态码和错误码来自平台，原样透传，实测包括 `OPEN_API_KEY_INVALID`（401）、`IDEMPOTENCY_CONFLICT`（409）、`REQUEST_LOG_NOT_FOUND`（404）。
本服务不做自动重试：`429` 与「幂等请求处理中」的 `409` 都带 `Retry-After`，由 Agent 框架按该秒数重试，且创建订单的重试必须复用同一个 `Idempotency-Key`。

所有响应都带 `Cache-Control: no-store`、`X-Content-Type-Options: nosniff` 和 `X-Request-ID`。
调用方传入的 `X-Request-ID` 只接受 1–128 位的字母、数字、`.`、`_`、`-`，否则替换为服务端生成的值。
日志里只记录方法、路径、状态码和耗时，不记录任何 Key、JWT 或请求体；平台调用失败时的上游路径只写日志，不返回给调用方。

## 等待支付的正确用法

实测平台的等待接口在未支付时约 87 秒就会主动关单并返回 `wait_result=closed`，远早于订单 `expired_at` 的 30 分钟。
Agent 框架建议这样编排：

1. 创建订单，展示二维码或支付宝链接。
2. 等用户确认已扫码或已打开收银台后，再调用等待接口；不要一生成二维码就调用。
3. 收到 `paid` 即充值成功；收到 `closed` 则提示用户重新下单，并换一个新的 `Idempotency-Key`。
4. 只有 `wait_result=wait_timeout` 且 `order_status=pending` 时，才用同一订单号再次等待。
5. 需要在不阻塞的情况下确认订单状态时，用查单接口。

本服务对等待接口做了两层保护：本进程并发超过 `WAIT_MAX_CONCURRENCY` 时直接返回 429，不占平台配额；
调用方中途断开时取消对平台的等待并释放槽位。

## 部署注意

- 等待支付接口会同步阻塞最长 150 秒（`PLATFORM_WAIT_TIMEOUT_SECONDS`）。本服务前面如有 Nginx 或网关，该路径的读取超时需设为 160 秒以上；Agent 框架调用本服务的超时也要大于这个值。
- uvicorn 不会因请求时长杀掉 worker；如果用 Gunicorn 管理进程，`--timeout` 同样要大于 160 秒。
- 多 worker 部署时，`WAIT_MAX_CONCURRENCY` 按进程生效；平台侧也是每 worker 32 个。
- Kubernetes 等编排环境用 `/healthz` 做存活探针，`/readyz` 做就绪探针。`/readyz` 会发一次真实的平台请求，结果缓存 `READINESS_CACHE_SECONDS` 秒。
- Docker：`docker build -t modelrouter .`，运行时通过 `-e` 或 `--env-file` 注入上述配置。

## 开发

```bash
.venv/bin/ruff check .   # 代码检查
.venv/bin/pytest         # 单元测试（用 httpx.MockTransport 模拟平台，不依赖网络）
```

目录结构：

```
src/modelrouter/
├── main.py              # 应用工厂、中间件、路由挂载、/healthz、/readyz
├── config.py            # 环境变量配置
├── security.py          # X-Service-Token、用户 Bearer 校验、JWT 声明读取（不验签）
├── errors.py            # 统一错误响应与错误码
├── concurrency.py       # 等待支付的本地并发槽位与断连取消
├── readiness.py         # 平台就绪探测（带缓存）
├── platform/client.py   # 平台 HTTP 客户端（注入代理 Key、解析响应）
└── api/
    ├── schemas.py       # 请求模型与本地校验
    ├── projections.py   # 响应字段白名单
    ├── auth.py          # /api/v1/auth/*
    ├── user.py          # /api/v1/user/*
    └── agent.py         # /api/v1/agent/*
scripts/e2e_smoke.py     # 对真实平台的冒烟联调
tests/                   # pytest
```

### 对真实平台的冒烟联调

`scripts/e2e_smoke.py` 会通过本地运行的 modelRouter 依次调用全部接口，并验证幂等、404 和本地校验。
它会在平台上真实创建一个测试用户、一个模型 Key 和一笔 1 元未支付订单，只在联调环境使用：

```bash
.venv/bin/python scripts/e2e_smoke.py --base http://127.0.0.1:8000 --token <SERVICE_API_TOKENS 之一>
# 加 --wait 会调用等待支付接口，阻塞约 90–120 秒，平台会在此期间关闭未支付订单
```

## 关于平台文档的几点说明

- 平台文档中模型 Key 接口用 `/user/...`，充值接口用 `/users/...`。本服务按文档原样调用，路径常量集中在 `src/modelrouter/platform/client.py` 顶部，平台修正后只需改那里。
- 平台成功响应的 `code` 是数字 200，错误响应的 `code` 是字符串。Agent 框架判断结果请以 HTTP 状态码为准，不要假设 `code` 的类型。
- 支付宝 `pay_url` 中带有 `return_url`（代理前台域名）和 `notify_url`（平台 API 域名），持有链接的人能看到这两个域名，这是支付流程本身决定的，本服务无法隐藏。
- 用户 JWT 的 payload 可被任何持有者解码，包含 `sub`（用户 ID）、`exp`、`iat`、`jti`。如果不希望终端用户看到这些，JWT 应由 Agent 框架自己保管。
