from util.msg import Msg
from util.api.by_token.send_msg import send_msg
from util.api.by_token.api import recv_next_msg

from remote_ssh_executor import get_remote_executor


def _build_session_id(msg: Msg) -> str:
    """构造稳定会话键，避免只用 sender 导致无法复用 executor。"""
    for key in ("session_id", "conversation_id", "chat_id", "thread_id"):
        value = getattr(msg, key, None)
        if value:
            return f"{key}:{value}"

    sender = getattr(msg, "sender", None) or "unknown_sender"
    receiver = getattr(msg, "receiver", None) or "unknown_receiver"
    return f"sender_receiver:{sender}->{receiver}"


def _get_executor(msg: Msg):
    """按稳定会话键复用 executor，确保 `cd` 后目录可延续。"""
    session_id = _build_session_id(msg)
    executor = get_remote_executor(
        session_id=session_id,
        host="10.173.21.118",
        user="nvidia",  # 替换为实际用户名
        password="",  # 替换为实际密码
        debug=True,
    )
    print(f"[plugin] session_id={session_id}, executor_id={id(executor)}")
    return executor


def handle(msg: Msg):
    print("-------------------")
    print(msg.params)

    # 首次进入：只做引导，不执行命令
    if msg.is_first_input():
        send_msg(
            "当前支持命令：\n"
            "1) cd <目录>  例如: cd /home/nvidia\n"
            "2) python3 ... 例如: python3 -c \"import os; print(os.getcwd())\"\n"
            "请直接输入命令。",
            msg.receiver,
        )
        recv_next_msg(msg)
        return

    # 后续消息：执行远程命令并返回结果
    executor = _get_executor(msg)
    result = executor.execute(msg.params)
    send_msg(result, msg.receiver)
    recv_next_msg(msg)


if __name__ == '__main__':
    from util.debug.debug import debug_handle

    # 本地调试示例
    user_input = 'python3 -c "print(123)"'
    debug_handle(handle, user_input)
