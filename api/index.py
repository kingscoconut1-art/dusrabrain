import json
import os
import re
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

    def groq_request(
        self,
        api_key,
        messages,
        max_tokens=1000
    ):
        url = (
            "https://api.groq.com/openai/v1/"
            "chat/completions"
        )

        payload = {
            "model": "openai/gpt-oss-20b",
            "messages": messages,
            "max_tokens": max_tokens
        }

        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + api_key,
                "User-Agent": (
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

        with urllib.request.urlopen(
            request,
            timeout=30
        ) as response:

            response_body = (
                response.read()
                .decode("utf-8")
            )

            result = json.loads(response_body)

        choices = result.get("choices", [])

        if not choices:
            raise Exception(
                "Groq returned no choices."
            )

        response_message = choices[0].get(
            "message",
            {}
        )

        return response_message.get(
            "content",
            ""
        ).strip()

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
                        SELECT
                            memory,
                            category,
                            importance,
                            subject
                        FROM memories
                        WHERE user_id = %s
                        ORDER BY
                            importance DESC,
                            created_at DESC
                        LIMIT 30
                        """,
                        (user_id,)
                    )

                    rows = cursor.fetchall()

                    return [
                        {
                            "memory": row[0],
                            "category": row[1],
                            "importance": row[2],
                            "subject": row[3]
                        }
                        for row in rows
                    ]

        except Exception:
            return []

    def save_memory(
        self,
        user_id,
        memory,
        category,
        importance,
        subject
    ):

        database_url = self.get_database_url()

        if not database_url:
            return {
                "saved": False,
                "error": "Database URL not found."
            }

        try:

            with psycopg.connect(
                database_url,
                connect_timeout=5
            ) as connection:

                with connection.cursor() as cursor:

                    cursor.execute(
                        """
                        INSERT INTO memories
                        (
                            user_id,
                            memory,
                            category,
                            importance,
                            subject
                        )
                        VALUES
                        (
                            %s,
                            %s,
                            %s,
                            %s,
                            %s
                        )
                        """,
                        (
                            user_id,
                            memory,
                            category,
                            importance,
                            subject
                        )
                    )

                connection.commit()

            return {
                "saved": True,
                "error": None
            }

        except Exception as e:

            return {
                "saved": False,
                "error": str(e)
            }

    def clean_json_response(self, text):

        if not text:
            return ""

        text = text.strip()

        text = re.sub(
            r"^```json\s*",
            "",
            text,
            flags=re.IGNORECASE
        )

        text = re.sub(
            r"^```\s*",
            "",
            text
        )

        text = re.sub(
            r"\s*```$",
            "",
            text
        )

        return text.strip()

    def analyze_memory(
        self,
        api_key,
        message
    ):

        analysis_prompt = """
You are the memory extraction system for Dusra Brain.

Decide whether the user's message contains
LONG-TERM information about the user that
should be remembered.

Remember:

- Personal preferences
- Important personal facts
- Projects
- Businesses
- Goals
- Long-term plans
- Important people and relationships
- Work information
- Decisions
- User instructions
- Important facts explicitly stated by the user

Do NOT remember:

- Normal questions
- Greetings
- Casual conversation
- Temporary requests
- One-time calculations
- General knowledge questions
- Information unrelated to the user

The memory must contain ONLY facts supported
by the user's message.

Never invent additional personal information.

Also identify the main SUBJECT of the memory.

Examples of subjects:

Carbon Mandi
Dusra Brain
Evolve India
AIG India Textiles
Personal
Work
Family

Return ONLY valid JSON.

If the message should be remembered:

{
  "should_remember": true,
  "memory": "A clean factual statement describing exactly what the user said.",
  "category": "project",
  "subject": "Carbon Mandi",
  "importance": 8
}

If it should NOT be remembered:

{
  "should_remember": false,
  "memory": "",
  "category": "general",
  "subject": "general",
  "importance": 1
}

Allowed categories:

personal
preference
project
business
goal
relationship
work
decision
instruction
general

Importance must be an integer from 1 to 10.

User message:
""" + message

        try:

            result = self.groq_request(
                api_key,
                [
                    {
                        "role": "system",
                        "content": (
                            "You are the memory extraction "
                            "engine for Dusra Brain. "
                            "Return JSON only. "
                            "Never invent personal facts."
                        )
                    },
                    {
                        "role": "user",
                        "content": analysis_prompt
                    }
                ],
                max_tokens=350
            )

            result = self.clean_json_response(result)

            parsed = json.loads(result)

            should_remember = bool(
                parsed.get(
                    "should_remember",
                    False
                )
            )

            memory = str(
                parsed.get(
                    "memory",
                    ""
                )
            ).strip()

            category = str(
                parsed.get(
                    "category",
                    "general"
                )
            ).strip().lower()

            subject = str(
                parsed.get(
                    "subject",
                    "general"
                )
            ).strip()

            importance = parsed.get(
                "importance",
                5
            )

            try:
                importance = int(importance)
            except Exception:
                importance = 5

            importance = max(
                1,
                min(
                    10,
                    importance
                )
            )

            allowed_categories = {
                "personal",
                "preference",
                "project",
                "business",
                "goal",
                "relationship",
                "work",
                "decision",
                "instruction",
                "general"
            }

            if category not in allowed_categories:
                category = "general"

            if not subject:
                subject = "general"

            if not should_remember:

                return {
                    "should_remember": False,
                    "memory": "",
                    "category": "general",
                    "subject": "general",
                    "importance": 1,
                    "error": None
                }

            if not memory:

                return {
                    "should_remember": False,
                    "memory": "",
                    "category": "general",
                    "subject": "general",
                    "importance": 1,
                    "error": (
                        "Memory extraction returned "
                        "no memory text."
                    )
                }

            return {
                "should_remember": True,
                "memory": memory,
                "category": category,
                "subject": subject,
                "importance": importance,
                "error": None
            }

        except Exception as e:

            return {
                "should_remember": False,
                "memory": "",
                "category": "general",
                "subject": "general",
                "importance": 1,
                "error": str(e)
            }

    def do_GET(self):

        self.send_json(
            200,
            {
                "name": "Dusra Brain",
                "status": "online",
                "groq_key_detected": bool(
                    os.environ.get(
                        "GROQ_API_KEY"
                    )
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
                        "error": (
                            "Message is required."
                        )
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

            memories = self.get_memories(user_id)

            memory_text = ""

            if memories:

                memory_lines = []

                for item in reversed(memories):

                    memory_lines.append(
                        "- Subject: "
                        + item["subject"]
                        + " | Category: "
                        + item["category"]
                        + " | Importance: "
                        + str(item["importance"])
                        + " | Memory: "
                        + item["memory"]
                    )

                memory_text = (
                    "\n\nUSER'S LONG-TERM MEMORIES:\n"
                    + "\n".join(memory_lines)
                )

            system_prompt = (
                "You are Dusra Brain, "
                "a personal AI brain and memory assistant. "

                "You help the user think, remember, "
                "plan and execute. "

                "Be helpful, practical, clear and concise. "

                "IMPORTANT MEMORY RULES: "

                "The USER'S LONG-TERM MEMORIES section "
                "contains facts that have actually been "
                "stored about the user. "

                "When answering questions about the user's "
                "personal life, projects, businesses, "
                "preferences, goals, relationships, work, "
                "or history, use only information supported "
                "by the stored memories or information "
                "explicitly provided in the current message. "

                "NEVER invent, assume, or expand personal "
                "facts that are not supported by the stored "
                "memories or the current conversation. "

                "Do not turn a general description into "
                "a personal fact. "

                "Do not claim that the user owns, operates, "
                "plans, wants, or has achieved something "
                "unless that information is actually "
                "supported by memory or the current message. "

                "If the stored memories do not contain "
                "enough information to answer a personal "
                "question, say that you don't have enough "
                "stored information and ask the user if "
                "they want to provide more information. "

                "For general knowledge questions, you may "
                "answer normally, but clearly distinguish "
                "general knowledge from the user's personal "
                "information. "

                "Never mention the internal memory system "
                "unless the user asks."

                + memory_text
            )

            try:

                text = self.groq_request(
                    api_key,
                    [
                        {
                            "role": "system",
                            "content": system_prompt
                        },
                        {
                            "role": "user",
                            "content": message
                        }
                    ],
                    max_tokens=1000
                )

            except urllib.error.HTTPError as e:

                error_body = (
                    e.read()
                    .decode(
                        "utf-8",
                        errors="replace"
                    )
                )

                self.send_json(
                    500,
                    {
                        "error": (
                            "Groq request failed"
                        ),
                        "status_code": e.code,
                        "details": error_body
                    }
                )

                return

            except Exception as e:

                self.send_json(
                    500,
                    {
                        "error": (
                            "Groq connection failed"
                        ),
                        "details": str(e)
                    }
                )

                return

            if not text:

                self.send_json(
                    500,
                    {
                        "error": (
                            "Groq returned an empty response."
                        )
                    }
                )

                return

            memory_analysis = self.analyze_memory(
                api_key,
                message
            )

            memory_saved = False
            memory_save_error = None

            if memory_analysis[
                "should_remember"
            ]:

                save_result = self.save_memory(
                    user_id,
                    memory_analysis["memory"],
                    memory_analysis["category"],
                    memory_analysis["importance"],
                    memory_analysis["subject"]
                )

                memory_saved = save_result["saved"]
                memory_save_error = save_result["error"]

            self.send_json(
                200,
                {
                    "name": "Dusra Brain",
                    "response": text,
                    "memory_saved": memory_saved,
                    "memory_subject": (
                        memory_analysis["subject"]
                    ),
                    "memory_category": (
                        memory_analysis["category"]
                    ),
                    "memory_importance": (
                        memory_analysis["importance"]
                    ),
                    "memory_error": (
                        memory_analysis.get("error")
                    ),
                    "memory_save_error": memory_save_error,
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
