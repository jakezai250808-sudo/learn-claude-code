from util.msg import Msg
from util.api.by_token.send_msg import send_msg
from util.api.by_token.api import recv_next_msg

from remote_ssh_executor import get_remote_executor
from remote_ssh_executor import RemoteCommandExecutor

import json
import hashlib
from pathlib import Path


_STATE_FILE = Path('.remote_cwd_state.json')


def _control_path_for_session(session_id: str) -> str:
    digest = hashlib.sha1(session_id.encode('utf-8')).hexdigest()[:16]
    base_dir = Path('/tmp/remote_ssh_mux')
    base_dir.mkdir(parents=True, exist_ok=True)
    return str(base_dir / f'plugin_mux_{digest}')


def _build_session_id(msg: Msg) -> str:
    for key in ("session_id", "conversation_id", "chat_id", "thread_id"):
        value = getattr(msg, key, None)
        if value:
            return f"{key}:{value}"

    sender = getattr(msg, "sender", None) or "unknown_sender"
    receiver = getattr(msg, "receiver", None) or "unknown_receiver"
    return f"sender_receiver:{sender}->{receiver}"


def _load_state() -> dict:
    if not _STATE_FILE.exists():
        return {}
    try:
        return json.loads(_STATE_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    _STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )


def _get_saved_cwd(session_id: str) -> str | None:
    return _load_state().get(session_id)


def _set_saved_cwd(session_id: str, cwd: str) -> None:
    state = _load_state()
    state[session_id] = cwd
    _save_state(state)


def _build_executor(session_id: str) -> RemoteCommandExecutor:
    """无状态 handle 场景：每次调用新建 executor。"""
    return RemoteCommandExecutor(
        host="10.173.21.118",
        user="nvidia",  # 替换为实际用户名
        password="",  # 替换为实际密码
        debug=True,
        apply_cwd_on_python=False,
        enable_connection_reuse=True,
        control_path=_control_path_for_session(session_id),
    )


def handle(msg: Msg):
    print("-------------------")
    print(msg.params)

    if msg.is_first_input():
        send_msg(
            "当前支持命令：\n"
            "1) cd <目录>  例如: cd /home/nvidia\n"
            "2) python3 ... 例如: python3 -c \"import os; print(os.getcwd())\"\n"
            "说明：handle 无状态调用时，会把每个会话最近一次 cd 的目录持久化到本地文件；python3 不做命令拼接；若有历史目录，会先单独执行一次 cd，再单独执行 python3。",
            msg.receiver,
        )
        recv_next_msg(msg)
        return

    session_id = _build_session_id(msg)
    executor = _build_executor(session_id)

    command = (msg.params or '').strip()
    print(f"[plugin] session_id={session_id}, control_path={_control_path_for_session(session_id)}")

    if command.startswith('python3'):
        saved_cwd = _get_saved_cwd(session_id)
        if saved_cwd:
            print(f"[plugin] replay cwd for {session_id}: cd {saved_cwd}")
            cd_result = executor.execute(f"cd {saved_cwd}")
            print(f"[plugin] replay cd result: {cd_result}")

    result = executor.execute(command)

    if command.startswith('cd ') and result.startswith('当前远程目录：'):
        new_cwd = result.replace('当前远程目录：', '', 1).strip()
        if new_cwd:
            _set_saved_cwd(session_id, new_cwd)
            print(f"[plugin] save cwd for {session_id}: {new_cwd}")

    send_msg(result, msg.receiver)
    recv_next_msg(msg)


if __name__ == '__main__':
    from util.debug.debug import debug_handle

    user_input = 'python3 -c "print(123)"'
    debug_handle(handle, user_input)
