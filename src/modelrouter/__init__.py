"""modelRouter：代理开放接口的后端封装服务。

本服务持有固定的代理 API Key，封装平台 `/api/agent/open/v1` 下的接口，
并对 Agent 框架暴露自己的 `/api/v1` 接口，调用方无需接触平台 Key。
"""

__version__ = "0.1.0"
