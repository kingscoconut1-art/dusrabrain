import json
import os
import urllib.request
import urllib.error
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
                "gemini_key_detected": bool(
                    os.environ.get("GEMINI_API_KEY")
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
                "GEMINI_API_KEY"
            )

            if not api_key:
                self.send_json(
                    500,
                    {
                        "error": "GEMINI_API_KEY is not configured."
                    }
                )
                return

            url = (
                "https://generativelanguage.googleapis.com/"
                "v1beta/models/gemini-2.5-flash:generateContent"
                "?key=" + api_key
            )

            payload = {
                "contents": [
                    {
                        "parts": [
                            {
                                "text": message
                            }
                        ]
                    }
                ],
                "systemInstruction": {
                    "parts": [
                        {
                            "text": (
                                "You are Dusra Brain, a personal AI "
                                "brain and memory assistant. "
                                "Be helpful, concise and practical."
                            )
                        }
                    ]
                }
            }

            request = urllib.request.Request(
                url,
                data=json.dumps(
                    payload
                ).encode("utf-8"),
                headers={
                    "Content-Type": "application/json"
                },
                method="POST"
            )

            try:
                with urllib.request.urlopen(
                    request,
                    timeout=30
                ) as response:

                    result = json.loads(
                        response.read().decode("utf-8")
                    )

            except urllib.error.HTTPError as e:

                error_body = e.read().decode(
                    "utf-8",
                    errors="replace"
                )

                try:
                    error_data = json.loads(
                        error_body
                    )
                except Exception:
                    error_data = {
                        "raw": error_body
                    }

                self.send_json(
                    e.code,
                    {
                        "error": "Gemini API error",
                        "gemini_error": error_data
                    }
                )
                return

            text = ""

            candidates = result.get(
                "candidates",
                []
            )

            if candidates:

                parts = candidates[0].get(
                    "content",
                    {}
                ).get(
                    "parts",
                    []
                )

                for part in parts:

                    if "text" in part:

                        text += part["text"]

            if not text:
                text = "No response received from Gemini."

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
                    "error": str(e),
                    "type": type(e).__name__
                }
            )
