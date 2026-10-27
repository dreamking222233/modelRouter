# modelRouter 项目总结与部署指南

> 更新日期：2026-10-27　　代码分支：`main`

## 1. 项目简介

### 1.1 背景与定位

平台（`https://api.xiaoleai.team`，即「代理开放接口」）把注册、登录、模型 Key 创建、充值和订单查询能力开放给代理，
但每个请求都必须携带固定的代理 API Key（`X-Agent-API-Key: agk_...`）。
这把 Key 代表整个代理的身份与分润，不能散落在 Agent 框架、桌面端或前端代码里。

modelRouter 就是为此而建的一层后端：

- **唯一持有** 代理 API Key，Agent 框架永远接触不到它；
- **封装** 平台 `/api/agent/open/v1` 下的全部接口，对外暴露自己的 `/api/v1` 接口；
- **收敛风险**：请求本地校验、响应字段白名单、并发保护、错误码统一；
- **补齐能力**：平台没有的非阻塞查单、就绪探测、Python SDK。

### 1.2 整体架构

```
终端用户 ──▶ Agent 框架 ──X-Service-Token──▶ modelRouter ──X-Agent-API-Key──▶ 平台开放接口
                            │                                   │
                            └── 用户级操作再带 Authorization: Bearer <用户 JWT>，原样转发 ──┘
```

- modelRouter 无状态，不落库，不缓存用户 JWT、订单或余额，可水平扩展。
- Agent 框架是唯一的直接调用方，用 `X-Service-Token` 鉴权；终端用户不应直接访问 modelRouter。
- 用户 JWT 由平台签发（实测有效期 24 小时），由 Agent 框架按用户保存。

### 1.3 技术栈

| 组件 | 选型 |
|---|---|
| 语言 | Python 3.11 / 3.12 / 3.13 |
| Web 框架 | FastAPI + uvicorn |
| HTTP 客户端 | httpx（异步） |
| 配置 | pydantic-settings，环境变量或 `.env` |
| 测试 | pytest，`httpx.MockTransport` 模拟平台，共 70 个用例 |
| 代码检查 | ruff |
| CI | GitHub Actions，三个 Python 版本上跑 lint + 测试 |
| 交付 | Dockerfile；客户端 SDK `client/` 独立安装 |

## 2. 功能清单

### 2.1 对外接口

所有 `/api/v1` 接口需要请求头 `X-Service-Token`。标「✓」的接口还需要 `Authorization: Bearer <用户 JWT>`。

| 本服务接口 | 对应平台接口 | 用户 JWT | 说明 |
|---|---|:-:|---|
| `POST /api/v1/auth/register` | `POST /auth/register` | | 注册，返回 `token`、`user` |
| `POST /api/v1/auth/login` | `POST /auth/login` | | 登录，返回 `token`、`user`、`is_first_login`、`balance` |
| `GET /api/v1/user/api-keys/group-options` | `GET /user/api-keys/group-options` | ✓ | 分组选项，已裁掉内部统计字段 |
| `POST /api/v1/user/api-keys` | `POST /user/api-keys` | ✓ | 创建模型 API Key，明文只返回一次 |
| `POST /api/v1/user/recharge-orders` | `POST /users/recharge-orders` | ✓ | 创建充值订单，须带 `Idempotency-Key` |
| `GET /api/v1/user/recharge-orders/{order_no}` | `GET /agent/payments`（按用户筛选） | ✓ | **本服务新增** 非阻塞查单 |
| `POST /api/v1/user/recharge-orders/{order_no}/wait` | `POST /users/recharge-orders/{order_no}/wait` | ✓ | 同步等待支付，最长 120 秒 |
| `GET /api/v1/agent/requests/{request_id}` | `GET /agent/requests/{request_id}` | | 请求详情 |
| `GET /api/v1/agent/payments` | `GET /agent/payments` | | 充值记录，默认 `view=user` 裁掉经营字段 |
| `GET /healthz` | | | 存活检查，无需令牌 |
| `GET /readyz` | `GET /agent/payments?page_size=1` | | 就绪检查，平台不可达或 Key 无效返回 503 |

### 2.2 安全与加固

- 代理 Key 只存在于服务端环境变量和出站请求头，不出现在任何响应、错误与日志中。
- `X-Service-Token` 恒定时间比对，支持多个令牌并存以便轮换；未配置令牌且未显式关闭鉴权时拒绝启动。
- 请求转发前先做本地校验：金额范围与小数位、渠道与比例码枚举、`group_mode` 与分组字段搭配、`Idempotency-Key` 格式、日期范围、订单号与 UUID 格式。
- 响应字段白名单：分组选项去掉 `key_count`、`mapping_count` 等内部统计；充值记录默认去掉 `agent_name`、`source_host`、`agent_settlement_rate`、`agent_income_cny` 等代理经营数据。
- 平台调用失败只返回 `UPSTREAM_*` 错误码，上游路径只写日志。
- Swagger 文档默认关闭；所有响应带 `Cache-Control: no-store`、`X-Content-Type-Options: nosniff`；`X-Request-ID` 只接受安全字符。
- 日志只记录方法、路径、状态码、耗时与请求 ID，不记录任何 Key、JWT 或请求体。

### 2.3 等待支付保护

- 本进程并发槽位 `WAIT_MAX_CONCURRENCY`（默认 32，与平台每 worker 上限一致），满了直接返回 429 并带 `Retry-After`，不占用平台配额。
- 调用方中途断开时，取消对平台的等待并释放槽位。
- 等待接口使用独立的 150 秒超时（`PLATFORM_WAIT_TIMEOUT_SECONDS`），其余接口 15 秒。

### 2.4 错误码约定

响应体统一为 `{"code": ..., "message": ..., "data": ...}`。成功时 `code` 为数字 200，错误时为字符串。

| HTTP | `code` | 含义 |
|---|---|---|
| 400 | `IDEMPOTENCY_KEY_MISSING` / `IDEMPOTENCY_KEY_INVALID` / `INVALID_DATE_RANGE` | 请求头或参数不合法 |
| 401 | `SERVICE_TOKEN_MISSING` / `SERVICE_TOKEN_INVALID` | 服务令牌问题 |
| 401 | `USER_TOKEN_MISSING` / `USER_TOKEN_MALFORMED` / `USER_TOKEN_UNREADABLE` / `USER_TOKEN_EXPIRED` | 用户 JWT 问题 |
| 404 | `NOT_FOUND` / `ORDER_NOT_FOUND` | 路径或订单不存在 |
| 422 | `VALIDATION_ERROR` | 字段校验失败，`data` 列出字段位置 |
| 429 | `WAIT_CONCURRENCY_LIMIT` | 等待支付并发已满 |
| 499 | `CLIENT_DISCONNECTED` | 调用方断开（仅日志可见） |
| 500 | `INTERNAL_ERROR` | 内部错误 |
| 502 | `UPSTREAM_UNAVAILABLE` / `UPSTREAM_BAD_RESPONSE` | 平台不可达或响应异常 |
| 503 | `PLATFORM_UNAVAILABLE` | 仅 `/readyz` |
| 504 | `UPSTREAM_TIMEOUT` | 平台超时 |

平台自身的错误原样透传，实测包括 `OPEN_API_KEY_INVALID`（401）、`IDEMPOTENCY_CONFLICT`（409）、`REQUEST_LOG_NOT_FOUND`（404）。

## 3. 代码结构

```
modelRouter/
├── src/modelrouter/            # 后端服务
│   ├── main.py                 # 应用工厂、中间件、路由挂载、/healthz、/readyz
│   ├── config.py               # 环境变量配置（pydantic-settings）
│   ├── security.py             # 服务令牌、用户 Bearer 校验、JWT 声明读取（不验签）
│   ├── errors.py               # 统一错误响应与错误码
│   ├── concurrency.py          # 等待支付的并发槽位与断连取消
│   ├── readiness.py            # 平台就绪探测（带缓存）
│   ├── platform/client.py      # 平台 HTTP 客户端：注入代理 Key、解析响应、路径常量
│   └── api/
│       ├── schemas.py          # 请求模型与本地校验
│       ├── projections.py      # 响应字段白名单
│       ├── auth.py / user.py / agent.py
│       └── deps.py             # 依赖注入与透传辅助
├── client/                     # Python SDK：modelrouter-client（只依赖 httpx）
├── examples/agent_integration.py   # Agent 框架接入示例（服务类）
├── scripts/e2e_smoke.py        # 对真实平台的冒烟联调
├── scripts/export_openapi.py   # 导出 OpenAPI
├── docs/openapi.json           # 导出的 OpenAPI 3 文档
├── tests/                      # 70 个 pytest 用例
├── Dockerfile / .env.example / pyproject.toml
└── .github/workflows/ci.yml
```

## 4. 配置项

通过环境变量或项目根目录 `.env` 配置，大小写不敏感。

| 变量 | 必填 | 默认 | 说明 |
|---|:-:|---|---|
| `PLATFORM_API_BASE` | 是 | | 平台根地址，如 `https://api.xiaoleai.team`，不带 `/api/agent/open/v1` |
| `PLATFORM_AGENT_API_KEY` | 是 | | 平台代理控制台 `/agent/open-api` 创建的 Key |
| `PLATFORM_TIMEOUT_SECONDS` | 否 | `15` | 普通接口超时 |
| `PLATFORM_WAIT_TIMEOUT_SECONDS` | 否 | `150` | 等待支付接口超时，必须大于 120 |
| `PLATFORM_VERIFY_SSL` | 否 | `true` | 是否校验平台证书 |
| `SERVICE_API_TOKENS` | 是* | | Agent 框架调用本服务的令牌，逗号分隔可多个 |
| `SERVICE_AUTH_DISABLED` | 否 | `false` | 跳过令牌校验，仅限本地调试 |
| `ENABLE_DOCS` | 否 | `false` | 开放 `/docs` 与 `/openapi.json` |
| `WAIT_MAX_CONCURRENCY` | 否 | `32` | 本进程等待支付并发上限 |
| `WAIT_RETRY_AFTER_SECONDS` | 否 | `5` | 429 的 `Retry-After` 秒数 |
| `ORDER_LOOKUP_MAX_PAGES` | 否 | `5` | 查单最多翻阅的充值记录页数（每页 100） |
| `READINESS_CACHE_SECONDS` | 否 | `30` | `/readyz` 结果缓存秒数 |
| `LOG_LEVEL` | 否 | `INFO` | 日志级别 |

\* 未配置且未显式设置 `SERVICE_AUTH_DISABLED=true` 时拒绝启动。

平台 Key 需要的 scope：`auth.register`、`auth.login`、`user.recharge`、`user.api_key.create`、`agent.request_detail`、`agent.recharge_records`。查单接口与 `/readyz` 依赖 `agent.recharge_records`。

## 5. 部署

### 5.1 上线前准备

1. **换一把新的代理 Key。** 开发联调期间使用的 Key 已在聊天与测试环境中出现过，上线前在平台控制台撤销并新建，只勾选上表中的 scope。
2. **生成服务令牌。** 例如 `python -c "import secrets; print(secrets.token_urlsafe(32))"`，填入 `SERVICE_API_TOKENS`，同一个值配置到 Agent 框架。
3. **确认 `ENABLE_DOCS=false`、`SERVICE_AUTH_DISABLED=false`。**
4. **清理测试数据。** 平台上 `mr_` 开头的测试账号（`mr_test_*`、`mr_wait_*`、`mr_url_*`、`mr_jwt_*`、`mr_v2_*`）与其未支付的 1 元订单可在后台删除。

### 5.2 方式一：Docker（推荐）

```bash
# 构建
docker build -t modelrouter:latest .

# 准备配置文件（不要提交到仓库）
cp .env.example modelrouter.env && vi modelrouter.env

# 运行
docker run -d --name modelrouter \
  --restart unless-stopped \
  --env-file modelrouter.env \
  -p 127.0.0.1:8000:8000 \
  modelrouter:latest

# 验证
curl -s http://127.0.0.1:8000/healthz
curl -s http://127.0.0.1:8000/readyz      # 200 表示平台可达且 Key 有效
```

镜像默认命令已带 `--no-access-log --no-server-header`。
需要多进程时追加 `--workers N`，注意 `WAIT_MAX_CONCURRENCY` 按进程生效。

docker compose 写法：

```yaml
services:
  modelrouter:
    build: .
    image: modelrouter:latest
    restart: unless-stopped
    env_file: modelrouter.env
    ports:
      - "127.0.0.1:8000:8000"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')"]
      interval: 30s
      timeout: 5s
      retries: 3
```

### 5.3 方式二：裸机 + systemd

```bash
# 安装
git clone https://github.com/dreamking222233/modelRouter.git /opt/modelrouter
cd /opt/modelrouter
python3 -m venv .venv && .venv/bin/pip install .
cp .env.example .env && vi .env
```

`/etc/systemd/system/modelrouter.service`：

```ini
[Unit]
Description=modelRouter - agent open api gateway
After=network-online.target
Wants=network-online.target

[Service]
User=www-data
WorkingDirectory=/opt/modelrouter
EnvironmentFile=/opt/modelrouter/.env
ExecStart=/opt/modelrouter/.venv/bin/uvicorn modelrouter.main:create_app --factory \
  --host 127.0.0.1 --port 8000 --no-access-log --no-server-header
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload && systemctl enable --now modelrouter
journalctl -u modelrouter -f
```

### 5.4 前置 Nginx

等待支付接口会阻塞最长 150 秒，反向代理的读取超时必须大于它：

```nginx
server {
    listen 443 ssl;
    server_name modelrouter.example.com;
    # ssl_certificate ...; ssl_certificate_key ...;

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header   Host $host;
        proxy_set_header   X-Request-ID $request_id;
        proxy_read_timeout 180s;
        proxy_send_timeout 180s;
        proxy_buffering    off;
    }
}
```

调用链上的每一段超时都要递增：平台 120 秒 → modelRouter 150 秒 → Nginx 180 秒 → Agent 框架（SDK 默认 170 秒，经 Nginx 时建议 190 秒）。
如果用 Gunicorn 管理进程，`--timeout` 同样要大于 180 秒。

### 5.5 Kubernetes 探针

```yaml
livenessProbe:
  httpGet: { path: /healthz, port: 8000 }
  periodSeconds: 20
readinessProbe:
  httpGet: { path: /readyz, port: 8000 }
  periodSeconds: 30
  failureThreshold: 3
```

`/readyz` 会向平台发一次真实请求，结果缓存 `READINESS_CACHE_SECONDS` 秒，探测频率不必太高。

### 5.6 网络与安全建议

- modelRouter 只对 Agent 框架所在网络开放，不要直接暴露到公网；如必须公网可达，至少限制来源 IP。
- 代理 Key 与服务令牌只放在环境变量或密钥管理系统里，不进镜像、不进仓库。
- 轮换服务令牌时先把新旧两个值都写入 `SERVICE_API_TOKENS`，Agent 框架切换完成后再移除旧值。
- 轮换代理 Key 需要重启服务（配置在启动时读取一次）。

## 6. Agent 框架接入

### 6.1 安装 SDK

```bash
pip install "modelrouter-client @ git+https://github.com/dreamking222233/modelRouter.git@main#subdirectory=client"
```

### 6.2 基本用法

```python
from modelrouter_client import ModelRouterClient, AuthenticationError, ConflictError

client = ModelRouterClient("https://modelrouter.example.com", service_token="<SERVICE_API_TOKENS 之一>")

auth = client.login("alice", "password")            # 失败抛 AuthenticationError
user = client.as_user(auth.token)                   # JWT 有效期 24 小时，按用户保存

key = user.create_api_key("AI 工作台", daily_limit_usd=20)   # key.key 明文只此一次
order = user.create_recharge_order(100, "wechat")   # order.payment_target：微信二维码 URL / 支付宝链接
```

异步环境使用 `AsyncModelRouterClient`，方法相同，全部 `await`。
`examples/agent_integration.py` 提供了一个可直接复制的服务类，封装登录或注册、创建 Key、发起充值、确认支付、查单。

### 6.3 推荐的充值流程

```
创建订单 ──▶ 展示二维码 / 收银台链接 ──▶ 用户确认已扫码 ──▶ 调用等待接口（≤120 秒）
                                                        ├─ paid            → 到账，result.balance 为新余额
                                                        ├─ closed          → 平台已关单，用新幂等键重新下单
                                                        ├─ can_wait_again  → 同一订单号再等一次
                                                        └─ 其他            → 失败，提示用户
任意时刻可用 user.get_recharge_order(order_no) 非阻塞查看状态
```

关键事实：平台在未支付时约 87 秒就会主动关单，因此等待接口要在用户确认扫码后再调，不要一生成二维码就调。

### 6.4 SDK 的内置行为

- 自动携带 `X-Service-Token`；`as_user()` 接受裸 JWT 或 `Bearer <JWT>`。
- 创建订单自动生成幂等键，`order.idempotency_key` 可复用；对 429 和带 `Retry-After` 的 409（处理中）自动重试，默认最多 2 次。
- 异常按状态码映射：`ValidationError`、`AuthenticationError`、`ForbiddenError`、`NotFoundError`、`ConflictError`、`RateLimitedError`、`ServiceUnavailableError`；网络问题抛 `TransportError`。`exc.code` 是字符串错误码，`exc.retry_after` 是建议重试秒数。
- 所有返回对象带 `raw` 原始字典。

### 6.5 非 Python 框架

`docs/openapi.json` 是导出的 OpenAPI 3 文档，可用 openapi-generator 生成任意语言的客户端。改动接口后运行 `python scripts/export_openapi.py` 重新导出。

## 7. 验证与运维

### 7.1 部署后验证

```bash
curl -s https://modelrouter.example.com/healthz          # {"code":200,...,"data":{"status":"ok"}}
curl -s https://modelrouter.example.com/readyz           # 200 正常；503 查日志，多为 Key 无效或网络不通
curl -s https://modelrouter.example.com/api/v1/agent/payments?page_size=1 \
  -H "X-Service-Token: $TOKEN"                           # 200 表示令牌与平台 scope 都正常
```

完整链路可用冒烟脚本（会在平台真实创建测试用户与 1 元未支付订单，只在联调环境使用）：

```bash
python scripts/e2e_smoke.py --base https://modelrouter.example.com --token $TOKEN
```

### 7.2 日志

每个请求一行：`METHOD PATH -> STATUS (耗时 ms) rid=<请求 ID>`；每次平台调用一行：`platform METHOD PATH -> STATUS (耗时 ms)`。
排查问题时用 `rid` 串联；Agent 框架可自带 `X-Request-ID` 以便两边对照。

### 7.3 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| `/readyz` 503，日志有「平台返回 401」 | 代理 Key 无效或已撤销 | 更换 `PLATFORM_AGENT_API_KEY` 并重启 |
| 接口返回 403 | 平台 Key 缺少对应 scope，或代理功能开关未开 | 在平台控制台补 scope / 开开关 |
| 等待接口 504 或 Nginx 504 | 某段超时小于下游 | 按 5.4 的链路逐级调大 |
| 大量 429 `WAIT_CONCURRENCY_LIMIT` | 同时等待的订单超过进程上限 | 加 worker 或实例；确认框架没有对同一订单重复等待 |
| 409 `IDEMPOTENCY_CONFLICT` | 同一幂等键用了不同参数 | 新业务请求必须换新幂等键 |
| 查单 404 但订单存在 | 订单太旧超出 `ORDER_LOOKUP_MAX_PAGES` | 调大页数，或直接用充值记录接口按 `user_id` 查 |

### 7.4 开发与测试

```bash
uv venv .venv && uv pip install -p .venv/bin/python -e ".[dev]" -e ./client
.venv/bin/ruff check .
.venv/bin/pytest            # 70 个用例，不依赖网络
```

推送到 GitHub 后 CI 自动在 Python 3.11、3.12、3.13 上执行同样的检查。

## 8. 已知限制与平台注意事项

- **等待接口会主动关单。** 未支付时约 87 秒返回 `closed`，远早于订单 30 分钟有效期；流程设计见 6.3。
- **平台错误码是字符串、成功码是数字。** 判断结果以 HTTP 状态码为准。
- **支付宝链接带平台域名。** `pay_url` 中的 `return_url` 指向代理前台域名、`notify_url` 指向平台 API 域名，支付完成后浏览器会跳回代理前台页面；这是支付流程本身决定的，本服务无法隐藏。
- **用户 JWT 可被解码。** payload 含 `sub`、`exp`、`iat`、`jti`，不含敏感信息，但不建议下发给终端用户。
- **查单接口不验签。** 它用 JWT 里的 `sub` 做筛选，签名只有平台能验，因此只允许受信任的 Agent 框架后端调用。
- **平台路径单复数不一致**（`/user/api-keys` 与 `/users/recharge-orders`）。本服务按文档原样调用，常量集中在 `src/modelrouter/platform/client.py`，平台修正后只改那里。
- **本服务不做自动重试。** 429 / 409 的 `Retry-After` 透传给调用方，由 SDK 或 Agent 框架决定重试。
