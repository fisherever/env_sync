from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from envsync.core.config import ConfigData
from envsync.core.rsync_config import build_rsync_args
from envsync.utils.envs import EnvContext
from envsync.utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class DiffReport:
    env1: str
    env2: str
    has_diff: bool
    summary_lines: List[str]
    output_path: Optional[Path] = None

    def summary(self) -> str:
        lines = [f"[{self.env1}] vs [{self.env2}]"]
        lines.extend(self.summary_lines)
        return "\n".join(lines)


class DiffService:
    def __init__(self, config: ConfigData):
        self.config = config

    def compare(self, env1: str, env2: str) -> DiffReport:
        """
        使用 rsync --dry-run --checksum --delete 生成文件级差异报告。
        - 支持本地与远程（通过 SSH）
        - 统计新增/修改/删除文件数量
        - 输出详细报告到 ~/.envsync/reports
        """
        ctx1, ctx2 = self._build_ctx(env1), self._build_ctx(env2)
        log.info("开始生成差异报告: %s -> %s", ctx1.display, ctx2.display)
        report_dir = Path.home() / ".envsync" / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        report_file = report_dir / f"diff-{env1}-vs-{env2}-{timestamp}.txt"

        lines, stats = self._rsync_diff(ctx1, ctx2)
        has_diff = any(v > 0 for v in stats)

        summary_lines = [
            f"新增: {stats[0]}",
            f"修改: {stats[1]}",
            f"删除: {stats[2]}",
            f"报告: {report_file}",
        ]

        report_file.write_text("\n".join(lines), encoding="utf-8")
        return DiffReport(env1=env1, env2=env2, has_diff=has_diff, summary_lines=summary_lines, output_path=report_file)

    def _build_ctx(self, env: str) -> EnvContext:
        if env not in self.config.envs:
            raise ValueError(f"环境未配置: {env}")
        return EnvContext(name=env, entry=self.config.envs[env])

    def _rsync_diff(self, src: EnvContext, dst: EnvContext) -> Tuple[List[str], Tuple[int, int, int]]:
        """
        返回 (明细行, (新增, 修改, 删除))
        """
        cmd = build_rsync_args(
            dry_run=True,
            checksum=True,
            delete=True,
            itemize=True,
        )
        cmd.append("--out-format=%i %f")
        cmd.extend([src.rsync_spec(), dst.rsync_spec()])
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode not in (0, 23):  # 23 可能表示部分文件差异/缺失，仍记录
            raise RuntimeError(f"rsync 执行失败: {proc.stderr or proc.stdout}")

        created = modified = deleted = 0
        detail_lines: List[str] = []
        for ln in proc.stdout.splitlines():
            if not ln.strip():
                continue
            if ln.startswith("*deleting "):
                deleted += 1
                detail_lines.append(f"DEL {ln[len('*deleting '):]}")
                continue
            item, _, path = ln.partition(" ")
            if not path:
                continue
            if "+++++++++" in item:
                created += 1
                detail_lines.append(f"ADD {path}")
            else:
                modified += 1
                detail_lines.append(f"MOD {path}")

        summary_header = f"rsync dry-run diff {src.display} -> {dst.display}"
        detail_lines.insert(0, summary_header)
        detail_lines.insert(1, "-" * len(summary_header))
        return detail_lines, (created, modified, deleted)
