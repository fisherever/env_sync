# EnvSync 环境同步管理框架

面向「local（可 Docker）、dev（预发布，无 Docker）、prod（生产，无公网，仅内网）」三环境的代码统一开发、差异探查、同步与部署工具。

## 核心特性

✅ **一键初始化**：自动配置三方环境到 GitLab 一致状态  
✅ **智能差异探查**：基于 rsync checksum 的文件级差异报告  
✅ **安全同步**：支持 safe/force 策略，防止误覆盖  
✅ **依赖管理**：Python/Node.js 离线下载与跨环境传输  
✅ **加密存储**：GitLab token 自动加密保护  
✅ **SSH 安全**：主机密钥验证，防止中间人攻击  
✅ **进度可视**：实时显示同步进度和状态  
✅ **自动排除**：智能过滤 .git、node_modules 等无关文件

## 安装

```bash
pip install -e .
```

> **Docker 可选**：当前机无需安装 Docker。仅当 local 机器需要时配置 `type: docker`。

## 部署到另一台电脑

### 方式一：pip 安装（推荐）
```bash
# 在目标机器上
git clone <your-gitlab-repo> envsync
cd envsync
pip install -e .

# 初始化配置
envsync init
```

### 方式二：rsync 复制
```bash
# 从本地机器 rsync
rsync -az /path/to/envsync user@target:/path/to/envsync
ssh user@target "cd /path/to/envsync && pip install -e ."
```

## 快速开始

### 1. 初始化配置

```bash
# 创建配置文件 ~/.envsync/config.yaml
envsync init

# 配置三个环境
envsync config set-env local --type native --host localhost --path /Users/you/project
envsync config set-env dev   --type native --host dev.example.com  --path /data2/project --user devuser
envsync config set-env prod  --type native --host prod.example.com --path /data/project  --user produser

# 配置 GitLab（token 会自动加密存储）
envsync config set-gitlab --url https://gitlab.company.com --token <your-token> --project group/repo

# 验证配置
envsync config validate
```

### 2. 一键初始化三方环境

```bash
# 以 local 为基准，初始化所有环境到一致状态
envsync init-all --base local --branch main
```

此命令将自动：
1. 验证所有环境连接
2. 在 local 初始化 Git（如需要）
3. 推送到 GitLab
4. 在 dev/prod 拉取代码
5. 验证三方 HEAD 一致性

### 3. 日常开发工作流

```bash
# 查看所有环境状态
envsync status

# 探查两个环境的差异
envsync diff local prod

# 安全同步（自动备份 + 校验）
envsync sync local dev

# 强制同步（覆盖目标）
envsync sync local dev --strategy force

# 同步并自动提交
envsync sync local dev --auto-commit
```

### 4. 回滚与恢复

```bash
# 查看可用检查点
envsync checkpoints dev

# 回滚到最近的检查点
envsync rollback dev

# 回滚到指定检查点
envsync rollback dev --checkpoint 20260109-001234

# 清理旧检查点，保留最近 3 个
envsync cleanup dev --keep 3
```

### 5. 依赖管理（离线场景）

```bash
# 在 dev（可联网）下载依赖
envsync deps download dev

# 传输到 prod（无公网）
envsync deps transfer dev prod

# 在 prod 离线安装
envsync deps install prod --cache
```

## 核心命令

### 配置管理
- `envsync init` - 创建默认配置文件
- `envsync config set-env <name>` - 设置环境信息
- `envsync config set-gitlab` - 配置 GitLab（token 自动加密）
- `envsync config list` - 查看配置
- `envsync config validate` - 验证配置

### 环境初始化与状态
- `envsync init-all [--base local] [--branch main]` - **一键初始化三方环境**
- `envsync status` - 查看所有环境 Git 状态

### 差异与同步
- `envsync diff <env1> <env2>` - 生成文件级差异报告
- `envsync sync <source> <target> [--strategy safe|force]` - 同步代码
  - `--backup/--no-backup` - 同步前自动备份（默认开启）
  - `--verify/--no-verify` - 同步后校验一致性（默认开启）
  - `--auto-commit` - 同步后自动 Git 提交

### 回滚与检查点
- `envsync rollback <env> [--checkpoint <timestamp>]` - 回滚到检查点
- `envsync checkpoints <env>` - 列出环境的所有检查点
- `envsync cleanup <env> [--keep N]` - 清理旧检查点

### 依赖管理
- `envsync deps download <env>` - 下载依赖到本地缓存
- `envsync deps transfer <source> <target>` - 跨环境传输依赖
- `envsync deps install <env> [--cache/--no-cache]` - 安装依赖

### 部署
- `envsync deploy <env>` - 部署到环境（待完善）

## 项目结构

```
envsync/
├── envsync/
│   ├── cli.py              # CLI 命令行入口
│   ├── core/
│   │   ├── config.py       # 配置管理（加密支持）
│   │   ├── init.py         # 一键初始化服务
│   │   ├── diff.py         # 差异探查
│   │   ├── sync.py         # 代码同步
│   │   ├── deps.py         # 依赖管理（支持本地/远程环境）
│   │   ├── deploy.py       # 部署编排（待实现）
│   │   ├── adapter.py      # 环境适配（模板渲染）
│   │   └── rsync_config.py # rsync 共享配置
│   └── utils/
│       ├── ssh.py          # SSH 远程执行（连接复用）
│       ├── git.py          # Git 操作封装
│       ├── envs.py         # 环境上下文
│       ├── crypto.py       # 加密工具（带版本前缀）
│       └── logger.py       # 日志工具
├── requirements.txt
├── pyproject.toml
└── README.md
```

## 故障排查

### SSH 连接失败
```
错误: 主机 xxx 不在 known_hosts 中
解决: ssh user@host  # 手动连接一次添加密钥
```

### rsync 权限错误
```
错误: rsync 传输失败
解决: 检查目标路径权限，确保 SSH 用户有写入权限
```

### Token 解密失败
```
错误: 解密失败，密钥可能已变更
解决: 重新运行 envsync config set-gitlab 设置 token
```

---

**开发状态**: ✅ 核心功能已完成，可用于生产环境  
**版本**: 0.1.0  
**许可**: MIT

## 技术实现

### 差异探查
- 使用 `rsync --dry-run --checksum --delete` 计算文件差异
- 自动排除 `.git`、`node_modules`、`__pycache__` 等
- 遵守 `.gitignore` 规则
- 输出详细报告到 `~/.envsync/reports/`

### 安全机制
- **Token 加密**：使用 PBKDF2HMAC + Fernet 加密，带 `enc:v1:` 版本前缀
- **SSH 验证**：优先使用系统 `known_hosts`，防止中间人攻击
- **SSH 连接复用**：同一环境上下文复用 SSH 连接，提升性能
- **配置备份**：每次保存自动备份旧配置
- **二次确认**：危险操作（force sync、init-all）需用户确认

### 依赖管理
- **Python**：`pip download` → 离线缓存 → `pip install --no-index`
- **Node.js**：`npm pack` → 传输 → `npm install --offline`
- **本地/远程支持**：远程环境自动在远程下载后 rsync 回本地缓存
- **传输**：通过 rsync + SSH 跨环境传输缓存

### rsync 配置
- 统一的排除规则通过 `rsync_config.py` 管理
- 自动排除：`.git`、`node_modules`、`__pycache__`、`.venv` 等
- 遵守 `.gitignore` 规则

## 环境变量

- `ENVSYNC_AUTO_ADD_HOST=true` - 允许自动添加未知 SSH 主机（仅开发环境）

## 注意事项

1. **首次 SSH 连接**：建议手动 SSH 登录一次，添加主机密钥到 `~/.ssh/known_hosts`
2. **Token 安全**：配置文件中的 token 已加密，但请妥善保管 `~/.envsync/.secret_key`
3. **依赖缓存**：离线安装前确保已通过 `deps download` 在联网环境缓存
4. **路径一致性**：三个环境的项目结构应保持一致

## 后续计划

- [ ] 部署编排完整流程（健康检查、回滚）
- [ ] 审计日志（记录所有同步/部署操作）
- [ ] Git 分支策略自动化（feature → dev → main）
- [ ] 配置模板系统（环境特定配置注入）
- [ ] 单元测试与集成测试

## 开发者 API

```python
from envsync.core import (
    ConfigService,
    DiffService,
    SyncService,
    DependencyService,
    InitService,
    build_rsync_args,
)

# 加载配置
config = ConfigService().load()

# 比较环境差异
diff_svc = DiffService(config)
report = diff_svc.compare("local", "prod")
print(report.summary())

# 构建自定义 rsync 参数
args = build_rsync_args(dry_run=True, checksum=True)
```
