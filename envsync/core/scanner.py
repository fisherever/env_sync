"""
项目结构自动扫描器：识别代码/非代码部分
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Literal

from envsync.core.config import ConfigData
from envsync.utils.envs import EnvContext
from envsync.utils.logger import get_logger

log = get_logger(__name__)


# 项目类型标记文件
PROJECT_MARKERS = {
    "python": ["requirements.txt", "pyproject.toml", "setup.py", "Pipfile", "poetry.lock"],
    "node": ["package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"],
    "go": ["go.mod", "go.sum"],
    "rust": ["Cargo.toml", "Cargo.lock"],
    "java": ["pom.xml", "build.gradle", "build.gradle.kts"],
    "ruby": ["Gemfile", "Gemfile.lock"],
    "php": ["composer.json", "composer.lock"],
}

# 非代码目录（依赖、构建产物、缓存等）
NON_CODE_DIRS = {
    # Python
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".venv", "venv", "env", ".env", "virtualenv",
    "*.egg-info", "dist", "build", ".eggs", "*.egg",
    ".tox", ".nox", "htmlcov", ".coverage",
    # Node.js
    "node_modules", ".npm", ".yarn", ".pnpm-store",
    # Go
    "vendor",
    # Rust
    "target",
    # Java
    "target", ".gradle", ".mvn",
    # General
    ".git", ".svn", ".hg",
    ".idea", ".vscode", "*.swp",
    ".DS_Store", "Thumbs.db",
    "logs", "log", "tmp", "temp", "cache",
    ".envsync",
}

# 代码文件扩展名
CODE_EXTENSIONS = {
    "python": {".py", ".pyx", ".pxd", ".pyi"},
    "node": {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte"},
    "go": {".go"},
    "rust": {".rs"},
    "java": {".java", ".kt", ".scala", ".groovy"},
    "ruby": {".rb", ".erb"},
    "php": {".php"},
    "web": {".html", ".htm", ".css", ".scss", ".sass", ".less"},
    "config": {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf"},
    "docs": {".md", ".rst", ".txt"},
    "shell": {".sh", ".bash", ".zsh", ".fish"},
}


@dataclass
class ProjectComponent:
    """项目组件（一个子项目或模块）"""
    path: str  # 相对于项目根目录的路径
    type: str  # python, node, go, etc.
    marker_files: List[str] = field(default_factory=list)
    code_dirs: List[str] = field(default_factory=list)  # 代码目录
    non_code_dirs: List[str] = field(default_factory=list)  # 非代码目录

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "type": self.type,
            "marker_files": self.marker_files,
            "code_dirs": self.code_dirs,
            "non_code_dirs": self.non_code_dirs,
        }


@dataclass
class ProjectStructure:
    """项目结构快照"""
    root_path: str
    env_name: str
    components: List[ProjectComponent] = field(default_factory=list)
    all_code_dirs: Set[str] = field(default_factory=set)
    all_non_code_dirs: Set[str] = field(default_factory=set)
    scan_time: str = ""

    def to_dict(self) -> dict:
        return {
            "root_path": self.root_path,
            "env_name": self.env_name,
            "components": [c.to_dict() for c in self.components],
            "all_code_dirs": sorted(self.all_code_dirs),
            "all_non_code_dirs": sorted(self.all_non_code_dirs),
            "scan_time": self.scan_time,
        }

    def summary(self) -> str:
        lines = [
            f"项目结构: {self.root_path}",
            f"环境: {self.env_name}",
            f"组件数: {len(self.components)}",
        ]
        for comp in self.components:
            lines.append(f"  [{comp.type}] {comp.path or '.'}")
            if comp.code_dirs:
                lines.append(f"    代码目录: {', '.join(comp.code_dirs[:5])}")
            if comp.non_code_dirs:
                lines.append(f"    排除目录: {', '.join(comp.non_code_dirs[:5])}")
        lines.append(f"总代码目录: {len(self.all_code_dirs)}")
        lines.append(f"总排除目录: {len(self.all_non_code_dirs)}")
        return "\n".join(lines)

    def get_rsync_excludes(self) -> List[str]:
        """生成 rsync 排除规则"""
        excludes = []
        for d in sorted(self.all_non_code_dirs):
            excludes.append(f"--exclude={d}")
        return excludes

    def get_rsync_includes(self, component_types: Optional[List[str]] = None) -> List[str]:
        """生成 rsync 包含规则（仅同步指定类型组件）"""
        if not component_types:
            return []  # 不过滤，同步全部代码
        
        includes = []
        for comp in self.components:
            if comp.type in component_types:
                if comp.path:
                    includes.append(f"--include={comp.path}/***")
                for code_dir in comp.code_dirs:
                    includes.append(f"--include={code_dir}/***")
        return includes


class ProjectScanner:
    """
    项目结构扫描器
    
    自动识别：
    - 项目类型（Python、Node.js、Go 等）
    - 代码目录 vs 依赖/构建产物
    - 多模块/monorepo 结构
    """

    def __init__(self, config: ConfigData):
        self.config = config
        self.cache_dir = Path.home() / ".envsync" / "scans"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def scan(self, env: str, force: bool = False) -> ProjectStructure:
        """
        扫描环境的项目结构
        
        Args:
            env: 环境名
            force: 强制重新扫描（忽略缓存）
        """
        ctx = self._ctx(env)
        
        # 检查缓存
        if not force:
            cached = self._load_cache(env)
            if cached:
                log.info("使用缓存的扫描结果: %s", env)
                return cached

        log.info("扫描项目结构: %s (%s)", env, ctx.display)
        
        structure = ProjectStructure(
            root_path=ctx.entry.path,
            env_name=env,
        )

        # 扫描项目
        self._scan_directory(ctx, "", structure)
        
        # 记录扫描时间
        import time
        structure.scan_time = time.strftime("%Y-%m-%d %H:%M:%S")
        
        # 保存缓存
        self._save_cache(env, structure)
        
        log.info("扫描完成: %d 组件, %d 代码目录, %d 排除目录",
                 len(structure.components),
                 len(structure.all_code_dirs),
                 len(structure.all_non_code_dirs))
        
        return structure

    def compare_structures(self, env1: str, env2: str) -> Dict:
        """
        比较两个环境的项目结构差异
        """
        struct1 = self.scan(env1)
        struct2 = self.scan(env2)
        
        # 比较组件
        types1 = {c.type for c in struct1.components}
        types2 = {c.type for c in struct2.components}
        
        # 比较代码目录
        code_only_in_1 = struct1.all_code_dirs - struct2.all_code_dirs
        code_only_in_2 = struct2.all_code_dirs - struct1.all_code_dirs
        
        return {
            "env1": env1,
            "env2": env2,
            "types_match": types1 == types2,
            "types_in_1_only": types1 - types2,
            "types_in_2_only": types2 - types1,
            "code_dirs_match": struct1.all_code_dirs == struct2.all_code_dirs,
            "code_only_in_1": code_only_in_1,
            "code_only_in_2": code_only_in_2,
            "structure_compatible": len(code_only_in_1) == 0 and len(code_only_in_2) == 0,
        }

    def _ctx(self, env: str) -> EnvContext:
        if env not in self.config.envs:
            raise ValueError(f"环境未配置: {env}")
        return EnvContext(name=env, entry=self.config.envs[env])

    def _scan_directory(self, ctx: EnvContext, rel_path: str, structure: ProjectStructure, depth: int = 0):
        """递归扫描目录"""
        if depth > 5:  # 限制扫描深度
            return
        
        full_path = f"{ctx.entry.path}/{rel_path}".rstrip("/")
        
        # 列出目录内容
        result = ctx.client.run(f"ls -la {full_path} 2>/dev/null | head -100")
        if result.code != 0:
            return
        
        # 检查是否有项目标记文件
        for proj_type, markers in PROJECT_MARKERS.items():
            for marker in markers:
                check = ctx.client.run(f"[ -f {full_path}/{marker} ] && echo found")
                if "found" in check.stdout:
                    # 发现项目组件
                    component = self._analyze_component(ctx, rel_path, proj_type, structure)
                    if component:
                        structure.components.append(component)
                    return  # 找到项目根，不再向下扫描

        # 获取子目录列表
        result = ctx.client.run(
            f"find {full_path} -maxdepth 1 -type d -not -name '.*' 2>/dev/null | tail -20"
        )
        if result.code != 0:
            return
        
        for line in result.stdout.strip().splitlines():
            if not line or line == full_path:
                continue
            dir_name = Path(line).name
            
            # 跳过非代码目录
            if self._is_non_code_dir(dir_name):
                non_code_rel = f"{rel_path}/{dir_name}".lstrip("/")
                structure.all_non_code_dirs.add(non_code_rel)
                continue
            
            # 递归扫描
            sub_rel = f"{rel_path}/{dir_name}".lstrip("/")
            self._scan_directory(ctx, sub_rel, structure, depth + 1)

    def _analyze_component(self, ctx: EnvContext, rel_path: str, proj_type: str, structure: ProjectStructure) -> Optional[ProjectComponent]:
        """分析项目组件"""
        full_path = f"{ctx.entry.path}/{rel_path}".rstrip("/")
        
        component = ProjectComponent(
            path=rel_path,
            type=proj_type,
        )
        
        # 查找标记文件
        for marker in PROJECT_MARKERS.get(proj_type, []):
            check = ctx.client.run(f"[ -f {full_path}/{marker} ] && echo found")
            if "found" in check.stdout:
                component.marker_files.append(marker)
        
        # 扫描子目录，区分代码和非代码
        result = ctx.client.run(f"find {full_path} -maxdepth 2 -type d 2>/dev/null | head -50")
        if result.code == 0:
            for line in result.stdout.strip().splitlines():
                if not line or line == full_path:
                    continue
                dir_name = Path(line).name
                sub_rel = line.replace(ctx.entry.path, "").lstrip("/")
                
                if self._is_non_code_dir(dir_name):
                    component.non_code_dirs.append(dir_name)
                    structure.all_non_code_dirs.add(sub_rel)
                else:
                    component.code_dirs.append(dir_name)
                    structure.all_code_dirs.add(sub_rel)
        
        # 将组件路径加入代码目录
        if rel_path:
            structure.all_code_dirs.add(rel_path)
        
        return component

    def _is_non_code_dir(self, name: str) -> bool:
        """判断是否为非代码目录"""
        if name.startswith("."):
            return True
        for pattern in NON_CODE_DIRS:
            if pattern.startswith("*"):
                if name.endswith(pattern[1:]):
                    return True
            elif name == pattern:
                return True
        return False

    def _load_cache(self, env: str) -> Optional[ProjectStructure]:
        """加载缓存的扫描结果"""
        cache_file = self.cache_dir / f"scan-{env}.json"
        if not cache_file.exists():
            return None
        
        try:
            import time
            # 检查缓存是否过期（1小时）
            if time.time() - cache_file.stat().st_mtime > 3600:
                return None
            
            data = json.loads(cache_file.read_text())
            return ProjectStructure(
                root_path=data["root_path"],
                env_name=data["env_name"],
                components=[ProjectComponent(**c) for c in data["components"]],
                all_code_dirs=set(data["all_code_dirs"]),
                all_non_code_dirs=set(data["all_non_code_dirs"]),
                scan_time=data.get("scan_time", ""),
            )
        except Exception:
            return None

    def _save_cache(self, env: str, structure: ProjectStructure):
        """保存扫描结果到缓存"""
        cache_file = self.cache_dir / f"scan-{env}.json"
        cache_file.write_text(json.dumps(structure.to_dict(), indent=2, ensure_ascii=False))
