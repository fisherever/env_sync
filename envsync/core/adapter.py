from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Any

from jinja2 import Environment, FileSystemLoader

from envsync.core.config import ConfigData, EnvEntry
from envsync.utils.logger import get_logger

log = get_logger(__name__)


class AdapterService:
    """
    环境适配：将模板渲染为环境专属配置文件。
    用途示例：数据库连接、内外网差异、特性开关。
    """

    def __init__(self, config: ConfigData):
        self.config = config

    def render(self, env: str, template_path: str, output_path: Optional[str] = None, extra: Optional[Dict[str, Any]] = None) -> Path:
        ctx_entry = self._env(env)
        tpl_path = Path(template_path)
        out_path = Path(output_path) if output_path else tpl_path.with_suffix("")
        out_path.parent.mkdir(parents=True, exist_ok=True)

        jinja_env = Environment(loader=FileSystemLoader(str(tpl_path.parent)), autoescape=False)
        template = jinja_env.get_template(tpl_path.name)
        context = {
            "env_name": env,
            "env": ctx_entry.to_dict(),
            "type": ctx_entry.type,
            "path": ctx_entry.path,
            "host": ctx_entry.host,
            "user": ctx_entry.user,
        }
        if extra:
            context.update(extra)

        rendered = template.render(**context)
        out_path.write_text(rendered, encoding="utf-8")
        log.info("模板渲染完成: %s -> %s", tpl_path, out_path)
        return out_path

    def _env(self, name: str) -> EnvEntry:
        if name not in self.config.envs:
            raise ValueError(f"环境未配置: {name}")
        return self.config.envs[name]
