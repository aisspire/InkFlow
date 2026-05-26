from http.server import BaseHTTPRequestHandler, HTTPServer
import json


HOST = "127.0.0.1"
PORT = 8674


class GitHubWebhookHandler(BaseHTTPRequestHandler):
    """接收 GitHub Webhook 的最小 HTTP 服务端。"""

    def do_POST(self) -> None:
        """处理 GitHub 发来的 POST 请求。"""

        # HTTP 请求体长度由 Content-Length 告诉我们。
        # 这和 TCP demo 里自己设计 4 字节长度头不一样。
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length)

        event = self.headers.get("X-GitHub-Event", "")
        delivery = self.headers.get("X-GitHub-Delivery", "")
        content_type = self.headers.get("Content-Type", "")

        print("[webhook] 收到 GitHub 请求")
        print(f"- event: {event}")
        print(f"- delivery: {delivery}")
        print(f"- content_type: {content_type}")

        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            self.wfile.write("不是合法 JSON".encode("utf-8"))
            return

        action = payload.get("action")
        repository = payload.get("repository", {})
        repo_name = repository.get("full_name", "")

        print(f"- action: {action}")
        print(f"- repo: {repo_name}")

        # 如果你监听的是 Release 发布，通常是：
        # X-GitHub-Event: release
        # payload["action"]: published
        if event == "release" and action == "published":
            print("[webhook] 检测到 Release published 事件。")

        # 如果你监听的是 GitHub Package 发布，可能是：
        # X-GitHub-Event: registry_package
        # payload["action"]: published
        if event == "registry_package" and action == "published":
            print("[webhook] 检测到 Package published 事件。")

        self.send_response(200)
        self.end_headers()
        self.wfile.write("ok".encode("utf-8"))


def main() -> None:
    server = HTTPServer((HOST, PORT), GitHubWebhookHandler)
    print(f"[webhook] 正在监听 http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()