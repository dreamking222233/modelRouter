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
    log_level: str = "INFO"

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
