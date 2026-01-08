from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from envsync.core.config import EnvEntry
from envsync.utils.ssh import SSHClientWrapper


@dataclass
class EnvContext:
    name: str
    entry: EnvEntry
    _client: Optional[SSHClientWrapper] = field(default=None, repr=False, compare=False)

    @property
    def is_remote(self) -> bool:
        return self.entry.host not in (None, "", "localhost", "127.0.0.1")

    @property
    def client(self) -> SSHClientWrapper:
        """惰性创建并复用 SSH 客户端"""
        if self._client is None:
            object.__setattr__(self, '_client', SSHClientWrapper(host=self.entry.host, user=self.entry.user))
        return self._client

    @property
    def display(self) -> str:
        if self.is_remote:
            user = f"{self.entry.user}@" if self.entry.user else ""
            return f"{user}{self.entry.host}:{self.entry.path}"
        return self.entry.path

    def rsync_spec(self) -> str:
        """
        Rsync 路径表示，包含尾部斜杠表示“目录内容”。
        """
        path = self.entry.path.rstrip("/") + "/"
        if self.is_remote:
            user = f"{self.entry.user}@" if self.entry.user else ""
            return f"{user}{self.entry.host}:{path}"
        return path
