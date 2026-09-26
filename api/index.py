import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler


class handler(BaseHTTPRequestHandler):

    def send_json(self, status, data):
        body = json.dumps(data).encode("utf-8")

        self.send_response(status)
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )
        self.send_header(
            "Content-Length",
            str(len(body))
        )
        self.end_headers()

        self.wfile.write(body)

    def do_GET(self):
        self.send_json(
            200,
            {
                "name": "Dusra Brain",
                "status": "online",
                "groq_key_detected": bool(
                    os.environ.get("GROQ_API_KEY")
                )
            }
        )

    def do_POST(self):
        try:

            length = int(
                self.headers.get(
                    "Content-Length",
                    "0"
                )
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
                "GROQ_API_KEY"
            )

            if not api_key:
                self.send_json(
                    500,
                    {
                        "error": "GROQ_API_KEY is not configured."
                    }
                )
                return

            url = (
                "https://api.groq.com/openai/v1/"
                "chat/completions"
            )

            payload = {
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are Dusra Brain, "
                            "a personal AI brain and "
                            "memory assistant. "
                            "Be helpful, practical, "
                            "clear and concise."
                        )
                    },
                    {
                        "role": "user",
                        "content": message
                    }
                ],
                "max_tokens": 1000
            }

            request = urllib.request.Request(
                url,
                data=json.dumps(
                    payload
                ).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": (
                        "Bearer " + api_key
                    )
                },
                method="POST"
            )

            try:

                with urllib.request.urlopen(
                    request,
                    timeout=30
                ) as response:

                    response_body = (
                        response.read()
                        .decode("utf-8")
                    )

                    result = json.loads(
                        response_body
                    )

            except urllib.error.HTTPError as e:

                error_body = e.read().decode(
                    "utf-8",
                    errors="replace"
                )

                self.send_json(
                    500,
                    {
                        "error": "Groq request failed",
                        "status_code": e.code,
                        "details": error_body
                    }
                )

                return

            except Exception as e:

                self.send_json(
                    500,
                    {
                        "error": "Groq connection failed",
                        "details": str(e)
                    }
                )

                return

            choices = result.get(
                "choices",
                []
            )

            if not choices:

                self.send_json(
                    500,
                    {
                        "error": "Groq returned no choices.",
                        "details": result
                    }
                )

                return

            response_message = choices[0].get(
                "message",
                {}
            )

            text = response_message.get(
                "content",
                ""
            )

            if not text:

                self.send_json(
                    500,
                    {
                        "error": "Groq returned an empty response.",
                        "details": result
                    }
                )

                return

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
                    "error": "Server error",
                    "details": str(e)
                }
            )
