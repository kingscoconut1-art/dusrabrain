import json
import os
from http.server import BaseHTTPRequestHandler

from anthropic import Anthropic


class handler(BaseHTTPRequestHandler):

    def _send_json(self, status, data):
        body = json.dumps(data).encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send_json(
            200,
            {
                "name": "Dusra Brain",
                "status": "online",
                "message": "Your personal AI brain is ready."
            }
        )

    def do_POST(self):
        try:
            content_length = int(
                self.headers.get("Content-Length", 0)
            )

            body = self.rfile.read(content_length)

            data = json.loads(body or b"{}")

            message = data.get("message", "").strip()

            if not message:
                self._send_json(
                    400,
                    {"error": "Message is required."}
                )
                return

            api_key = os.environ.get("ANTHROPIC_API_KEY")

            if not api_key:
                self._send_json(
                    500,
                    {"error": "ANTHROPIC_API_KEY is not configured."}
                )
                return

            client = Anthropic(api_key=api_key)

            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1000,
                system=(
                    "You are Dusra Brain, a personal AI brain and memory "
                    "assistant. Be helpful, concise and practical."
                ),
                messages=[
                    {
                        "role": "user",
                        "content": message
                    }
                ],
            )

            text = ""

            for block in response.content:
                if hasattr(block, "text"):
                    text += block.text

            self._send_json(
                200,
                {
                    "name": "Dusra Brain",
                    "response": text
                }
            )

        except Exception as e:
            self._send_json(
                500,
                {
                    "error": str(e)
                }
            )
