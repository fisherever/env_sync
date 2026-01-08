from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import List, Optional, Tuple

from envsync.utils.envs import EnvContext
from envsync.utils.ssh import CommandResult


@dataclass
class GitStatus:
    branch: str
    head: str
    upstream: Optional[str]
    ahead: int
    behind: int
    dirty: bool
    staged: int
    changed: int
    untracked: int
    short_status: List[str]

    def summary_lines(self) -> List[str]:
        lines = [
            f"branch={self.branch} head={self.head}",
            f"upstream={self.upstream or '-'} ahead={self.ahead} behind={self.behind}",
            f"dirty={self.dirty} staged={self.staged} changed={self.changed} untracked={self.untracked}",
        ]
        return lines


class GitRepo:
    """
    通过 EnvContext 在本地或远程执行 git 命令。
    """

    def __init__(self, ctx: EnvContext):
        self.ctx = ctx
        self.client = ctx.client
        self.path = ctx.entry.path

    def _git(self, args: str) -> CommandResult:
        cmd = f"git -C {shlex.quote(self.path)} {args}"
        return self.client.run(cmd)

    def ensure_repo(self):
        res = self._git("rev-parse --is-inside-work-tree")
        if res.code != 0 or "true" not in res.stdout:
            raise RuntimeError(f"{self.ctx.name}: 路径不是 git 仓库: {self.path}")

    def fetch(self):
        self._git("fetch --all --prune")

    def current_branch(self) -> str:
        res = self._git("rev-parse --abbrev-ref HEAD")
        res.check_ok(f"{self.ctx.name} 获取分支")
        return res.stdout.strip()

    def head_commit(self) -> str:
        res = self._git("rev-parse HEAD")
        res.check_ok(f"{self.ctx.name} 获取 HEAD")
        return res.stdout.strip()

    def upstream(self) -> Optional[str]:
        res = self._git("rev-parse --abbrev-ref --symbolic-full-name @{u}")
        if res.code != 0:
            return None
        return res.stdout.strip()

    def ahead_behind(self) -> Tuple[int, int]:
        """
        返回 (ahead, behind)
        """
        up = self.upstream()
        if not up:
            return 0, 0
        res = self._git("rev-list --left-right --count HEAD...@{u}")
        if res.code != 0:
            return 0, 0
        left, right = res.stdout.strip().split()
        # left = HEAD only, right = upstream only
        return int(left), int(right)

    def status(self) -> GitStatus:
        self.ensure_repo()
        branch = self.current_branch()
        head = self.head_commit()
        upstream = self.upstream()
        ahead, behind = self.ahead_behind()

        res = self._git("status --porcelain")
        res.check_ok(f"{self.ctx.name} status")
        lines = [ln for ln in res.stdout.splitlines() if ln]
        staged = sum(1 for ln in lines if ln[0] != " " and ln[0] != "?")
        changed = sum(1 for ln in lines if ln[0] == " " and ln[1] != "?")
        untracked = sum(1 for ln in lines if ln.startswith("??"))
        dirty = bool(lines)

        return GitStatus(
            branch=branch,
            head=head,
            upstream=upstream,
            ahead=ahead,
            behind=behind,
            dirty=dirty,
            staged=staged,
            changed=changed,
            untracked=untracked,
            short_status=lines,
        )

    def checkout_branch(self, branch: str):
        res = self._git(f"rev-parse --verify {shlex.quote(branch)}")
        if res.code == 0:
            self._git(f"checkout {shlex.quote(branch)}").check_ok(f"{self.ctx.name} checkout {branch}")
            return
        # 如果不存在，尝试跟踪远端
        res_remote = self._git(f"ls-remote --heads origin {shlex.quote(branch)}")
        if res_remote.code == 0 and res_remote.stdout.strip():
            self._git(f"checkout -b {shlex.quote(branch)} origin/{shlex.quote(branch)}").check_ok(
                f"{self.ctx.name} checkout -b {branch}"
            )
        else:
            self._git(f"checkout -b {shlex.quote(branch)}").check_ok(f"{self.ctx.name} 新建分支 {branch}")

    def pull(self, branch: Optional[str] = None):
        args = f"pull"
        if branch:
            args = f"pull origin {shlex.quote(branch)}"
        self._git(args).check_ok(f"{self.ctx.name} pull")

    def push(self, branch: Optional[str] = None, set_upstream: bool = True):
        args = "push"
        if branch:
            if set_upstream:
                args = f"push -u origin {shlex.quote(branch)}"
            else:
                args = f"push origin {shlex.quote(branch)}"
        self._git(args).check_ok(f"{self.ctx.name} push")

    def reset_hard(self, ref: str):
        self._git(f"reset --hard {shlex.quote(ref)}").check_ok(f"{self.ctx.name} reset --hard {ref}")

    def clean(self):
        self._git("clean -fd").check_ok(f"{self.ctx.name} clean -fd")

    def has_commit(self, commit: str) -> bool:
        res = self._git(f"cat-file -e {shlex.quote(commit)}^{{commit}}")
        return res.code == 0

    def diff_name_status(self, base: str, target: str) -> List[str]:
        res = self._git(f"diff --name-status {shlex.quote(base)}..{shlex.quote(target)}")
        if res.code != 0:
            return []
        return [ln for ln in res.stdout.splitlines() if ln]
