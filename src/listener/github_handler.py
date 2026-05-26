import json
import tomllib
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any


HOST = "127.0.0.1"
PORT = 8674
CONFIG_PATH = Path("config.toml")


def load_config(config_path: Path = CONFIG_PATH) -> dict[str, Any]:
    """读取项目配置文件。

    Python 3.11 自带 tomllib，可以读取 TOML 格式。
    这里暂时只需要里面的 GitHub App webhook secret，
    但返回完整配置更方便后面继续扩展。
    """

    if not config_path.exists():
        return {}

    with config_path.open("rb") as config_file:
        return tomllib.load(config_file)


def load_github_secret(config: dict[str, Any]) -> str:
    """从配置中读取 GitHub App webhook secret。

    config.toml 中的结构是：

        [github.app]
        secret = "..."

    注意：secret 用来校验 GitHub webhook 签名，不能打印到终端。
    这一版先只加载它，下一步再接入签名校验。
    """

    github_config = config.get("github", {})
    if not isinstance(github_config, dict):
        return ""

    app_config = github_config.get("app", {})
    if not isinstance(app_config, dict):
        return ""

    return str(app_config.get("secret", "")).strip()


class GitHubWebhookHandler(BaseHTTPRequestHandler):
    """接收 GitHub Webhook 的最小 HTTP 服务端。"""

    github_secret = ""

    def do_POST(self) -> None:
        """处理 GitHub 发来的 POST 请求。

        这一版先不做业务处理，只把 GitHub 真正发来的请求原样打印出来。
        等确认 event、action、payload 结构后，再把处理逻辑接上。
        """

        # HTTP 请求体长度由 Content-Length 告诉我们。
        # 这和 TCP demo 里自己设计 4 字节长度头不一样。
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_text(400, "Content-Length 不是合法数字。")
            return

        raw_body = self.rfile.read(content_length)

        event = self.headers.get("X-GitHub-Event", "")
        delivery = self.headers.get("X-GitHub-Delivery", "")
        content_type = self.headers.get("Content-Type", "")

        print("[webhook] 收到 GitHub 请求")
        print(f"- path: {self.path}")
        print(f"- event: {event}")
        print(f"- delivery: {delivery}")
        print(f"- content_type: {content_type}")
        print(f"- content_length: {content_length}")
        print(f"- secret_loaded: {'yes' if self.github_secret else 'no'}")
        print("[webhook] Headers")
        for header_name, header_value in self.headers.items():
            print(f"- {header_name}: {header_value}")

        print("[webhook] Raw Body Bytes")
        print(repr(raw_body))

        print("[webhook] Raw Body Text")
        print(raw_body.decode("utf-8", errors="replace"))

        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as error:
            print(f"[webhook] JSON 解析失败：{error}")
            self._send_text(400, "不是合法 JSON")
            return

        print("[webhook] JSON Body")
        print(json.dumps(payload, ensure_ascii=False, indent=2))

        # 这里故意不处理 release/package 等业务事件。
        # 先返回 200，方便 GitHub App 的 delivery 页面显示本地服务已收到请求。
        self._send_text(200, "ok")

    def _send_text(self, status_code: int, message: str) -> None:
        """向客户端返回一段 UTF-8 文本。"""

        body = message.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    config = load_config()
    GitHubWebhookHandler.github_secret = load_github_secret(config)

    server = HTTPServer((HOST, PORT), GitHubWebhookHandler)
    print(f"[webhook] 正在监听 http://{HOST}:{PORT}")
    if GitHubWebhookHandler.github_secret:
        print("[webhook] 已从 config.toml 加载 GitHub webhook secret。")
    else:
        print("[webhook] 未从 config.toml 读取到 GitHub webhook secret。")
    server.serve_forever()


if __name__ == "__main__":
    main()
