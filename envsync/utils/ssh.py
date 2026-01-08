from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from typing import Dict, Optional

import paramiko


@dataclass
class CommandResult:
    code: int
    stdout: str
    stderr: str

    def check_ok(self, context: str = "") -> "CommandResult":
        if self.code != 0:
            msg = f"命令执行失败{f' ({context})' if context else ''}: {self.stderr.strip() or self.stdout}"
            raise RuntimeError(msg)
        return self


class SSHClientWrapper:
    """
    简化本地/远程命令执行。host 为 None/localhost 时走本地子进程，否则走 SSH。
    使用系统 known_hosts 验证主机密钥以提高安全性。
    """

    def __init__(self, host: Optional[str], user: Optional[str] = None, port: int = 22, timeout: int = 600):
        self.host = host
        self.user = user
        self.port = port
        self.timeout = timeout

    def run(self, command: str, cwd: Optional[str] = None, env: Optional[Dict[str, str]] = None) -> CommandResult:
        if self.host in (None, "", "localhost", "127.0.0.1"):
            return self._run_local(command, cwd=cwd, env=env)
        return self._run_remote(command, cwd=cwd, env=env)

    def _run_local(self, command: str, cwd: Optional[str], env: Optional[Dict[str, str]]) -> CommandResult:
        full_cmd = command
        if cwd:
            full_cmd = f"cd {shlex.quote(cwd)} && {command}"
        proc = subprocess.run(
            full_cmd,
            shell=True,
            capture_output=True,
            text=True,
            env=env,
        )
        return CommandResult(code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)

    def _run_remote(self, command: str, cwd: Optional[str], env: Optional[Dict[str, str]]) -> CommandResult:
        client = paramiko.SSHClient()
        # 优先使用系统 known_hosts，提高安全性
        client.load_system_host_keys()
        # 仅在开发环境允许自动添加新主机（可通过环境变量控制）
        import os
        if os.environ.get("ENVSYNC_AUTO_ADD_HOST", "").lower() == "true":
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        else:
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        
        try:
            client.connect(
                hostname=self.host,
                username=self.user,
                port=self.port,
                allow_agent=True,
                look_for_keys=True,
                timeout=self.timeout,
            )
        except paramiko.SSHException as e:
            if "not found in known_hosts" in str(e).lower():
                raise RuntimeError(
                    f"主机 {self.host} 不在 known_hosts 中。"
                    f"请先手动 SSH 连接添加主机密钥，或设置环境变量 ENVSYNC_AUTO_ADD_HOST=true"
                ) from e
            raise
        try:
            env_prefix = ""
            if env:
                merged = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())
                env_prefix = f"export {merged} && "
            cmd = command
            if cwd:
                cmd = f"cd {shlex.quote(cwd)} && {command}"
            stdin, stdout, stderr = client.exec_command(env_prefix + cmd, timeout=self.timeout)
            out = stdout.read().decode()
            err = stderr.read().decode()
            exit_status = stdout.channel.recv_exit_status()
            return CommandResult(code=exit_status, stdout=out, stderr=err)
        finally:
            client.close()
