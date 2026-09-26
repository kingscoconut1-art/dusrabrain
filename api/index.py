import json
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
                "test": "API is working"
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
            )

            self.send_json(
                200,
                {
                    "name": "Dusra Brain",
                    "status": "success",
                    "received_message": message,
                    "response": (
                        "Dusra Brain API is working "
                        "correctly. Your message was received."
                    )
                }
            )

        except Exception as e:

            self.send_json(
                500,
                {
                    "status": "error",
                    "error": str(e)
                }
            )
