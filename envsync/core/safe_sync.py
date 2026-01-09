"""
高可靠同步服务：支持备份、回滚、校验、自动迁移
"""
from __future__ import annotations

import hashlib
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Literal

from envsync.core.config import ConfigData
from envsync.core.rsync_config import build_rsync_args, RSYNC_EXCLUDES
from envsync.utils.envs import EnvContext
from envsync.utils.git import GitRepo
from envsync.utils.logger import get_logger

# 延迟导入避免循环依赖
def _get_scanner():
    from envsync.core.scanner import ProjectScanner
    return ProjectScanner

log = get_logger(__name__)


@dataclass
class SyncCheckpoint:
    """同步检查点，用于回滚"""
    timestamp: str
    env_name: str
    backup_path: str
    git_commit: Optional[str] = None
    file_checksums: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "env_name": self.env_name,
            "backup_path": self.backup_path,
            "git_commit": self.git_commit,
            "file_checksums": self.file_checksums,
        }


@dataclass
class SyncResult:
    """同步结果"""
    success: bool
    source: str
    target: str
    checkpoint: Optional[SyncCheckpoint] = None
    files_synced: int = 0
    files_deleted: int = 0
    verified: bool = False
    code_only: bool = False
    components_synced: List[str] = field(default_factory=list)
    error: Optional[str] = None

    def summary(self) -> str:
        status = "✓ 成功" if self.success else "✗ 失败"
        lines = [
            f"{status}: {self.source} → {self.target}",
            f"  同步文件: {self.files_synced}, 删除文件: {self.files_deleted}",
            f"  校验通过: {'是' if self.verified else '否'}",
        ]
        if self.checkpoint:
            lines.append(f"  备份位置: {self.checkpoint.backup_path}")
        if self.error:
            lines.append(f"  错误: {self.error}")
        return "\n".join(lines)


class SafeSyncService:
    """
    高可靠同步服务
    
    特性：
    - 同步前自动备份目标环境
    - 同步后校验文件一致性
    - 支持快速回滚到检查点
    - 支持 Git 状态检查和自动提交
    """

    def __init__(self, config: ConfigData):
        self.config = config
        self.checkpoint_dir = Path.home() / ".envsync" / "checkpoints"
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

    def sync(
        self,
        source: str,
        target: str,
        *,
        strategy: Literal["safe", "force"] = "safe",
        backup: bool = True,
        verify: bool = True,
        auto_commit: bool = False,
        code_only: bool = False,
        components: Optional[List[str]] = None,
    ) -> SyncResult:
        """
        执行高可靠同步
        
        Args:
            source: 源环境名
            target: 目标环境名
            strategy: safe=检查未提交变更, force=强制覆盖
            backup: 是否在同步前备份目标
            code_only: 仅同步代码（自动扫描排除依赖/构建产物）
            components: 指定同步的组件类型（python, node 等）
            verify: 是否在同步后校验一致性
            auto_commit: 同步后是否自动 Git 提交
        """
        ctx_src = self._ctx(source)
        ctx_dst = self._ctx(target)
        log.info("开始安全同步: %s → %s", ctx_src.display, ctx_dst.display)

        checkpoint: Optional[SyncCheckpoint] = None
        extra_excludes: List[str] = []
        synced_components: List[str] = []

        try:
            # 0. 如果仅同步代码，先扫描项目结构
            if code_only:
                log.info("步骤 0: 扫描项目结构...")
                Scanner = _get_scanner()
                scanner = Scanner(self.config)
                src_structure = scanner.scan(source)
                extra_excludes = src_structure.get_rsync_excludes()
                synced_components = [c.type for c in src_structure.components]
                if components:
                    # 过滤指定组件
                    synced_components = [t for t in synced_components if t in components]
                log.info("  扫描完成: %d 组件, 排除 %d 目录",
                         len(src_structure.components), len(src_structure.all_non_code_dirs))

            # 1. 检查目标环境状态
            if strategy == "safe":
                self._check_clean_target(ctx_dst)

            # 2. 创建备份检查点
            if backup:
                log.info("步骤 1/4: 创建备份检查点...")
                checkpoint = self._create_checkpoint(ctx_dst)
                log.info("  备份完成: %s", checkpoint.backup_path)

            # 3. 执行同步
            log.info("步骤 2/4: 执行文件同步...")
            synced, deleted = self._do_sync(
                ctx_src, ctx_dst,
                checksum=strategy == "safe",
                extra_excludes=extra_excludes,
            )
            log.info("  同步完成: %d 文件, 删除 %d 文件", synced, deleted)

            # 4. 校验一致性
            verified = False
            if verify:
                log.info("步骤 3/4: 校验文件一致性...")
                verified = self._verify_sync(ctx_src, ctx_dst, extra_excludes=extra_excludes)
                if verified:
                    log.info("  ✓ 校验通过")
                else:
                    log.warning("  ✗ 校验失败，文件可能不一致")

            # 5. 自动提交
            if auto_commit:
                log.info("步骤 4/4: 自动提交变更...")
                self._auto_commit(ctx_dst, f"Synced from {source}")

            return SyncResult(
                success=True,
                source=source,
                target=target,
                checkpoint=checkpoint,
                files_synced=synced,
                files_deleted=deleted,
                verified=verified,
                code_only=code_only,
                components_synced=synced_components,
            )

        except Exception as e:
            log.error("同步失败: %s", str(e))
            return SyncResult(
                success=False,
                source=source,
                target=target,
                checkpoint=checkpoint,
                error=str(e),
            )

    def rollback(self, target: str, checkpoint_id: Optional[str] = None) -> bool:
        """
        回滚到检查点
        
        Args:
            target: 目标环境名
            checkpoint_id: 检查点时间戳，None 表示最近一个
        """
        ctx = self._ctx(target)
        
        # 查找检查点
        checkpoint = self._find_checkpoint(target, checkpoint_id)
        if not checkpoint:
            log.error("未找到检查点: %s", checkpoint_id or "最近")
            return False

        log.info("开始回滚 %s 到检查点 %s", target, checkpoint.timestamp)

        try:
            # 使用 rsync 从备份恢复
            backup_spec = checkpoint.backup_path.rstrip("/") + "/"
            target_spec = ctx.rsync_spec()

            cmd = ["rsync", "-az", "--delete"]
            cmd.extend(RSYNC_EXCLUDES)
            cmd.extend([backup_spec, target_spec])

            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode not in (0, 23):
                raise RuntimeError(f"rsync 恢复失败: {proc.stderr}")

            # 如果有 Git commit 记录，尝试恢复
            if checkpoint.git_commit:
                try:
                    repo = GitRepo(ctx)
                    repo.reset_hard(checkpoint.git_commit)
                    log.info("  Git 已恢复到 %s", checkpoint.git_commit[:8])
                except Exception as e:
                    log.warning("  Git 恢复失败: %s", e)

            log.info("✓ 回滚完成")
            return True

        except Exception as e:
            log.error("回滚失败: %s", e)
            return False

    def list_checkpoints(self, env: str) -> List[SyncCheckpoint]:
        """列出环境的所有检查点"""
        import json
        checkpoints = []
        pattern = f"checkpoint-{env}-*.json"
        for f in self.checkpoint_dir.glob(pattern):
            try:
                data = json.loads(f.read_text())
                checkpoints.append(SyncCheckpoint(**data))
            except Exception:
                continue
        return sorted(checkpoints, key=lambda c: c.timestamp, reverse=True)

    def cleanup_checkpoints(self, env: str, keep: int = 5):
        """清理旧检查点，保留最近 N 个"""
        checkpoints = self.list_checkpoints(env)
        for cp in checkpoints[keep:]:
            # 删除备份目录
            import shutil
            backup_path = Path(cp.backup_path)
            if backup_path.exists():
                shutil.rmtree(backup_path)
            # 删除元数据文件
            meta_file = self.checkpoint_dir / f"checkpoint-{env}-{cp.timestamp}.json"
            if meta_file.exists():
                meta_file.unlink()
        log.info("清理完成，保留 %d 个检查点", min(keep, len(checkpoints)))

    def _ctx(self, env: str) -> EnvContext:
        if env not in self.config.envs:
            raise ValueError(f"环境未配置: {env}")
        return EnvContext(name=env, entry=self.config.envs[env])

    def _check_clean_target(self, ctx: EnvContext):
        """检查目标环境是否干净"""
        try:
            repo = GitRepo(ctx)
            repo.ensure_repo()
            status = repo.status()
            if status.dirty:
                details = "\n".join(status.short_status[:10])
                raise RuntimeError(
                    f"目标 {ctx.display} 有未提交变更，请先处理：\n{details}"
                )
        except RuntimeError:
            raise
        except Exception:
            log.info("目标 %s 非 Git 仓库，跳过脏检查", ctx.display)

    def _create_checkpoint(self, ctx: EnvContext) -> SyncCheckpoint:
        """创建备份检查点"""
        import json

        timestamp = time.strftime("%Y%m%d-%H%M%S")
        backup_dir = self.checkpoint_dir / f"backup-{ctx.name}-{timestamp}"
        backup_dir.mkdir(parents=True, exist_ok=True)

        # rsync 备份
        cmd = ["rsync", "-az"]
        cmd.extend(RSYNC_EXCLUDES)
        cmd.extend([ctx.rsync_spec(), str(backup_dir) + "/"])

        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode not in (0, 23):
            raise RuntimeError(f"备份失败: {proc.stderr}")

        # 获取 Git commit
        git_commit = None
        try:
            repo = GitRepo(ctx)
            repo.ensure_repo()
            git_commit = repo.head_commit()
        except Exception:
            pass

        checkpoint = SyncCheckpoint(
            timestamp=timestamp,
            env_name=ctx.name,
            backup_path=str(backup_dir),
            git_commit=git_commit,
        )

        # 保存元数据
        meta_file = self.checkpoint_dir / f"checkpoint-{ctx.name}-{timestamp}.json"
        meta_file.write_text(json.dumps(checkpoint.to_dict(), indent=2))

        return checkpoint

    def _do_sync(
        self,
        src: EnvContext,
        dst: EnvContext,
        checksum: bool,
        extra_excludes: Optional[List[str]] = None,
    ) -> tuple[int, int]:
        """执行同步，返回 (同步文件数, 删除文件数)"""
        args = build_rsync_args(
            dry_run=False,
            checksum=checksum,
            delete=True,
            progress=True,
            itemize=True,
        )
        # 添加额外排除规则（代码扫描结果）
        if extra_excludes:
            args.extend(extra_excludes)
        args.extend([src.rsync_spec(), dst.rsync_spec()])

        proc = subprocess.run(args, capture_output=True, text=True)
        if proc.returncode not in (0, 23):
            raise RuntimeError(f"rsync 同步失败: {proc.stderr or proc.stdout}")

        # 解析结果统计
        synced = deleted = 0
        for line in proc.stdout.splitlines():
            if line.startswith(">") or line.startswith("<"):
                synced += 1
            elif line.startswith("*deleting"):
                deleted += 1

        return synced, deleted

    def _verify_sync(
        self,
        src: EnvContext,
        dst: EnvContext,
        extra_excludes: Optional[List[str]] = None,
    ) -> bool:
        """验证同步后的一致性"""
        # 使用 rsync dry-run + checksum 验证
        args = build_rsync_args(
            dry_run=True,
            checksum=True,
            delete=True,
            itemize=True,
        )
        if extra_excludes:
            args.extend(extra_excludes)
        args.extend([src.rsync_spec(), dst.rsync_spec()])

        proc = subprocess.run(args, capture_output=True, text=True)
        if proc.returncode not in (0, 23):
            return False

        # 如果没有差异输出，说明一致
        diff_lines = [ln for ln in proc.stdout.splitlines() if ln.strip() and not ln.startswith("building")]
        return len(diff_lines) == 0

    def _auto_commit(self, ctx: EnvContext, message: str):
        """自动提交变更"""
        try:
            repo = GitRepo(ctx)
            repo.ensure_repo()
            status = repo.status()
            if status.dirty:
                ctx.client.run("git add -A", cwd=ctx.entry.path)
                ctx.client.run(f'git commit -m "{message}"', cwd=ctx.entry.path)
                log.info("  自动提交完成")
        except Exception as e:
            log.warning("  自动提交失败: %s", e)

    def _find_checkpoint(self, env: str, checkpoint_id: Optional[str]) -> Optional[SyncCheckpoint]:
        """查找检查点"""
        checkpoints = self.list_checkpoints(env)
        if not checkpoints:
            return None
        if checkpoint_id is None:
            return checkpoints[0]  # 最近的
        for cp in checkpoints:
            if cp.timestamp == checkpoint_id:
                return cp
        return None
