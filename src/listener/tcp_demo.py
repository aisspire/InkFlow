"""一个最小 TCP 监听 demo。

运行方式：

    $env:PYTHONPATH = "src"
    python -m listener.tcp_demo

这个文件先不接入 InkFlow 主流程，只专注学习 Python 如何监听端口、
接收客户端消息、处理文本并回复客户端。
"""

import socket


# HOST 表示监听哪个 IP。
# 127.0.0.1 只允许本机访问，适合学习 demo，不会暴露给局域网其它机器。
HOST = "127.0.0.1"

# PORT 表示监听哪个端口。
# 客户端必须连接同一个 IP 和端口，服务端才能收到消息。
PORT = 8674

# recv() 一次最多读取多少字节。
# 第一版 demo 每个连接只读取一次消息，所以先给一个足够学习用的大小。
BUFFER_SIZE = 4096

# 最大消息限制，防止恶意传输
MAX_MESSAGE_SIZE = 10 * 1024 * 1024  # 10 MB
#python -c "
# import socket; 
# s=socket.create_connection(('127.0.0.1', 8674)); 
# s.sendall('hello listener'.encode('utf-8')); 
# print(s.recv(4096).decode('utf-8')); 
# s.close()"
#


def recv_exact(connection: socket.socket,size: int) -> bytes:
    """精确接收指定字节数的数据。

    recv() 不保证一次读够 size 个字节，所以这里用 while 循环反复读。
    如果客户端中途断开，recv() 会返回空 bytes：b""。
    """
    chunks: list[bytes] = []
    remaining = size

    while remaining > 0:
        chunk = connection.recv(min(BUFFER_SIZE,remaining))
        if not chunk:
            raise ConnectionError("客户端在消息接收完成前断开连接")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)
    

def handle_message(message: str) -> str:
    """处理客户端发来的文本，并返回要回复的文本。

    这里故意把“消息处理”和“网络监听”拆开：
    - 网络部分负责收发 bytes。
    - 处理函数只面对普通 Python 字符串 str。

    这样以后想把处理逻辑换成 InkFlow 工作流时，只改这个函数会更清楚。
    """

    clean_message = message.strip()
    if not clean_message:
        return "服务端收到了一条空消息。"

    return f"服务端已处理：{clean_message}（字符数：{len(clean_message)}）"


def run_server(host: str = HOST, port: int = PORT) -> None:
    """启动 TCP 服务端，持续等待客户端连接。"""

    # AF_INET 表示使用 IPv4；SOCK_STREAM 表示使用 TCP。
    # with 会在函数退出时自动关闭 socket，类似“用完自动收拾现场”。
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        # 允许服务重启后更快重新绑定同一个端口，减少学习时反复启动的困扰。
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # bind() 把这个 socket 绑定到指定 IP 和端口。
        # 如果端口已经被占用，这里会抛出 OSError，终端会直接看到错误。
        server_socket.bind((host, port))

        # listen() 让 socket 从“普通 socket”变成“监听 socket”。
        server_socket.listen()

        print(f"[listener] 正在监听 {host}:{port}，按 Ctrl+C 停止。")

        while True:
            # accept() 会等待客户端连接；没有客户端时，程序会停在这一行。
            connection, address = server_socket.accept()

            # connection 是本次客户端连接对应的新 socket。
            # address 通常是 (客户端 IP, 客户端临时端口)。
            with connection:
                print(f"[listener] 客户端已连接：{address}")

                # 先读取 4 个字节的消息长度。
                header = recv_exact(connection, 4)

                # big 表示按“大端序”把 bytes 转成整数。
                message_size = int.from_bytes(header, byteorder="big")
                if message_size > MAX_MESSAGE_SIZE:
                    raise ValueError(f"消息过大：{message_size} bytes")
                # 再按长度读取完整正文。
                data = recv_exact(connection, message_size)

                message = data.decode("utf-8")
                print(f"[listener] 收到消息：{message}")

                response = handle_message(message)

                # 回复前要再把 Python 字符串编码回 bytes。
                connection.sendall(response.encode("utf-8"))


def main() -> None:
    """命令行入口。"""

    try:
        run_server()
    except KeyboardInterrupt:
        print("\n[listener] 服务端已停止。")


if __name__ == "__main__":
    main()


