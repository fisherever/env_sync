from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from envsync.core.config import ConfigData
from envsync.utils.envs import EnvContext
from envsync.utils.logger import get_logger

log = get_logger(__name__)


class DependencyService:
    """
    依赖管理：Python pip 和 Node.js npm 的离线下载、缓存与传输。
    """

    def __init__(self, config: ConfigData):
        self.config = config
        self.cache_root = Path.home() / ".envsync" / "deps-cache"

    def download(self, env: str) -> Path:
        """在可联网环境下载依赖包"""
        ctx = self._ctx(env)
        log.info("开始下载 %s 的依赖包...", env)
        cache_dir = self.cache_root / env
        cache_dir.mkdir(parents=True, exist_ok=True)

        # 检测 Python 项目
        py_downloaded = self._download_python(ctx, cache_dir)
        # 检测 Node.js 项目
        node_downloaded = self._download_node(ctx, cache_dir)

        if not py_downloaded and not node_downloaded:
            log.warning("未检测到 requirements.txt 或 package.json，跳过")
        else:
            log.info("✓ %s 依赖已缓存到: %s", env, cache_dir)
        return cache_dir

    def transfer(self, source: str, target: str) -> Path:
        """从 source 环境的缓存传输到 target 环境"""
        src_cache = self.cache_root / source
        if not src_cache.exists():
            raise RuntimeError(f"{source} 缓存不存在，请先运行 envsync deps download {source}")

        dst_ctx = self._ctx(target)
        log.info("传输依赖缓存从 %s 到 %s...", source, target)

        # 在目标环境创建缓存目录
        remote_cache = f"{dst_ctx.entry.path}/.envsync-deps"
        dst_ctx.client.run(f"mkdir -p {remote_cache}")

        # 使用 rsync 传输
        dst_spec = dst_ctx.rsync_spec().replace(
            dst_ctx.entry.path.rstrip("/") + "/",
            remote_cache + "/"
        )
        cmd = [
            "rsync",
            "-az",
            "--info=progress2",
            f"{src_cache}/",
            dst_spec,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"rsync 传输失败: {proc.stderr}")

        log.info("✓ 依赖已传输到 %s:%s", target, remote_cache)
        return Path(remote_cache)

    def install(self, env: str, use_cache: bool = True):
        """在目标环境安装依赖（从缓存或联网）"""
        ctx = self._ctx(env)
        log.info("开始在 %s 安装依赖...", env)

        cache_dir = f"{ctx.entry.path}/.envsync-deps" if use_cache else None

        # Python
        self._install_python(ctx, cache_dir)
        # Node.js
        self._install_node(ctx, cache_dir)

        log.info("✓ %s 依赖安装完成", env)

    def _ctx(self, env: str) -> EnvContext:
        if env not in self.config.envs:
            raise ValueError(f"环境未配置: {env}")
        return EnvContext(name=env, entry=self.config.envs[env])

    def _download_python(self, ctx: EnvContext, cache_dir: Path) -> bool:
        """下载 Python 依赖到本地缓存"""
        req_check = ctx.client.run(f"[ -f {ctx.entry.path}/requirements.txt ] && echo exists")
        if "exists" not in req_check.stdout:
            return False

        log.info("  下载 Python 依赖...")
        py_cache = cache_dir / "python"
        py_cache.mkdir(parents=True, exist_ok=True)

        if ctx.is_remote:
            # 远程环境：先在远程下载，再 rsync 回本地
            remote_cache = f"/tmp/envsync-deps-{ctx.name}"
            ctx.client.run(f"mkdir -p {remote_cache}")
            cmd = f"pip download -r {ctx.entry.path}/requirements.txt -d {remote_cache}"
            result = ctx.client.run(cmd)
            if result.code != 0:
                log.warning("  pip download 失败: %s", result.stderr)
                return False
            # rsync 回本地
            rsync_cmd = [
                "rsync", "-az",
                f"{ctx.entry.user + '@' if ctx.entry.user else ''}{ctx.entry.host}:{remote_cache}/",
                f"{py_cache}/",
            ]
            proc = subprocess.run(rsync_cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                log.warning("  rsync 传输失败: %s", proc.stderr)
                return False
        else:
            # 本地环境：直接下载到缓存目录
            cmd = f"pip download -r {ctx.entry.path}/requirements.txt -d {py_cache}"
            result = ctx.client.run(cmd)
            if result.code != 0:
                log.warning("  pip download 失败: %s", result.stderr)
                return False

        log.info("  ✓ Python 依赖已下载")
        return True

    def _download_node(self, ctx: EnvContext, cache_dir: Path) -> bool:
        """下载 Node.js 依赖到本地缓存"""
        pkg_check = ctx.client.run(f"[ -f {ctx.entry.path}/package.json ] && echo exists")
        if "exists" not in pkg_check.stdout:
            return False

        log.info("  下载 Node.js 依赖...")
        node_cache = cache_dir / "node"
        node_cache.mkdir(parents=True, exist_ok=True)

        if ctx.is_remote:
            # 远程环境：在远程打包后 rsync 回本地
            remote_cache = f"/tmp/envsync-node-{ctx.name}"
            ctx.client.run(f"mkdir -p {remote_cache}")
            cmd = f"cd {ctx.entry.path} && npm pack --pack-destination {remote_cache}"
            result = ctx.client.run(cmd)
            if result.code != 0:
                log.warning("  npm pack 失败: %s", result.stderr)
                return False
            # rsync 回本地
            rsync_cmd = [
                "rsync", "-az",
                f"{ctx.entry.user + '@' if ctx.entry.user else ''}{ctx.entry.host}:{remote_cache}/",
                f"{node_cache}/",
            ]
            proc = subprocess.run(rsync_cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                log.warning("  rsync 传输失败: %s", proc.stderr)
                return False
        else:
            # 本地环境：直接打包到缓存目录
            cmd = f"cd {ctx.entry.path} && npm pack --pack-destination {node_cache}"
            result = ctx.client.run(cmd)
            if result.code != 0:
                log.warning("  npm pack 失败: %s", result.stderr)
                return False

        log.info("  ✓ Node.js 依赖已下载")
        return True

    def _install_python(self, ctx: EnvContext, cache_dir: Optional[str] = None):
        """安装 Python 依赖"""
        req_check = ctx.client.run(f"[ -f {ctx.entry.path}/requirements.txt ] && echo exists")
        if "exists" not in req_check.stdout:
            return

        log.info("  安装 Python 依赖...")
        if cache_dir:
            # 离线安装
            cmd = f"pip install --no-index --find-links={cache_dir}/python -r {ctx.entry.path}/requirements.txt"
        else:
            # 联网安装
            cmd = f"pip install -r {ctx.entry.path}/requirements.txt"

        result = ctx.client.run(cmd, cwd=ctx.entry.path)
        if result.code != 0:
            log.warning("  pip install 失败: %s", result.stderr)
        else:
            log.info("  ✓ Python 依赖安装完成")

    def _install_node(self, ctx: EnvContext, cache_dir: Optional[str] = None):
        """安装 Node.js 依赖"""
        pkg_check = ctx.client.run(f"[ -f {ctx.entry.path}/package.json ] && echo exists")
        if "exists" not in pkg_check.stdout:
            return

        log.info("  安装 Node.js 依赖...")
        if cache_dir:
            # 离线安装（使用 npm install --offline）
            cmd = f"npm install --offline --cache {cache_dir}/node"
        else:
            # 联网安装
            cmd = "npm install"

        result = ctx.client.run(cmd, cwd=ctx.entry.path)
        if result.code != 0:
            log.warning("  npm install 失败: %s", result.stderr)
        else:
            log.info("  ✓ Node.js 依赖安装完成")
