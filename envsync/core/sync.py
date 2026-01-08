from __future__ import annotations

import subprocess
from typing import Literal, Optional

from envsync.core.config import ConfigData
from envsync.core.rsync_config import build_rsync_args
from envsync.utils.envs import EnvContext
from envsync.utils.git import GitRepo
from envsync.utils.logger import get_logger

log = get_logger(__name__)


class SyncService:
    """
    使用 rsync 进行文件同步，可跨本地/SSH。
    - safe 策略：若目标存在未提交变更则中止；使用 --checksum 确保内容一致
    - force 策略：直接覆盖（仍保留 --delete）
    """

    def __init__(self, config: ConfigData):
        self.config = config

    def sync(self, source: str, target: str, strategy: Literal["safe", "force"] = "safe"):
        ctx_src, ctx_dst = self._ctx(source), self._ctx(target)
        log.info("开始同步: %s -> %s (strategy=%s)", ctx_src.display, ctx_dst.display, strategy)
        if strategy == "safe":
            self._ensure_clean_target(ctx_dst)
        self._rsync_copy(ctx_src, ctx_dst, checksum=strategy == "safe")
        log.info("同步完成: %s -> %s", ctx_src.display, ctx_dst.display)

    def _ctx(self, env: str) -> EnvContext:
        if env not in self.config.envs:
            raise ValueError(f"环境未配置: {env}")
        return EnvContext(name=env, entry=self.config.envs[env])

    def _ensure_clean_target(self, ctx: EnvContext):
        repo = self._git_repo(ctx)
        if not repo:
            log.info("目标 %s 非 git 仓库，跳过脏检查（safe 模式）", ctx.display)
            return
        status = repo.status()
        if status.dirty:
            details = "\n".join(status.short_status[:20])
            raise RuntimeError(f"目标 {ctx.display} 有未提交变更，已中止同步。\n{details}")

    def _git_repo(self, ctx: EnvContext) -> Optional[GitRepo]:
        try:
            repo = GitRepo(ctx)
            repo.ensure_repo()
            return repo
        except Exception:
            return None

    def _rsync_copy(self, src: EnvContext, dst: EnvContext, checksum: bool = False):
        args = build_rsync_args(
            dry_run=False,
            checksum=checksum,
            delete=True,
            progress=True,
            itemize=True,
        )
        args.extend([src.rsync_spec(), dst.rsync_spec()])
        proc = subprocess.run(args, capture_output=True, text=True)
        if proc.returncode not in (0, 23):
            raise RuntimeError(f"rsync 失败: {proc.stderr or proc.stdout}")
        log.info(proc.stdout.strip() or "rsync 完成")
