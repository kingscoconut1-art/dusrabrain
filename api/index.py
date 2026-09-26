import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler

import psycopg


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

    def get_database_url(self):
        return (
            os.environ.get("STORAGE_URL")
            or os.environ.get("DATABASE_URL")
        )

    def get_memories(self, user_id):
        database_url = self.get_database_url()

        if not database_url:
            return []

        try:
            with psycopg.connect(
                database_url,
                connect_timeout=5
            ) as connection:

                with connection.cursor() as cursor:

                    cursor.execute(
                        """
                        SELECT memory
                        FROM memories
                        WHERE user_id = %s
                        ORDER BY created_at DESC
                        LIMIT 10
                        """,
                        (user_id,)
                    )

                    rows = cursor.fetchall()

                    return [
                        row[0]
                        for row in rows
                    ]

        except Exception:
            return []

    def save_memory(self, user_id, memory):
        database_url = self.get_database_url()

        if not database_url:
            return False

        try:
            with psycopg.connect(
                database_url,
                connect_timeout=5
            ) as connection:

                with connection.cursor() as cursor:

                    cursor.execute(
                        """
                        INSERT INTO memories
                        (user_id, memory)
                        VALUES (%s, %s)
                        """,
                        (
                            user_id,
                            memory
                        )
                    )

                connection.commit()

            return True

        except Exception:
            return False

    def do_GET(self):

        self.send_json(
            200,
            {
                "name": "Dusra Brain",
                "status": "online",
                "groq_key_detected": bool(
                    os.environ.get("GROQ_API_KEY")
                ),
                "database_detected": bool(
                    self.get_database_url()
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

            user_id = data.get(
                "user_id",
                "default_user"
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
                        "error": (
                            "GROQ_API_KEY is not configured."
                        )
                    }
                )

                return

            memories = self.get_memories(
                user_id
            )

            memory_text = ""

            if memories:

                memory_text = (
                    "\n\nRelevant memories "
                    "from previous conversations:\n"
                    + "\n".join(
                        "- " + memory
                        for memory in reversed(memories)
                    )
                )

            url = (
                "https://api.groq.com/openai/v1/"
                "chat/completions"
            )

            system_prompt = (
                "You are Dusra Brain, "
                "a personal AI brain and "
                "memory assistant. "
                "Be helpful, practical, "
                "clear and concise. "
                "Use the user's previous "
                "memories when relevant."
                + memory_text
            )

            payload = {

                "model": "openai/gpt-oss-20b",

                "messages": [

                    {
                        "role": "system",
                        "content": system_prompt
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

                    "Content-Type":
                        "application/json",

                    "Authorization":
                        "Bearer " + api_key,

                    "User-Agent":
                        (
                            "Mozilla/5.0 "
                            "(Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 "
                            "(KHTML, like Gecko) "
                            "Chrome/131.0.0.0 "
                            "Safari/537.36"
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
                        "error": (
                            "Groq returned no choices."
                        ),
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
                        "error": (
                            "Groq returned an empty response."
                        ),
                        "details": result
                    }
                )

                return

            memory_saved = self.save_memory(
                user_id,
                message
            )

            self.send_json(
                200,
                {
                    "name": "Dusra Brain",
                    "response": text,
                    "memory_saved": memory_saved,
                    "memories_used": len(memories)
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
