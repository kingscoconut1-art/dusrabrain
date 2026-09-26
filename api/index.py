import json
import os
import urllib.error
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
                "gemini_key_detected": bool(
                    os.environ.get("GEMINI_API_KEY")
                )
            }
        )

    def do_POST(self):
        try:
            # Read request
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

            # Validate message
            if not message:
                self.send_json(
                    400,
                    {
                        "error": "Message is required."
                    }
                )
                return

            # Get Gemini API key
            api_key = os.environ.get(
                "GEMINI_API_KEY"
            )

            if not api_key:
                self.send_json(
                    500,
                    {
                        "error": (
                            "GEMINI_API_KEY is not configured."
                        )
                    }
                )
                return

            # Gemini API endpoint
            url = (
                "https://generativelanguage.googleapis.com/"
                "v1beta/models/gemini-3.5-flash:"
                "generateContent"
            )

            # Gemini request
            payload = {
                "systemInstruction": {
                    "parts": [
                        {
                            "text": (
                                "You are Dusra Brain, "
                                "a personal AI brain and "
                                "memory assistant. "
                                "Be helpful, practical, "
                                "clear and concise."
                            )
                        }
                    ]
                },
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {
                                "text": message
                            }
                        ]
                    }
                ]
            }

            request = urllib.request.Request(
                url,
                data=json.dumps(
                    payload
                ).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": api_key
                },
                method="POST"
            )

            # Call Gemini
            try:

                with urllib.request.urlopen(
                    request,
                    timeout=30
                ) as response:

                    result = json.loads(
                        response.read().decode(
                            "utf-8"
                        )
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

                # Extract Google's actual error message
                error_message = (
                    "Gemini API error"
                )

                try:

                    error_message = (
                        error_data
                        .get("error", {})
                        .get(
                            "message",
                            error_message
                        )
                    )

                except Exception:
                    pass

                self.send_json(
                    e.code,
                    {
                        "error": error_message,
                        "gemini_error": error_data
                    }
                )

                return

            # Extract response text
            text = ""

            candidates = result.get(
                "candidates",
                []
            )

            if candidates:

                content = candidates[0].get(
                    "content",
                    {}
                )

                parts = content.get(
                    "parts",
                    []
                )

                for part in parts:

                    if "text" in part:

                        text += part["text"]

            # Handle empty response
            if not text:

                self.send_json(
                    500,
                    {
                        "error": (
                            "Gemini returned an empty response."
                        ),
                        "gemini_response": result
                    }
                )

                return

            # Successful response
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
