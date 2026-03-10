#!/usr/bin/env python3
"""通过函数接口在远程 Ubuntu 主机执行受限命令（cd / python3）。"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from dataclasses import dataclass


@dataclass
class RemoteCommandExecutor:
    """远程命令执行器。

    仅允许执行两类命令：
    1) cd <path>
    2) python3 ...

    支持两种认证方式：
    - SSH 私钥 (`private_key`)
    - SSH 密码 (`password`，依赖本机安装 sshpass)
    """

    host: str
    user: str
    port: int = 22
    connect_timeout: int = 10
    remote_cwd: str = "~"
    private_key: str | None = None
    password: str | None = None

    def _build_ssh_cmd(self, remote_command: str) -> list[str]:
        ssh_cmd: list[str] = []

        # 若提供密码，使用 sshpass；否则默认走 key/agent
        if self.password:
            if shutil.which("sshpass") is None:
                raise RuntimeError(
                    "检测到 password 已设置，但本机未安装 sshpass。"
                    "请安装 sshpass，或改用 private_key。"
                )
            ssh_cmd.extend(["sshpass", "-p", self.password])

        ssh_cmd.extend([
            "ssh",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            f"ConnectTimeout={self.connect_timeout}",
            "-p",
            str(self.port),
        ])

        if self.password:
            # 允许密码认证
            ssh_cmd.extend(["-o", "BatchMode=no"])
        else:
            # 无密码时保持非交互，避免卡住
            ssh_cmd.extend(["-o", "BatchMode=yes"])

        if self.private_key:
            ssh_cmd.extend(["-i", self.private_key])

        ssh_cmd.extend([f"{self.user}@{self.host}", remote_command])
        return ssh_cmd

    def _run_ssh(self, remote_command: str) -> tuple[int, str, str]:
        try:
            ssh_cmd = self._build_ssh_cmd(remote_command)
        except RuntimeError as exc:
            return 2, "", str(exc)

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
            remote_shell = f"cd {shlex.quote(self.remote_cwd)} && {command}"
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
    # ===== 使用示例 1：私钥认证（推荐） =====
    by_key = RemoteCommandExecutor(
        host="10.173.21.118",
        user="ubuntu",  # 替换为实际用户名
        private_key="~/.ssh/id_rsa",  # 替换为你的私钥路径
    )
    print(by_key.execute("cd /tmp"))
    print(by_key.execute("python3 -c \"import os; print('cwd=', os.getcwd())\""))

    # ===== 使用示例 2：密码认证（需安装 sshpass） =====
    by_password = RemoteCommandExecutor(
        host="10.173.21.118",
        user="ubuntu",  # 替换为实际用户名
        password="your_password_here",  # 替换为实际密码
    )
    print(by_password.execute("python3 -c \"print(sum(range(1, 11)))\""))
