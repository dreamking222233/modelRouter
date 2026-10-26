"""导出 modelRouter 的 OpenAPI 文档到 docs/openapi.json，供非 Python 的 Agent 框架生成客户端。

用法：python scripts/export_openapi.py
"""

from __future__ import annotations

import json
from pathlib import Path

from modelrouter.config import Settings
from modelrouter.main import create_app

OUTPUT = Path(__file__).resolve().parent.parent / "docs" / "openapi.json"


def main() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        platform_api_base="https://platform.invalid",
        platform_agent_api_key="placeholder",
        service_auth_disabled=True,
        enable_docs=True,
    )
    app = create_app(settings)
    spec = app.openapi()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已写入 {OUTPUT}（{len(spec['paths'])} 个路径）")


if __name__ == "__main__":
    main()
