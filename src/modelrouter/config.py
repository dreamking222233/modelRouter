from __future__ import annotations

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 平台开放接口统一前缀
OPEN_API_PREFIX = "/api/agent/open/v1"


class Settings(BaseSettings):
    """全部配置来自环境变量（或 .env 文件），字段名即变量名（大小写不敏感）。"""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ---- 平台 ----
    platform_api_base: str = Field(description="平台根地址，例如 https://api.example.com")
    platform_agent_api_key: str = Field(min_length=1, description="代理开放接口 API Key，agk_ 开头")
    platform_timeout_seconds: float = Field(default=15.0, gt=0, description="普通接口超时（秒）")
    platform_wait_timeout_seconds: float = Field(
        default=150.0, gt=120, description="等待支付接口超时（秒），必须大于平台的 120 秒"
    )
    platform_verify_ssl: bool = True

    # ---- 本服务 ----
    service_api_tokens: str = Field(default="", description="逗号分隔的 X-Service-Token 列表")
    service_auth_disabled: bool = Field(default=False, description="仅调试用：跳过令牌校验")
    enable_docs: bool = Field(default=False, description="是否开放 /docs 与 /openapi.json")
    log_level: str = "INFO"

    # ---- 等待支付与查单 ----
    wait_max_concurrency: int = Field(
        default=32, ge=1, description="本进程同时进行的等待支付请求上限，超出直接返回 429"
    )
    wait_retry_after_seconds: int = Field(default=5, ge=1, description="429 时建议的重试间隔")
    order_lookup_max_pages: int = Field(
        default=5, ge=1, le=50, description="按订单号查单时最多翻阅的充值记录页数（每页 100）"
    )

    # ---- 就绪探测 ----
    readiness_cache_seconds: float = Field(
        default=30.0, ge=0, description="/readyz 探测结果缓存秒数"
    )

    @property
    def platform_open_api_base(self) -> str:
        return self.platform_api_base.rstrip("/") + OPEN_API_PREFIX

    @property
    def service_token_list(self) -> list[str]:
        return [token.strip() for token in self.service_api_tokens.split(",") if token.strip()]

    @model_validator(mode="after")
    def _validate(self) -> Settings:
        if not self.platform_api_base.startswith(("http://", "https://")):
            raise ValueError("PLATFORM_API_BASE 必须以 http:// 或 https:// 开头")
        if not self.service_auth_disabled and not self.service_token_list:
            raise ValueError("必须配置 SERVICE_API_TOKENS，或显式设置 SERVICE_AUTH_DISABLED=true")
        return self
