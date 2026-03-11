#!/usr/bin/env python3
"""通过函数接口在远程 Ubuntu 主机执行受限命令（cd / python3）。"""

from __future__ import annotations

import shlex
import hashlib
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict


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
    debug: bool = False
    apply_cwd_on_python: bool = False
    enable_connection_reuse: bool = True
    control_path: str | None = None
    control_persist: str = "300s"
    screen_session_name: str | None = None
    command_wait_timeout: float = 15.0

    def __post_init__(self) -> None:
        self._command_index = 0

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

    def _base_remote_cwd(self) -> str:
        """返回可安全拼接到 bash -lc 的远程工作目录；未设置时返回空字符串。"""
        cwd = (self.remote_cwd or "").strip()
        if cwd in ("", "~"):
            return ""
        return shlex.quote(cwd)


    def _resolve_control_path(self) -> str:
        """返回 SSH 连接复用用的 ControlPath。"""
        if self.control_path:
            return self.control_path

        raw = f"{self.user}@{self.host}:{self.port}"
        digest = hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]
        base_dir = Path('/tmp/remote_ssh_mux')
        base_dir.mkdir(parents=True, exist_ok=True)
        return str(base_dir / f"mux_{digest}")

    def _build_ssh_cmd(self, remote_command: str) -> list[str]:
        ssh_cmd: list[str] = []

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

        if self.enable_connection_reuse:
            control_path = self._resolve_control_path()
            ssh_cmd.extend([
                "-o", "ControlMaster=auto",
                "-o", f"ControlPersist={self.control_persist}",
                "-o", f"ControlPath={control_path}",
            ])

        if self.password:
            ssh_cmd.extend(["-o", "BatchMode=no"])
        else:
            ssh_cmd.extend(["-o", "BatchMode=yes"])

        private_key = self._resolve_private_key()
        if private_key:
            ssh_cmd.extend(["-i", private_key, "-o", "IdentitiesOnly=yes"])

        ssh_cmd.extend([f"{self.user}@{self.host}", remote_command])
        return ssh_cmd

    @staticmethod
    def _escape_for_ansi_c(value: str) -> str:
        return (
            value
            .replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace("\n", "\\n")
        )

    def _screen_name(self) -> str:
        if self.screen_session_name:
            return self.screen_session_name
        raw = f"{self.user}@{self.host}:{self.port}"
        digest = hashlib.sha1(raw.encode('utf-8')).hexdigest()[:10]
        return f"rse_{digest}"

    def _run_ssh(self, remote_command: str) -> tuple[int, str, str]:
        try:
            ssh_cmd = self._build_ssh_cmd(remote_command)
        except RuntimeError as exc:
            return 2, "", str(exc)

        self._log(f"reuse: enabled={self.enable_connection_reuse}, control_path={self.control_path or self._resolve_control_path() if self.enable_connection_reuse else None}")
        safe_cmd_for_log = ["***" if x == self.password else x for x in ssh_cmd]
        self._log(f"SSH command: {' '.join(safe_cmd_for_log)}")
        proc = subprocess.run(ssh_cmd, capture_output=True, text=True)
        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()
        self._log(f"SSH exit={proc.returncode}")
        if stdout:
            self._log(f"SSH stdout: {stdout}")
        if stderr:
            self._log(f"SSH stderr: {stderr}")
        return proc.returncode, stdout, stderr

    def _ensure_screen_session(self) -> tuple[bool, str]:
        session_name = self._screen_name()
        q_name = shlex.quote(session_name)
        check_cmd = f"bash -lc {shlex.quote(f'screen -list {q_name} >/dev/null 2>&1')}"
        rc, _, _ = self._run_ssh(check_cmd)
        if rc == 0:
            return True, session_name

        start_shell = f"screen -dmS {q_name} bash"
        rc, out, err = self._run_ssh(f"bash -lc {shlex.quote(start_shell)}")
        if rc != 0:
            return False, f"启动 screen 会话失败: {err or out}"

        return True, session_name

    def _execute_in_screen(self, shell_command: str) -> tuple[int, str]:
        ok, info = self._ensure_screen_session()
        if not ok:
            return 2, info

        session_name = info
        self._command_index += 1
        marker = f"{int(time.time() * 1000)}_{self._command_index}"
        safe_session = ''.join(ch if ch.isalnum() else '_' for ch in session_name)
        out_file = f"/tmp/{safe_session}_{marker}.out"
        rc_file = f"/tmp/{safe_session}_{marker}.rc"

        wrapped = f"{{ {shell_command}; }} > {shlex.quote(out_file)} 2>&1; echo $? > {shlex.quote(rc_file)}"
        stuffed = self._escape_for_ansi_c(wrapped + "\n")
        send_cmd = f"screen -S {shlex.quote(session_name)} -X stuff $'{stuffed}'"
        rc, out, err = self._run_ssh(f"bash -lc {shlex.quote(send_cmd)}")
        if rc != 0:
            return 2, f"发送命令到 screen 失败: {err or out}"

        wait_script = (
            f"for _ in $(seq 1 {int(self.command_wait_timeout * 10)}); do "
            f"[ -f {shlex.quote(rc_file)} ] && break; sleep 0.1; "
            f"done; "
            f"if [ ! -f {shlex.quote(rc_file)} ]; then echo __TIMEOUT__; exit 124; fi; "
            f"cat {shlex.quote(rc_file)}"
        )
        rc, out, err = self._run_ssh(f"bash -lc {shlex.quote(wait_script)}")
        if rc != 0:
            return 2, f"等待命令执行结果失败: {err or out}"
        if out.strip() == "__TIMEOUT__":
            return 124, "执行超时，未在预期时间内收到结果。"

        try:
            cmd_rc = int(out.strip().splitlines()[-1])
        except Exception:
            cmd_rc = 1

        read_script = (
            f"cat {shlex.quote(out_file)} 2>/dev/null; "
            f"rm -f {shlex.quote(out_file)} {shlex.quote(rc_file)}"
        )
        _, cmd_out, _ = self._run_ssh(f"bash -lc {shlex.quote(read_script)}")
        return cmd_rc, cmd_out

    def execute(self, command: str) -> str:
        """函数接口：传入命令字符串并返回执行结果。"""
        command = command.strip()
        if not command:
            return "错误：命令不能为空。"

        self._log(f"execute() received: {command}")
        self._log(f"current remote_cwd state: {self.remote_cwd}")

        if command.startswith("cd ") or command == "cd":
            target = command[2:].strip() or "~"
            return self._handle_cd(target)

        if command.startswith("python3"):
            if self.remote_cwd in ("", "~"):
                self._log("warning: remote_cwd still default (~). 如果你在 handle 中每次都 new executor，cd 状态不会延续。")
            remote_shell = command
            self._log(
                f"python3 remote shell: {remote_shell} "
                f"(apply_cwd_on_python={self.apply_cwd_on_python})"
            )
            rc, out = self._execute_in_screen(remote_shell)
            if rc == 0:
                return out or "(no output)"
            return (
                f"执行失败 (exit={rc})\n"
                f"remote_shell={remote_shell}\n"
                f"{out}"
            )

        return "错误：仅允许执行 'cd' 和 'python3' 命令。"

    def _handle_cd(self, target: str) -> str:
        probe = f"cd {shlex.quote(target)} && pwd"
        self._log(f"cd probe shell: {probe}")
        rc, out = self._execute_in_screen(probe)
        if rc != 0:
            return (
                "切换目录失败\n"
                f"cd_target={target}\n"
                f"{out}"
            )

        self.remote_cwd = out
        self._log(f"remote_cwd updated to: {self.remote_cwd}")
        return f"当前远程目录：{self.remote_cwd}"


# 可选：为“handle 会被反复调用”的场景提供会话级 executor 复用。
_EXECUTOR_POOL: Dict[str, "RemoteCommandExecutor"] = {}


def get_remote_executor(session_id: str, **kwargs) -> "RemoteCommandExecutor":
    """按 session_id 复用 executor，保证 cd 后目录状态可延续。"""
    if session_id not in _EXECUTOR_POOL:
        _EXECUTOR_POOL[session_id] = RemoteCommandExecutor(**kwargs)
    return _EXECUTOR_POOL[session_id]


if __name__ == "__main__":
    # handle 场景建议：
    # ex = get_remote_executor("demo-user", host="10.173.21.118", user="ubuntu", password="xxx", debug=True)
    # print(ex.execute("cd /home"))
    # print(ex.execute("python3 -c \"import os; print(os.getcwd())\""))

    by_key = RemoteCommandExecutor(
        host="10.173.21.118",
        user="ubuntu",
        private_key="~/.ssh/id_rsa",
        debug=True,
        apply_cwd_on_python=True,
    )
    print(by_key.execute("cd /tmp"))
    print(by_key.execute("python3 -c \"import os; print('cwd=', os.getcwd())\""))

    by_password = RemoteCommandExecutor(
        host="10.173.21.118",
        user="ubuntu",
        password="your_password_here",
        debug=True,
        apply_cwd_on_python=True,
    )
    print(by_password.execute("cd /tmp"))
    print(by_password.execute("python3 -c \"import os; print('cwd=', os.getcwd())\""))
    print(by_password.execute("python3 -c \"print(sum(range(1, 11)))\""))
