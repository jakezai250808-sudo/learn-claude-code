#!/usr/bin/env python3
"""通过函数接口在远程 Ubuntu 主机执行受限命令（cd / python3）。"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class RemoteCommandExecutor:
    """无状态远程命令执行器。"""

    host: str
    user: str
    port: int = 22
    connect_timeout: int = 10
    private_key: str | None = None
    password: str | None = None
    debug: bool = False
    default_cwd: str = "$HOME"

    def _log(self, message: str) -> None:
        if self.debug:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[RemoteCommandExecutor][{ts}] {message}")

    def _resolve_private_key(self) -> str | None:
        if not self.private_key:
            return None
        key_path = Path(self.private_key).expanduser()
        if not key_path.exists():
            raise RuntimeError(f"私钥文件不存在: {key_path}")
        return str(key_path)

    def _normalize_cwd(self, cwd: str | None) -> str:
        value = (cwd or self.default_cwd or "").strip()
        if value in ("", "~", "$HOME"):
            return "$HOME"
        return shlex.quote(value)

    def _build_ssh_cmd(self, remote_command: str) -> list[str]:
        ssh_cmd: list[str] = []
        if self.password:
            if shutil.which("sshpass") is None:
                raise RuntimeError("检测到 password 已设置，但本机未安装 sshpass。请安装 sshpass，或改用 private_key。")
            ssh_cmd.extend(["sshpass", "-p", self.password])

        ssh_cmd.extend([
            "ssh", "-o", "StrictHostKeyChecking=accept-new", "-o",
            f"ConnectTimeout={self.connect_timeout}", "-p", str(self.port),
            "-o", "BatchMode=no" if self.password else "BatchMode=yes",
        ])

        private_key = self._resolve_private_key()
        if private_key:
            ssh_cmd.extend(["-i", private_key, "-o", "IdentitiesOnly=yes"])

        ssh_cmd.extend([f"{self.user}@{self.host}", remote_command])
        return ssh_cmd

    def _run_ssh(self, remote_command: str) -> tuple[int, str, str]:
        try:
            ssh_cmd = self._build_ssh_cmd(remote_command)
        except RuntimeError as exc:
            return 2, "", str(exc)

        safe = ["***" if x == self.password else x for x in ssh_cmd]
        self._log(f"SSH command: {' '.join(safe)}")
        proc = subprocess.run(ssh_cmd, capture_output=True, text=True)
        out = proc.stdout.strip()
        err = proc.stderr.strip()
        self._log(f"SSH exit={proc.returncode}")
        if out:
            self._log(f"SSH stdout: {out}")
        if err:
            self._log(f"SSH stderr: {err}")
        return proc.returncode, out, err

    def execute(self, command: str, cwd: str | None = None) -> str:
        """函数接口（无状态）：每次调用独立执行。"""
        command = command.strip()
        if not command:
            return "错误：命令不能为空。"

        resolved_cwd = self._normalize_cwd(cwd)
        self._log(f"execute() received: {command}")
        self._log(f"effective cwd: {resolved_cwd}")

        if command.startswith("cd ") or command == "cd":
            target = command[2:].strip() or "$HOME"
            probe = f"cd {resolved_cwd} && cd {shlex.quote(target)} && pwd"
            self._log(f"cd probe shell: {probe}")
            rc, out, err = self._run_ssh(f"bash -lc {shlex.quote(probe)}")
            if rc != 0:
                return f"切换目录失败\nprobe={probe}\n{err or out}"
            return f"目标目录可用：{out}"

        if command.startswith("python3"):
            remote_shell = f"cd {resolved_cwd} && {command}"
            self._log(f"python3 remote shell: {remote_shell}")
            rc, out, err = self._run_ssh(f"bash -lc {shlex.quote(remote_shell)}")
            if rc == 0:
                return out or "(no output)"
            return f"执行失败 (exit={rc})\nremote_shell={remote_shell}\n{err or out}"

        return "错误：仅允许执行 'cd' 和 'python3' 命令。"


if __name__ == "__main__":
    # 无状态示例：每次都可新建一个 executor，目录通过 cwd 显式传入。
    ex = RemoteCommandExecutor(
        host="10.173.21.118",
        user="ubuntu",
        password="your_password_here",
        debug=True,
    )
    print(ex.execute("cd /tmp"))
    print(ex.execute("python3 -c \"import os; print('cwd=', os.getcwd())\"", cwd="/tmp"))
    print(ex.execute("python3 -c \"print(sum(range(1, 11)))\"", cwd="/tmp"))
