#!/usr/bin/env python3
"""通过函数接口在远程 Ubuntu 主机执行受限命令（cd / python3）。"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass


@dataclass
class RemoteCommandExecutor:
    """远程命令执行器。

    仅允许执行两类命令：
    1) cd <path>
    2) python3 ...
    """

    host: str
    user: str
    port: int = 22
    connect_timeout: int = 10
    remote_cwd: str = "~"

    def _run_ssh(self, remote_command: str) -> tuple[int, str, str]:
        ssh_cmd = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            f"ConnectTimeout={self.connect_timeout}",
            "-p",
            str(self.port),
            f"{self.user}@{self.host}",
            remote_command,
        ]
        proc = subprocess.run(ssh_cmd, capture_output=True, text=True)
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()

    def execute(self, command: str) -> str:
        """函数接口：传入命令字符串并返回执行结果。"""
        command = command.strip()
        if not command:
            return "错误：命令不能为空。"

        if command.startswith("cd ") or command == "cd":
            target = command[2:].strip() or "~"
            return self._handle_cd(target)

        if command.startswith("python3"):
            remote_shell = (
                f"cd {shlex.quote(self.remote_cwd)} "
                f"&& {command}"
            )
            rc, out, err = self._run_ssh(f"bash -lc {shlex.quote(remote_shell)}")
            if rc == 0:
                return out or "(no output)"
            return f"执行失败 (exit={rc})\n{err or out}"

        return "错误：仅允许执行 'cd' 和 'python3' 命令。"

    def _handle_cd(self, target: str) -> str:
        probe = (
            f"cd {shlex.quote(self.remote_cwd)} "
            f"&& cd {shlex.quote(target)} "
            "&& pwd"
        )
        rc, out, err = self._run_ssh(f"bash -lc {shlex.quote(probe)}")
        if rc != 0:
            return f"切换目录失败\n{err or out}"

        self.remote_cwd = out
        return f"当前远程目录：{self.remote_cwd}"


if __name__ == "__main__":
    # ===== 使用示例 =====
    # 1) 请替换成你在 10.173.21.118 机器上的实际用户名
    executor = RemoteCommandExecutor(host="10.173.21.118", user="ubuntu")

    # 2) 切换目录
    print(executor.execute("cd /tmp"))

    # 3) 执行 python3 命令
    print(executor.execute("python3 -c \"import os; print('cwd=', os.getcwd())\""))
    print(executor.execute("python3 -c \"print(sum(range(1, 11)))\""))
