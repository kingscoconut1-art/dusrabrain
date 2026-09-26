import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler


class handler(BaseHTTPRequestHandler):

    def send_json(self, status, data):
        body = json.dumps(data).encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.send_json(
            200,
            {
                "name": "Dusra Brain",
                "status": "online",
                "anthropic_key_detected": bool(
                    os.environ.get("ANTHROPIC_API_KEY")
                )
            }
        )

    def do_POST(self):
        try:
            length = int(
                self.headers.get("Content-Length", 0)
            )

            body = self.rfile.read(length)

            data = json.loads(
                body or b"{}"
            )

            message = data.get(
                "message",
                ""
            ).strip()

            if not message:
                self.send_json(
                    400,
                    {
                        "error": "Message is required."
                    }
                )
                return

            api_key = os.environ.get(
                "ANTHROPIC_API_KEY"
            )

            if not api_key:
                self.send_json(
                    500,
                    {
                        "error": "ANTHROPIC_API_KEY is not configured."
                    }
                )
                return

            payload = {
                "model": "claude-sonnet-4-6",
                "max_tokens": 1000,
                "system": (
                    "You are Dusra Brain, a personal AI brain "
                    "and memory assistant. "
                    "Be helpful, concise and practical."
                ),
                "messages": [
                    {
                        "role": "user",
                        "content": message
                    }
                ]
            }

            request = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=json.dumps(
                    payload
                ).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01"
                },
                method="POST"
            )

            with urllib.request.urlopen(
                request,
                timeout=30
            ) as response:

                result = json.loads(
                    response.read().decode("utf-8")
                )

            text = ""

            for block in result.get(
                "content",
                []
            ):
                if block.get("type") == "text":
                    text += block.get(
                        "text",
                        ""
                    )

            self.send_json(
                200,
                {
                    "name": "Dusra Brain",
                    "response": text
                }
            )

        except Exception as e:

            self.send_json(
                500,
                {
                    "error": str(e)
                }
            )
