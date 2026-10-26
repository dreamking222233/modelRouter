# modelrouter-client

modelRouter 服务的 Python 客户端，只依赖 `httpx`，供 Agent 框架接入。

```bash
pip install "modelrouter-client @ git+https://github.com/dreamking222233/modelRouter.git@main#subdirectory=client"
```

```python
from modelrouter_client import ModelRouterClient

client = ModelRouterClient("http://modelrouter:8000", service_token="...")
auth = client.login("alice", "password")        # AuthResult(token, user, balance, ...)
user = client.as_user(auth.token)               # 绑定用户 JWT

key = user.create_api_key("AI 工作台")            # ApiKey(key="sk-...", ...)
order = user.create_recharge_order(100, "wechat")  # RechargeOrder(order_no, wechat_code_url, ...)
result = user.wait_for_payment(order.order_no)  # WaitResult(paid / closed / timed_out)
```

异步版本为 `AsyncModelRouterClient`，方法签名相同，全部 `await`。
完整说明见仓库根目录 README 的「接入 Agent 框架」一节。
