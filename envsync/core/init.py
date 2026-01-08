from __future__ import annotations

from typing import Optional

from envsync.core.config import ConfigData
from envsync.utils.envs import EnvContext
from envsync.utils.git import GitRepo
from envsync.utils.logger import get_logger

log = get_logger(__name__)


class InitService:
    """
    一键初始化所有环境到一致状态：
    1. 验证环境连接
    2. 确定基准环境（默认 local）
    3. 在基准环境初始化 Git（如果需要）
    4. 推送到 GitLab
    5. 其他环境 clone 或 pull
    6. 验证三方代码一致性
    """

    def __init__(self, config: ConfigData):
        self.config = config

    def init_all(self, base_env: str = "local", branch: str = "main"):
        """
        一键初始化三方环境
        """
        log.info("开始初始化所有环境，基准环境: %s", base_env)

        # 1. 验证环境
        log.info("步骤 1/6: 验证环境配置与连接...")
        self._validate_environments()

        # 2. 确保基准环境是 Git 仓库
        log.info("步骤 2/6: 检查基准环境 Git 仓库...")
        base_ctx = self._ctx(base_env)
        base_repo = self._ensure_git_repo(base_ctx)

        # 3. 确保远程仓库配置
        log.info("步骤 3/6: 配置 GitLab 远程仓库...")
        self._ensure_remote(base_repo, base_ctx)

        # 4. 推送基准代码到 GitLab
        log.info("步骤 4/6: 推送基准代码到 GitLab...")
        self._push_base(base_repo, branch)

        # 5. 其他环境从 GitLab 拉取
        log.info("步骤 5/6: 同步其他环境...")
        for env_name in self.config.envs.keys():
            if env_name == base_env:
                continue
            self._sync_env(env_name, branch)

        # 6. 验证一致性
        log.info("步骤 6/6: 验证环境一致性...")
        self._verify_consistency(branch)

        log.info("✓ 所有环境初始化完成")

    def _ctx(self, env: str) -> EnvContext:
        if env not in self.config.envs:
            raise ValueError(f"环境未配置: {env}")
        return EnvContext(name=env, entry=self.config.envs[env])

    def _validate_environments(self):
        """验证所有环境可连接"""
        issues = self.config.validate()
        if issues:
            raise RuntimeError(f"配置验证失败:\n" + "\n".join(f"  - {i}" for i in issues))

        for env_name, entry in self.config.envs.items():
            ctx = EnvContext(name=env_name, entry=entry)
            try:
                # 简单的连通性测试
                result = ctx.client.run("echo 'connectivity test'")
                if result.code != 0:
                    raise RuntimeError(f"连接失败: {result.stderr}")
                log.info("  ✓ %s 连接正常", env_name)
            except Exception as e:
                raise RuntimeError(f"环境 {env_name} 连接失败: {e}")

    def _ensure_git_repo(self, ctx: EnvContext) -> GitRepo:
        """确保环境是 Git 仓库，如不是则初始化"""
        try:
            repo = GitRepo(ctx)
            repo.ensure_repo()
            log.info("  ✓ %s 已是 Git 仓库", ctx.name)
            return repo
        except Exception:
            log.info("  ! %s 不是 Git 仓库，正在初始化...", ctx.name)
            ctx.client.run("git init", cwd=ctx.entry.path).check_ok("git init")
            ctx.client.run("git add .", cwd=ctx.entry.path).check_ok("git add")
            ctx.client.run(
                'git commit -m "Initial commit by envsync"',
                cwd=ctx.entry.path,
            ).check_ok("git commit")
            repo = GitRepo(ctx)
            log.info("  ✓ %s Git 仓库初始化完成", ctx.name)
            return repo

    def _ensure_remote(self, repo: GitRepo, ctx: EnvContext):
        """配置 GitLab 远程仓库"""
        if not self.config.gitlab:
            raise RuntimeError("未配置 GitLab 信息，请先运行 envsync config set-gitlab")

        # 检查是否已有 origin
        result = ctx.client.run("git remote get-url origin", cwd=ctx.entry.path)
        if result.code == 0:
            current_url = result.stdout.strip()
            log.info("  ✓ 已配置远程仓库: %s", current_url)
            return

        # 添加 origin
        if not self.config.gitlab.project:
            raise RuntimeError("GitLab 项目路径未配置，请在 config 中指定 project")

        git_url = f"{self.config.gitlab.url}/{self.config.gitlab.project}.git"
        ctx.client.run(f"git remote add origin {git_url}", cwd=ctx.entry.path).check_ok("add remote")
        log.info("  ✓ 已添加远程仓库: %s", git_url)

    def _push_base(self, repo: GitRepo, branch: str):
        """推送基准环境代码"""
        try:
            # 确保在正确分支
            current = repo.current_branch()
            if current != branch:
                log.info("  切换到分支 %s...", branch)
                repo.checkout_branch(branch)

            # 推送
            repo.push(branch, set_upstream=True)
            log.info("  ✓ 已推送到 GitLab: %s", branch)
        except Exception as e:
            raise RuntimeError(f"推送失败: {e}")

    def _sync_env(self, env: str, branch: str):
        """同步环境到 GitLab 最新代码"""
        ctx = self._ctx(env)
        log.info("  同步 %s...", env)

        try:
            # 尝试作为现有仓库拉取
            repo = GitRepo(ctx)
            repo.ensure_repo()
            self._ensure_remote(repo, ctx)
            repo.fetch()
            repo.checkout_branch(branch)
            repo.pull(branch)
            log.info("  ✓ %s 已同步", env)
        except Exception as clone_err:
            # 如果不是仓库，尝试 clone
            log.info("  ! %s 非 Git 仓库，尝试 clone...", env)
            try:
                git_url = f"{self.config.gitlab.url}/{self.config.gitlab.project}.git"
                # 备份原目录（如果有内容）
                backup_cmd = f"[ -d {ctx.entry.path} ] && mv {ctx.entry.path} {ctx.entry.path}.bak-$(date +%s) || true"
                ctx.client.run(backup_cmd)
                # Clone
                parent = str(ctx.entry.path).rsplit("/", 1)[0]
                clone_cmd = f"git clone -b {branch} {git_url} {ctx.entry.path}"
                ctx.client.run(clone_cmd, cwd=parent).check_ok("git clone")
                log.info("  ✓ %s clone 完成", env)
            except Exception as e:
                raise RuntimeError(f"{env} 同步失败: clone_err={clone_err}, clone={e}")

    def _verify_consistency(self, branch: str):
        """验证所有环境 HEAD 一致"""
        commits = {}
        for env_name in self.config.envs.keys():
            ctx = self._ctx(env_name)
            repo = GitRepo(ctx)
            head = repo.head_commit()
            commits[env_name] = head
            log.info("  %s: %s", env_name, head[:8])

        unique_commits = set(commits.values())
        if len(unique_commits) > 1:
            log.warning("警告: 环境 HEAD 不一致，可能需要手动检查")
            for env, commit in commits.items():
                log.warning("  %s: %s", env, commit)
        else:
            log.info("  ✓ 所有环境 HEAD 一致")
