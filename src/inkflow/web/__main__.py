"""在 Windows openai_agents 环境中运行：python -m inkflow.web。"""

import argparse
import socket
from pathlib import Path

import uvicorn

from inkflow.main import configure_utf8_stdio
from inkflow.web.app import create_app


HOST = "127.0.0.1"
DEFAULT_PORT = 18765


def port_number(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("端口必须是 0 到 65535 之间的整数") from None
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError("端口必须是 0 到 65535 之间的整数")
    return port


def bind_socket(port: int | None) -> socket.socket:
    """默认先用固定端口；不可用时用 0 让操作系统分配空闲端口。"""
    requested_port = DEFAULT_PORT if port is None else port
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # Windows 使用独占绑定，避免与已有服务共享端口；其它系统允许正常重启复用。
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((HOST, requested_port))
    except OSError:
        sock.close()
        # 仅默认模式回退。显式 --port N 失败时保留错误，避免开发代理连接错服务。
        if port is None:
            return bind_socket(0)
        raise
    return sock


def main():
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="启动 InkFlow 本地中文工作台")
    parser.add_argument("--config", default="config.toml", help="工作流配置路径")
    parser.add_argument(
        "--port", type=port_number,
        help=f"本机端口；默认优先 {DEFAULT_PORT}，不可用时自动分配；0 表示直接自动分配",
    )
    parser.add_argument("--workspace", type=Path, help="授权编辑的文章目录，默认配置文件所在目录")
    args = parser.parse_args()
    try:
        sock = bind_socket(args.port)
    except OSError as exc:
        hint = "请检查系统是否允许本机网络监听。"
        if args.port not in (None, 0):
            hint = "可使用 --port 0 自动选择可用端口。"
        parser.exit(1, f"无法绑定本机端口：{exc}\n{hint}\n")

    # 持续持有绑定成功的 socket 并交给 Uvicorn，避免探测后释放端口又被其它程序抢占。
    with sock:
        actual_port = sock.getsockname()[1]
        if args.port is None and actual_port != DEFAULT_PORT:
            print(f"首选端口 {DEFAULT_PORT} 不可用，已自动选择端口 {actual_port}。", flush=True)
        config = uvicorn.Config(
            create_app(Path(args.config), workspace_root=args.workspace),
            host=HOST, port=actual_port, workers=1, access_log=False,
        )
        print(f"InkFlow 访问地址：http://{HOST}:{actual_port}（按 Ctrl+C 停止）", flush=True)
        server = uvicorn.Server(config)
        server.run(sockets=[sock])
        if not server.started:
            parser.exit(1)


if __name__ == "__main__":
    main()
