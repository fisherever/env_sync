import sys
from pathlib import Path
import click

from envsync.core.config import ConfigService, DEFAULT_CONFIG_PATH
from envsync.core.diff import DiffService
from envsync.core.sync import SyncService
from envsync.core.safe_sync import SafeSyncService
from envsync.core.scanner import ProjectScanner
from envsync.core.deps import DependencyService
from envsync.core.deploy import DeployService
from envsync.core.init import InitService
from envsync.utils.envs import EnvContext
from envsync.utils.git import GitRepo
from envsync.utils.logger import get_logger

log = get_logger(__name__)


@click.group()
def cli():
    """EnvSync 环境同步管理 CLI"""
    pass


@cli.command()
@click.option("--config", "config_path", default=DEFAULT_CONFIG_PATH, type=click.Path(), help="配置文件路径")
def init(config_path):
    """初始化配置文件"""
    config = ConfigService(Path(config_path))
    config.ensure_initialized()
    click.echo(f"配置文件已创建: {config.config_path}")


@cli.group()
def config():
    """配置管理"""
    pass


@config.command("set-env")
@click.argument("env_name")
@click.option("--type", "env_type", required=True, type=click.Choice(["docker", "native"]))
@click.option("--host", required=False, help="主机名或IP，local native 模式可为空")
@click.option("--path", "env_path", required=True, help="代码目录绝对路径")
@click.option("--user", required=False, help="SSH 用户名")
def config_set_env(env_name, env_type, host, env_path, user):
    """设置环境信息"""
    service = ConfigService()
    cfg = service.load()
    cfg.set_env(env_name, env_type, host, env_path, user)
    service.save(cfg)
    click.echo(f"环境 {env_name} 已更新")


@config.command("set-gitlab")
@click.option("--url", required=True)
@click.option("--token", required=True, help="访问令牌，会以本地加密方式存储")
@click.option("--project", "project_path", required=False, help="GitLab 项目路径，如 group/repo")
def config_set_gitlab(url, token, project_path):
    """设置 GitLab 信息"""
    service = ConfigService()
    cfg = service.load()
    cfg.set_gitlab(url, token, project_path)
    service.save(cfg)
    click.echo("GitLab 信息已更新")


@config.command("list")
def config_list():
    """列出配置"""
    service = ConfigService()
    cfg = service.load()
    click.echo(cfg.pretty())


@config.command("validate")
def config_validate():
    """验证配置"""
    service = ConfigService()
    cfg = service.load()
    issues = cfg.validate()
    if issues:
        click.echo("配置存在问题:")
        for issue in issues:
            click.echo(f"- {issue}")
        sys.exit(1)
    click.echo("配置验证通过")


@cli.command()
def status():
    """查看所有环境的 Git 状态"""
    config = ConfigService().load()
    click.echo("=" * 60)
    click.echo("环境状态概览")
    click.echo("=" * 60)
    for name, entry in config.envs.items():
        ctx = EnvContext(name, entry)
        try:
            repo = GitRepo(ctx)
            st = repo.status()
            click.echo(f"\n[{name}] {ctx.display}")
            for line in st.summary_lines():
                click.echo(f"  {line}")
            if st.dirty:
                click.echo(f"  ⚠ 工作区有未提交变更:")
                for status_line in st.short_status[:5]:
                    click.echo(f"    {status_line}")
                if len(st.short_status) > 5:
                    click.echo(f"    ... 还有 {len(st.short_status) - 5} 个文件")
        except Exception as e:
            click.echo(f"\n[{name}] {ctx.display}")
            click.echo(f"  ✗ 错误: {e}", err=True)
    click.echo("\n" + "=" * 60)


@cli.command()
@click.argument("env1")
@click.argument("env2")
def diff(env1, env2):
    """对比两个环境的代码差异"""
    service = DiffService(ConfigService().load())
    report = service.compare(env1, env2)
    click.echo(report.summary())
    if report.has_diff:
        click.echo("\n详细差异报告已生成到: ")
        click.echo(f" - {report.output_path}")


@cli.command()
@click.option("--base", default="local", help="基准环境，默认为 local")
@click.option("--branch", default="main", help="Git 分支，默认为 main")
@click.confirmation_option(prompt="此操作将初始化所有环境到一致状态，是否继续？")
def init_all(base, branch):
    """一键初始化所有环境到一致状态"""
    service = InitService(ConfigService().load())
    service.init_all(base_env=base, branch=branch)
    click.echo(f"\n✓ 所有环境已初始化完成，基准: {base}, 分支: {branch}")


@cli.command()
@click.argument("source")
@click.argument("target")
@click.option("--strategy", default="safe", type=click.Choice(["safe", "force"]))
@click.option("--backup/--no-backup", default=True, help="同步前备份目标环境")
@click.option("--verify/--no-verify", default=True, help="同步后校验一致性")
@click.option("--auto-commit", is_flag=True, help="同步后自动 Git 提交")
@click.option("--code-only", is_flag=True, help="仅同步代码（自动排除依赖/构建产物）")
@click.option("--component", "components", multiple=True, help="指定同步的组件类型（python/node/go等）")
def sync(source, target, strategy, backup, verify, auto_commit, code_only, components):
    """同步代码（支持备份、校验、自动提交）"""
    if strategy == "force":
        click.confirm(
            f"强制同步将覆盖 {target} 的所有变更，确认继续？",
            abort=True,
        )
    service = SafeSyncService(ConfigService().load())
    result = service.sync(
        source, target,
        strategy=strategy,
        backup=backup,
        verify=verify,
        auto_commit=auto_commit,
        code_only=code_only,
        components=list(components) if components else None,
    )
    click.echo(result.summary())
    if result.code_only and result.components_synced:
        click.echo(f"  同步组件: {', '.join(result.components_synced)}")
    if not result.success:
        sys.exit(1)


@cli.group()
def deps():
    """依赖管理"""
    pass


@deps.command("download")
@click.argument("env")
def deps_download(env):
    service = DependencyService(ConfigService().load())
    path = service.download(env)
    click.echo(f"依赖已缓存到: {path}")


@deps.command("transfer")
@click.argument("source")
@click.argument("target")
def deps_transfer(source, target):
    service = DependencyService(ConfigService().load())
    path = service.transfer(source, target)
    click.echo(f"依赖已从 {source} 传输到 {target}: {path}")


@deps.command("install")
@click.argument("env")
@click.option("--cache/--no-cache", default=True, help="是否使用本地缓存")
def deps_install(env, cache):
    """在指定环境安装依赖"""
    service = DependencyService(ConfigService().load())
    service.install(env, use_cache=cache)
    click.echo(f"{env} 依赖安装完成")


@cli.command()
@click.argument("env")
def deploy(env):
    """部署到指定环境"""
    service = DeployService(ConfigService().load())
    service.deploy(env)
    click.echo(f"{env} 部署完成")


@cli.command()
@click.argument("target")
@click.option("--checkpoint", default=None, help="指定检查点时间戳，默认最近一个")
def rollback(target, checkpoint):
    """回滚到检查点"""
    click.confirm(
        f"将回滚 {target} 到检查点 {checkpoint or '最近'}，确认继续？",
        abort=True,
    )
    service = SafeSyncService(ConfigService().load())
    success = service.rollback(target, checkpoint)
    if success:
        click.echo(f"✓ {target} 已回滚")
    else:
        click.echo(f"✗ 回滚失败", err=True)
        sys.exit(1)


@cli.command("checkpoints")
@click.argument("env")
def list_checkpoints(env):
    """列出环境的所有检查点"""
    service = SafeSyncService(ConfigService().load())
    checkpoints = service.list_checkpoints(env)
    if not checkpoints:
        click.echo(f"{env} 没有检查点")
        return
    click.echo(f"{env} 的检查点：")
    for cp in checkpoints:
        git_info = f" (git: {cp.git_commit[:8]})" if cp.git_commit else ""
        click.echo(f"  {cp.timestamp}{git_info}")
        click.echo(f"    备份: {cp.backup_path}")


@cli.command("cleanup")
@click.argument("env")
@click.option("--keep", default=5, help="保留最近 N 个检查点")
def cleanup_checkpoints(env, keep):
    """清理旧检查点"""
    service = SafeSyncService(ConfigService().load())
    service.cleanup_checkpoints(env, keep=keep)
    click.echo(f"已清理 {env} 的旧检查点，保留最近 {keep} 个")


@cli.command()
@click.argument("env")
@click.option("--force", is_flag=True, help="强制重新扫描（忽略缓存）")
def scan(env, force):
    """扫描环境的项目结构（识别代码/非代码）"""
    scanner = ProjectScanner(ConfigService().load())
    structure = scanner.scan(env, force=force)
    click.echo(structure.summary())


@cli.command("compare-structure")
@click.argument("env1")
@click.argument("env2")
def compare_structure(env1, env2):
    """比较两个环境的项目结构差异"""
    scanner = ProjectScanner(ConfigService().load())
    result = scanner.compare_structures(env1, env2)
    
    click.echo(f"结构比较: {env1} vs {env2}")
    click.echo(f"  类型匹配: {'✓' if result['types_match'] else '✗'}")
    if result['types_in_1_only']:
        click.echo(f"  仅在 {env1}: {result['types_in_1_only']}")
    if result['types_in_2_only']:
        click.echo(f"  仅在 {env2}: {result['types_in_2_only']}")
    click.echo(f"  代码目录匹配: {'✓' if result['code_dirs_match'] else '✗'}")
    if result['code_only_in_1']:
        click.echo(f"  仅在 {env1} 的目录: {len(result['code_only_in_1'])} 个")
    if result['code_only_in_2']:
        click.echo(f"  仅在 {env2} 的目录: {len(result['code_only_in_2'])} 个")
    click.echo(f"  结构兼容: {'✓ 可安全同步' if result['structure_compatible'] else '⚠ 需注意差异'}")


def main():
    try:
        cli()
    except KeyboardInterrupt:
        click.echo("\n操作已取消", err=True)
        sys.exit(130)
    except Exception as e:
        log.error("执行失败: %s", str(e))
        import traceback
        if sys.stderr.isatty():
            # 交互式终端显示详细错误
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
