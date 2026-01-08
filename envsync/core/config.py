from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, List, Any

import yaml

from envsync.utils.crypto import SecretManager

DEFAULT_CONFIG_DIR = Path.home() / ".envsync"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.yaml"


@dataclass
class EnvEntry:
    name: str
    type: str  # docker | native
    path: str
    host: Optional[str] = None
    user: Optional[str] = None
    extras: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> List[str]:
        issues: List[str] = []
        if self.type not in ("docker", "native"):
            issues.append(f"{self.name}: type 必须为 docker 或 native")
        if not self.path:
            issues.append(f"{self.name}: path 不能为空")
        return issues

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "type": self.type,
            "path": self.path,
        }
        if self.host:
            data["host"] = self.host
        if self.user:
            data["user"] = self.user
        if self.extras:
            data.update(self.extras)
        return data

    @classmethod
    def from_dict(cls, name: str, data: Dict[str, Any]) -> "EnvEntry":
        return cls(
            name=name,
            type=data.get("type", "native"),
            path=data.get("path", ""),
            host=data.get("host"),
            user=data.get("user"),
            extras={
                k: v
                for k, v in data.items()
                if k
                not in {
                    "type",
                    "path",
                    "host",
                    "user",
                }
            },
        )


@dataclass
class GitLabConfig:
    url: str
    token: str
    project: Optional[str] = None
    _encrypted: bool = False

    def validate(self) -> List[str]:
        issues: List[str] = []
        if not self.url:
            issues.append("gitlab.url 不能为空")
        if not self.token:
            issues.append("gitlab.token 不能为空")
        return issues

    def to_dict(self, encrypt: bool = True) -> Dict[str, Any]:
        """序列化，支持加密 token"""
        token_value = self.token
        if encrypt and not self._encrypted:
            secret_mgr = SecretManager()
            token_value = secret_mgr.encrypt(self.token)
        data = {"url": self.url, "token": token_value}
        if self.project:
            data["project"] = self.project
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GitLabConfig":
        token = data.get("token", "")
        secret_mgr = SecretManager()
        # 自动检测并解密
        is_encrypted = secret_mgr.is_encrypted(token)
        if is_encrypted:
            token = secret_mgr.decrypt(token)
        return cls(
            url=data.get("url", ""),
            token=token,
            project=data.get("project"),
            _encrypted=is_encrypted,
        )


@dataclass
class ConfigData:
    envs: Dict[str, EnvEntry] = field(default_factory=dict)
    gitlab: Optional[GitLabConfig] = None

    def set_env(
        self,
        name: str,
        env_type: str,
        host: Optional[str],
        path: str,
        user: Optional[str],
        extras: Optional[Dict[str, Any]] = None,
    ):
        self.envs[name] = EnvEntry(
            name=name,
            type=env_type,
            host=host,
            path=path,
            user=user,
            extras=extras or {},
        )

    def set_gitlab(self, url: str, token: str, project: Optional[str]):
        self.gitlab = GitLabConfig(url=url, token=token, project=project)

    def validate(self) -> List[str]:
        issues: List[str] = []
        if not self.envs:
            issues.append("至少需要配置一个环境")
        for env in self.envs.values():
            issues.extend(env.validate())
        if self.gitlab:
            issues.extend(self.gitlab.validate())
        else:
            issues.append("未配置 GitLab 信息")
        return issues

    def to_dict(self, encrypt: bool = True) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "environments": {name: env.to_dict() for name, env in self.envs.items()},
        }
        if self.gitlab:
            data["gitlab"] = self.gitlab.to_dict(encrypt=encrypt)
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConfigData":
        envs_raw = data.get("environments", {})
        envs = {name: EnvEntry.from_dict(name, env_data) for name, env_data in envs_raw.items()}
        gitlab_data = data.get("gitlab")
        gitlab = GitLabConfig.from_dict(gitlab_data) if gitlab_data else None
        return cls(envs=envs, gitlab=gitlab)

    def pretty(self) -> str:
        return yaml.safe_dump(self.to_dict(), sort_keys=False, allow_unicode=True)


class ConfigService:
    def __init__(self, config_path: Path = DEFAULT_CONFIG_PATH):
        self.config_path = Path(config_path)

    def ensure_initialized(self):
        if self.config_path.exists():
            return
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        default_content = {
            "environments": {
                "local": {
                    "type": "native",
                    "path": "/path/to/your/project",
                    "host": "localhost",
                },
                "dev": {
                    "type": "native",
                    "path": "/data2/project",
                    "host": "dev.example.com",
                },
                "prod": {
                    "type": "native",
                    "path": "/data/project",
                    "host": "prod.example.com",
                },
            },
            "gitlab": {
                "url": "https://gitlab.company.com",
                "token": "<your-token>",
                "project": "group/repo",
            },
        }
        with self.config_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(default_content, f, sort_keys=False, allow_unicode=True)

    def load(self) -> ConfigData:
        self.ensure_initialized()
        with self.config_path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return ConfigData.from_dict(raw)

    def save(self, config: ConfigData, encrypt: bool = True):
        """保存配置，默认加密敏感信息"""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        # 备份旧配置
        if self.config_path.exists():
            import time
            backup = self.config_path.with_suffix(f".{time.strftime('%Y%m%d-%H%M%S')}.yaml")
            import shutil
            shutil.copy(self.config_path, backup)
        with self.config_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(config.to_dict(encrypt=encrypt), f, sort_keys=False, allow_unicode=True)
