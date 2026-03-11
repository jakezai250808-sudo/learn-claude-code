from util.msg import Msg
from util.api.by_token.send_msg import send_msg
from util.api.by_token.api import recv_next_msg

from remote_ssh_executor import get_remote_executor
import hashlib
from pathlib import Path


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


def _build_executor(session_id: str):
    """按 session_id 复用 executor，实现同会话命令共享一个远端 screen。"""
    return get_remote_executor(
        session_id,
        host="10.173.21.118",
        user="nvidia",  # 替换为实际用户名
        password="",  # 替换为实际密码
        debug=True,
        enable_connection_reuse=True,
        control_path=_control_path_for_session(session_id),
        screen_session_name=f"plugin_{hashlib.sha1(session_id.encode('utf-8')).hexdigest()[:10]}",
    )


def handle(msg: Msg):
    print("-------------------")
    print(msg.params)

    if msg.is_first_input():
        send_msg(
            "当前支持命令：\n"
            "1) cd <目录>  例如: cd /home/nvidia\n"
            "2) python3 ... 例如: python3 -c \"import os; print(os.getcwd())\"\n"
            "说明：同一个会话会复用同一个 executor，并在远端复用同一个 screen，会话内的 cd 目录会自然延续。",
            msg.receiver,
        )
        recv_next_msg(msg)
        return

    session_id = _build_session_id(msg)
    executor = _build_executor(session_id)

    command = (msg.params or '').strip()
    print(f"[plugin] session_id={session_id}, control_path={_control_path_for_session(session_id)}")

    result = executor.execute(command)

    send_msg(result, msg.receiver)
    recv_next_msg(msg)


if __name__ == '__main__':
    from util.debug.debug import debug_handle

    user_input = 'python3 -c "print(123)"'
    debug_handle(handle, user_input)
