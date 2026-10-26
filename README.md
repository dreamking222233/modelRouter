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
- 响应体与平台保持一致：`{"code": ..., "message": ..., "data": ...}`，平台的 HTTP 状态码和 `Retry-After` 头原样透传。

## 快速开始

```bash
# 1. 安装（推荐 uv，也可以用 pip）
uv venv .venv && uv pip install -p .venv/bin/python -e ".[dev]"
# 或：python -m venv .venv && .venv/bin/pip install -e ".[dev]"

# 2. 配置
cp .env.example .env   # 填入 PLATFORM_API_BASE、PLATFORM_AGENT_API_KEY、SERVICE_API_TOKENS

# 3. 启动
.venv/bin/uvicorn modelrouter.main:create_app --factory --host 0.0.0.0 --port 8000
```

启动后访问 `http://localhost:8000/docs` 可以看到 Swagger 文档，`GET /healthz` 为健康检查。

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
| `LOG_LEVEL` | 否 | `INFO` | 日志级别 |

\* 未配置 `SERVICE_API_TOKENS` 且未显式设置 `SERVICE_AUTH_DISABLED=true` 时服务拒绝启动。

平台 Key 需要的 scope：`auth.register`、`auth.login`、`user.recharge`、`user.api_key.create`、`agent.request_detail`、`agent.recharge_records`。只开通本服务实际用到的即可。

## 接口总览

所有 `/api/v1` 接口都要求请求头 `X-Service-Token: <SERVICE_API_TOKENS 中的任一值>`。
标注「用户 JWT」的接口还要求 `Authorization: Bearer <JWT>`，JWT 来自登录或注册接口的 `data.token`。

| 本服务 | 平台接口 | 用户 JWT | 说明 |
|---|---|:-:|---|
| `POST /api/v1/auth/register` | `POST /auth/register` | | 注册用户，返回 `data.token`、`data.user` |
| `POST /api/v1/auth/login` | `POST /auth/login` | | 登录，返回 `token`、`user`、`is_first_login`、`balance` |
| `GET /api/v1/user/api-keys/group-options` | `GET /user/api-keys/group-options` | ✓ | 模型分组选项，创建 Key 前先读它 |
| `POST /api/v1/user/api-keys` | `POST /user/api-keys` | ✓ | 创建用户模型 API Key，明文只返回一次 |
| `POST /api/v1/user/recharge-orders` | `POST /users/recharge-orders` | ✓ | 创建充值订单，须带 `Idempotency-Key` 头 |
| `POST /api/v1/user/recharge-orders/{order_no}/wait` | `POST /users/recharge-orders/{order_no}/wait` | ✓ | 同步等待支付结果，最长 120 秒 |
| `GET /api/v1/agent/requests/{request_id}` | `GET /agent/requests/{request_id}` | | 代理范围内的请求详情 |
| `GET /api/v1/agent/payments` | `GET /agent/payments` | | 代理充值记录，支持分页与筛选 |
| `GET /healthz` | | | 健康检查，无需令牌 |

请求字段与平台文档完全一致，本服务会在转发前先做一次校验（金额范围与小数位、
渠道与比例码枚举、`group_mode` 与分组字段的搭配、`Idempotency-Key` 格式、日期范围等），
不合法的请求直接返回 400/422，不会到达平台。

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

# 等待支付结果（调用方超时请设置为 160 秒以上）
curl -s --max-time 170 -X POST "$BASE/api/v1/user/recharge-orders/ORDER_NO/wait" \
  -H "X-Service-Token: $TOKEN" -H "Authorization: Bearer $USER_JWT"
```

## 错误约定

| HTTP | 来源 | 含义 |
|---|---|---|
| 400 / 422 | 本服务 | 请求头或字段校验失败，`data` 中给出字段位置 |
| 401 | 本服务 | 缺少或错误的 `X-Service-Token`，或用户级接口缺少 `Authorization` |
| 502 | 本服务 | 平台连不上，或返回了非 `{code,message,data}` 结构 |
| 504 | 本服务 | 平台响应超时 |
| 其他 | 平台 | 平台原始状态码与响应体，`429` / `409` 的 `Retry-After` 头一并透传 |

本服务不做自动重试：`429` 与「幂等请求处理中」的 `409` 都带 `Retry-After`，由 Agent 框架按该秒数重试，且创建订单的重试必须复用同一个 `Idempotency-Key`。

所有响应都带 `Cache-Control: no-store` 和 `X-Request-ID`（透传调用方的，没有则自动生成），日志里只记录方法、路径、状态码和耗时，不记录任何 Key、JWT 或请求体。

## 部署注意

- 等待支付接口会同步阻塞最长 150 秒（`PLATFORM_WAIT_TIMEOUT_SECONDS`）。本服务前面如有 Nginx 或网关，该路径的读取超时需设为 160 秒以上；Agent 框架调用本服务的超时也要大于这个值。
- uvicorn 不会因请求时长杀掉 worker；如果用 Gunicorn 管理进程，`--timeout` 同样要大于 160 秒。
- 平台对等待接口有每 worker 32 个并发的限制，超出返回 429，本服务原样透传。
- Docker：`docker build -t modelrouter .`，运行时通过 `-e` 或 `--env-file` 注入上述配置。

## 开发

```bash
.venv/bin/ruff check .   # 代码检查
.venv/bin/pytest         # 单元测试（用 httpx.MockTransport 模拟平台，不依赖网络）
```

目录结构：

```
src/modelrouter/
├── main.py              # 应用工厂、中间件、路由挂载
├── config.py            # 环境变量配置
├── security.py          # X-Service-Token 与用户 Bearer 校验
├── errors.py            # 统一错误响应
├── platform/client.py   # 平台 HTTP 客户端（注入代理 Key、解析响应）
└── api/
    ├── schemas.py       # 请求模型与本地校验
    ├── auth.py          # /api/v1/auth/*
    ├── user.py          # /api/v1/user/*
    └── agent.py         # /api/v1/agent/*
tests/                   # pytest
```

## 关于平台文档的几点说明

- 平台文档中模型 Key 接口用 `/user/...`，充值接口用 `/users/...`。本服务按文档原样调用，路径常量集中在 `src/modelrouter/platform/client.py` 顶部，平台修正后只需改那里。
- 平台的等待接口在 120 秒截止前会尝试关单，因此用户扫码后超过 120 秒才付款可能失败，这时需要用新的 `Idempotency-Key` 重新下单；`wait_result=wait_timeout` 且 `order_status=pending` 时可用同一订单号继续等待。
- 平台不提供非阻塞的单笔订单查询接口，查状态只能调等待接口。
