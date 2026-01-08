"""
rsync 共享配置：排除规则和通用参数
"""
from __future__ import annotations

from typing import List

# 通用排除规则
RSYNC_EXCLUDES: List[str] = [
    "--exclude=.git",
    "--exclude=.envsync",
    "--exclude=node_modules",
    "--exclude=__pycache__",
    "--exclude=*.pyc",
    "--exclude=*.pyo",
    "--exclude=.DS_Store",
    "--exclude=*.swp",
    "--exclude=.venv",
    "--exclude=venv",
]

# 遵守 .gitignore 规则
RSYNC_GITIGNORE_FILTER = "--filter=:- .gitignore"


def build_rsync_args(
    *,
    dry_run: bool = False,
    checksum: bool = False,
    delete: bool = True,
    progress: bool = False,
    itemize: bool = False,
) -> List[str]:
    """
    构建 rsync 参数列表
    
    Args:
        dry_run: 仅模拟，不实际执行
        checksum: 使用校验和比较（更精确但更慢）
        delete: 删除目标端多余文件
        progress: 显示进度
        itemize: 显示详细变更
    """
    args = ["rsync"]
    
    # 基础参数
    base_flags = "-a"  # archive mode
    if dry_run:
        base_flags += "n"  # dry-run
    base_flags += "z"  # compress
    args.append(base_flags)
    
    if checksum:
        args.append("--checksum")
    
    if delete:
        args.append("--delete")
    
    if progress:
        args.append("--info=stats2,progress2")
    
    if itemize:
        args.append("--itemize-changes")
    
    # 添加排除规则
    args.extend(RSYNC_EXCLUDES)
    args.append(RSYNC_GITIGNORE_FILTER)
    
    return args
