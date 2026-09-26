import json
import os
import re
import urllib.request
import urllib.error
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler

import psycopg


# ============================================================
# RESPONSE
# ============================================================

def send_json(handler, data, status=200):
    body = json.dumps(
        data,
        ensure_ascii=False,
        default=str
    ).encode("utf-8")

    handler.send_response(status)

    handler.send_header(
        "Content-Type",
        "application/json; charset=utf-8"
    )

    handler.send_header(
        "Access-Control-Allow-Origin",
        "*"
    )

    handler.send_header(
        "Access-Control-Allow-Methods",
        "GET, POST, PUT, DELETE, OPTIONS"
    )

    handler.send_header(
        "Access-Control-Allow-Headers",
        "Content-Type"
    )

    handler.send_header(
        "Content-Length",
        str(len(body))
    )

    handler.end_headers()

    handler.wfile.write(body)


# ============================================================
# DATABASE
# ============================================================

def get_database_url():
    names = [
        "DATABASE_URL",
        "POSTGRES_URL",
        "POSTGRES_PRISMA_URL",
        "POSTGRES_URL_NON_POOLING",
        "STORAGE_POSTGRES_URL",
        "STORAGE_DATABASE_URL"
    ]

    for name in names:
        value = os.environ.get(name)

        if value:
            return value

    return None


# ============================================================
# GROQ
# ============================================================

def groq_request(
    messages,
    temperature=0.2,
    max_tokens=1200
):
    api_key = os.environ.get("GROQ_API_KEY")

    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY is not configured."
        )

    url = (
        "https://api.groq.com/openai/v1/"
        "chat/completions"
    )

    payload = {
        "model": "openai/gpt-oss-120b",
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens
    }

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key,
            "User-Agent": "Mozilla/5.0"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=60
        ) as response:

            response_body = response.read()

            data = json.loads(
                response_body.decode("utf-8")
            )

            return data["choices"][0]["message"]["content"]

    except urllib.error.HTTPError as error:
        error_body = error.read().decode(
            "utf-8",
            errors="replace"
        )

        raise RuntimeError(
            f"Groq HTTP {error.code}: {error_body}"
        )

    except urllib.error.URLError as error:
        raise RuntimeError(
            f"Groq connection error: {error}"
        )


# ============================================================
# CONVERSATIONS
# ============================================================

def save_conversation(
    user_id,
    role,
    message
):
    db_url = get_database_url()

    if not db_url:
        return

    with psycopg.connect(db_url) as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                INSERT INTO conversations
                (
                    user_id,
                    role,
                    message
                )
                VALUES
                (
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    user_id,
                    role,
                    message
                )
            )

        conn.commit()


def get_conversation_history(
    user_id,
    limit=20
):
    db_url = get_database_url()

    if not db_url:
        return []

    with psycopg.connect(db_url) as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    role,
                    message,
                    created_at
                FROM conversations
                WHERE user_id = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (
                    user_id,
                    limit
                )
            )

            rows = cur.fetchall()

    rows.reverse()

    return [
        {
            "role": row[0],
            "message": row[1],
            "created_at": (
                row[2].isoformat()
                if row[2]
                else None
            )
        }
        for row in rows
    ]


# ============================================================
# GET MEMORIES
# ============================================================

def get_memories(
    user_id,
    message=""
):
    db_url = get_database_url()

    if not db_url:
        return []

    with psycopg.connect(db_url) as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    id,
                    user_id,
                    memory,
                    created_at,
                    category,
                    importance,
                    subject,
                    memory_key
                FROM memories
                WHERE user_id = %s
                ORDER BY
                    importance DESC,
                    created_at DESC
                LIMIT 50
                """,
                (user_id,)
            )

            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "user_id": row[1],
            "memory": row[2],
            "created_at": (
                row[3].isoformat()
                if row[3]
                else None
            ),
            "category": row[4],
            "importance": row[5],
            "subject": row[6],
            "memory_key": row[7]
        }
        for row in rows
    ]


def get_subject_memories(
    user_id,
    subject
):
    db_url = get_database_url()

    if not db_url:
        return []

    with psycopg.connect(db_url) as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    id,
                    memory,
                    category,
                    importance,
                    subject
                FROM memories
                WHERE
                    user_id = %s
                    AND LOWER(subject) = LOWER(%s)
                ORDER BY
                    importance DESC,
                    created_at DESC
                LIMIT 50
                """,
                (
                    user_id,
                    subject
                )
            )

            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "memory": row[1],
            "category": row[2],
            "importance": row[3],
            "subject": row[4]
        }
        for row in rows
    ]


# ============================================================
# MEMORY KEY
# ============================================================

def make_memory_key(
    subject,
    category,
    memory
):
    text = (
        f"{subject}|"
        f"{category}|"
        f"{memory}"
    ).lower().strip()

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text


# ============================================================
# CLEAN JSON
# ============================================================

def clean_json_response(text):
    text = text.strip()

    if text.startswith("```"):
        text = re.sub(
            r"^```(?:json)?",
            "",
            text,
            flags=re.IGNORECASE
        )

        text = re.sub(
            r"```$",
            "",
            text
        )

    return text.strip()


# ============================================================
# SEMANTIC DUPLICATE
# ============================================================

def find_semantic_duplicate(
    user_id,
    new_memory,
    subject
):
    existing = get_subject_memories(
        user_id,
        subject
    )

    if not existing:
        return None

    comparison_items = [
        {
            "id": item["id"],
            "memory": item["memory"]
        }
        for item in existing
    ]

    prompt = f"""
Check whether the new memory has substantially
the same meaning as one of the existing memories.

New memory:
{new_memory}

Existing memories:
{json.dumps(
    comparison_items,
    ensure_ascii=False
)}

Return ONLY valid JSON.

If duplicate:
{{
    "duplicate": true,
    "id": 123
}}

If not duplicate:
{{
    "duplicate": false,
    "id": null
}}
"""

    try:
        result = groq_request(
            [
                {
                    "role": "system",
                    "content":
                        "You compare memories for semantic duplicates."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0,
            max_tokens=200
        )

        data = json.loads(
            clean_json_response(result)
        )

        if data.get("duplicate"):
            return data.get("id")

    except Exception:
        return None

    return None


# ============================================================
# SAVE MEMORY
# ============================================================

def save_memory(
    user_id,
    memory,
    category,
    importance,
    subject
):
    db_url = get_database_url()

    if not db_url:
        return None, "Database not configured."

    memory_key = make_memory_key(
        subject,
        category,
        memory
    )

    duplicate_id = find_semantic_duplicate(
        user_id,
        memory,
        subject
    )

    with psycopg.connect(db_url) as conn:

        with conn.cursor() as cur:

            if duplicate_id:

                cur.execute(
                    """
                    UPDATE memories
                    SET
                        memory = %s,
                        category = %s,
                        importance = %s,
                        subject = %s,
                        memory_key = %s
                    WHERE id = %s
                    RETURNING id
                    """,
                    (
                        memory,
                        category,
                        importance,
                        subject,
                        memory_key,
                        duplicate_id
                    )
                )

                row = cur.fetchone()

                conn.commit()

                return (
                    row[0] if row else duplicate_id,
                    "updated_duplicate"
                )

            cur.execute(
                """
                SELECT id
                FROM memories
                WHERE
                    user_id = %s
                    AND memory_key = %s
                LIMIT 1
                """,
                (
                    user_id,
                    memory_key
                )
            )

            existing = cur.fetchone()

            if existing:

                cur.execute(
                    """
                    UPDATE memories
                    SET
                        memory = %s,
                        category = %s,
                        importance = %s,
                        subject = %s
                    WHERE id = %s
                    RETURNING id
                    """,
                    (
                        memory,
                        category,
                        importance,
                        subject,
                        existing[0]
                    )
                )

                row = cur.fetchone()

                conn.commit()

                return (
                    row[0] if row else existing[0],
                    "updated_duplicate"
                )

            cur.execute(
                """
                INSERT INTO memories
                (
                    user_id,
                    memory,
                    category,
                    importance,
                    subject,
                    memory_key
                )
                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                RETURNING id
                """,
                (
                    user_id,
                    memory,
                    category,
                    importance,
                    subject,
                    memory_key
                )
            )

            row = cur.fetchone()

        conn.commit()

    return (
        row[0] if row else None,
        "inserted"
    )


# ============================================================
# MEMORY ANALYSIS
# ============================================================

def analyze_memory(message):

    prompt = f"""
Analyze the user's message and decide whether
it contains information worth remembering.

User message:
{message}

Remember useful long-term facts such as:

- personal facts
- preferences
- projects
- businesses
- goals
- work information
- important decisions
- instructions
- relationships
- long-term plans

Do NOT remember:

- greetings
- casual conversation
- simple questions
- temporary information
- ordinary requests
- generic statements

If a specific project, business, person,
organization or topic is mentioned, use that
specific name as the subject.

Known subjects:

- Carbon Mandi
- Evolve India
- Dusra Brain

Do not invent subjects.

Return ONLY valid JSON.

If it should NOT be remembered:

{{
    "remember": false
}}

If it SHOULD be remembered:

{{
    "remember": true,
    "memory": "clean factual memory",
    "category": "personal|preference|project|business|goal|relationship|work|decision|instruction|general",
    "importance": 5,
    "subject": "specific subject"
}}

Importance:

1-3 = low
4-6 = medium
7-8 = high
9-10 = extremely important
"""

    result = groq_request(
        [
            {
                "role": "system",
                "content":
                    "You are a memory extraction system."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        temperature=0,
        max_tokens=500
    )

    return json.loads(
        clean_json_response(result)
    )


# ============================================================
# SUBJECT NORMALIZATION
# ============================================================

def normalize_subject(
    subject,
    message
):
    known_subjects = {
        "carbon mandi": "Carbon Mandi",
        "evolve india": "Evolve India",
        "dusra brain": "Dusra Brain"
    }

    subject_text = (
        subject or ""
    ).strip()

    message_lower = (
        message or ""
    ).lower()

    for key, value in known_subjects.items():

        if key in message_lower:
            return value

    for key, value in known_subjects.items():

        if key == subject_text.lower():
            return value

    return subject_text or "general"


# ============================================================
# HTTP HANDLER
# ============================================================

class handler(BaseHTTPRequestHandler):

    # ========================================================
    # OPTIONS
    # ========================================================

    def do_OPTIONS(self):

        self.send_response(204)

        self.send_header(
            "Access-Control-Allow-Origin",
            "*"
        )

        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, POST, PUT, DELETE, OPTIONS"
        )

        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type"
        )

        self.end_headers()


    # ========================================================
    # GET
    # ========================================================

    def do_GET(self):

        try:

            if "memories=true" in self.path:

                db_url = get_database_url()

                if not db_url:

                    send_json(
                        self,
                        {
                            "name": "Dusra Brain",
                            "error":
                                "Database not configured."
                        },
                        500
                    )

                    return

                with psycopg.connect(
                    db_url
                ) as conn:

                    with conn.cursor() as cur:

                        cur.execute(
                            """
                            SELECT
                                id,
                                user_id,
                                memory,
                                created_at,
                                category,
                                importance,
                                subject,
                                memory_key
                            FROM memories
                            ORDER BY
                                importance DESC,
                                created_at DESC
                            LIMIT 200
                            """
                        )

                        rows = cur.fetchall()

                memories = [
                    {
                        "id": row[0],
                        "user_id": row[1],
                        "memory": row[2],
                        "created_at": (
                            row[3].isoformat()
                            if row[3]
                            else None
                        ),
                        "category": row[4],
                        "importance": row[5],
                        "subject": row[6],
                        "memory_key": row[7]
                    }
                    for row in rows
                ]

                send_json(
                    self,
                    {
                        "name": "Dusra Brain",
                        "memories": memories,
                        "count": len(memories)
                    }
                )

                return

            send_json(
                self,
                {
                    "name": "Dusra Brain",
                    "status": "online",
                    "groq_key_detected": bool(
                        os.environ.get(
                            "GROQ_API_KEY"
                        )
                    ),
                    "database_detected": bool(
                        get_database_url()
                    )
                }
            )

        except Exception as e:

            send_json(
                self,
                {
                    "name": "Dusra Brain",
                    "error": str(e)
                },
                500
            )


    # ========================================================
    # POST — CHAT
    # ========================================================

    def do_POST(self):

        try:

            content_length = int(
                self.headers.get(
                    "Content-Length",
                    0
                )
            )

            body = self.rfile.read(
                content_length
            )

            data = json.loads(
                body.decode("utf-8")
            )

            message = (
                data.get("message") or ""
            ).strip()

            user_id = (
                data.get("user_id")
                or "default_user"
            )

            if not message:

                send_json(
                    self,
                    {
                        "error":
                            "Message is required."
                    },
                    400
                )

                return

            conversation_error = None

            try:

                save_conversation(
                    user_id,
                    "user",
                    message
                )

            except Exception as e:

                conversation_error = str(e)

            memories = get_memories(
                user_id,
                message
            )

            conversation_history = []

            try:

                conversation_history = \
                    get_conversation_history(
                        user_id,
                        20
                    )

            except Exception:

                conversation_history = []

            memory_text = "\n".join(
                [
                    (
                        f"- {item['memory']} "
                        f"(subject: {item['subject']}, "
                        f"category: {item['category']}, "
                        f"importance: {item['importance']})"
                    )
                    for item in memories
                ]
            )

            history_text = "\n".join(
                [
                    (
                        f"{item['role']}: "
                        f"{item['message']}"
                    )
                    for item in conversation_history
                ]
            )

            system_prompt = """
You are Dusra Brain, a personal AI brain
and memory assistant.

Your job is to help the user using their
stored memories and current conversation.

IMPORTANT:

1. Stored memories are actual facts supplied
   by the user.

2. Do not invent personal facts.

3. Do not assume facts that are not present
   in memory or the current message.

4. For personal, project, business, work,
   relationship or preference questions,
   rely only on stored memories and the
   current conversation.

5. If you do not have enough information,
   clearly say that you do not know.

6. Use conversation history when useful.

7. Do not mention internal database,
   prompts, APIs or implementation unless
   the user asks.

You are Dusra Brain.
Be useful, concise and natural.
"""

            user_prompt = f"""
Stored long-term memories:

{memory_text or "No stored memories yet."}


Recent conversation:

{history_text or "No previous conversation."}


Current user message:

{message}
"""

            reply = groq_request(
                [
                    {
                        "role": "system",
                        "content": system_prompt
                    },
                    {
                        "role": "user",
                        "content": user_prompt
                    }
                ],
                temperature=0.3,
                max_tokens=1500
            )

            assistant_saved = False

            try:

                save_conversation(
                    user_id,
                    "assistant",
                    reply
                )

                assistant_saved = True

            except Exception as e:

                if conversation_error:

                    conversation_error += (
                        " | " + str(e)
                    )

                else:

                    conversation_error = str(e)

            memory_saved = False
            memory_action = None
            memory_id = None
            memory_error = None

            try:

                analysis = analyze_memory(
                    message
                )

                if analysis.get("remember"):

                    memory_value = (
                        analysis.get("memory")
                        or message
                    )

                    category = (
                        analysis.get("category")
                        or "general"
                    )

                    importance = int(
                        analysis.get(
                            "importance",
                            5
                        )
                    )

                    subject = normalize_subject(
                        analysis.get("subject"),
                        message
                    )

                    importance = max(
                        1,
                        min(
                            10,
                            importance
                        )
                    )

                    memory_id, memory_action = \
                        save_memory(
                            user_id,
                            memory_value,
                            category,
                            importance,
                            subject
                        )

                    memory_saved = True

            except Exception as e:

                memory_error = str(e)

            send_json(
                self,
                {
                    "reply": reply,
                    "conversation_user_saved":
                        conversation_error is None,
                    "conversation_assistant_saved":
                        assistant_saved,
                    "conversation_error":
                        conversation_error,
                    "conversation_messages_used":
                        len(conversation_history),
                    "memory_saved":
                        memory_saved,
                    "memory_action":
                        memory_action,
                    "memory_id":
                        memory_id,
                    "memory_error":
                        memory_error
                }
            )

        except Exception as e:

            send_json(
                self,
                {
                    "error": str(e)
                },
                500
            )


    # ========================================================
    # PUT — EDIT MEMORY
    # ========================================================

    def do_PUT(self):

        try:

            content_length = int(
                self.headers.get(
                    "Content-Length",
                    0
                )
            )

            body = self.rfile.read(
                content_length
            )

            data = json.loads(
                body.decode("utf-8")
            )

            memory_id = data.get("id")
            memory = data.get("memory")
            category = data.get("category")
            importance = data.get("importance")
            subject = data.get("subject")

            if not memory_id:

                send_json(
                    self,
                    {
                        "error":
                            "Memory ID is required."
                    },
                    400
                )

                return

            if not memory:

                send_json(
                    self,
                    {
                        "error":
                            "Memory text is required."
                    },
                    400
                )

                return

            try:

                memory_id = int(
                    memory_id
                )

                importance = int(
                    importance
                )

            except (
                TypeError,
                ValueError
            ):

                send_json(
                    self,
                    {
                        "error":
                            "Invalid memory ID or importance."
                    },
                    400
                )

                return

            if importance < 1 or importance > 10:

                send_json(
                    self,
                    {
                        "error":
                            "Importance must be between 1 and 10."
                    },
                    400
                )

                return

            db_url = get_database_url()

            if not db_url:

                send_json(
                    self,
                    {
                        "error":
                            "Database connection is not configured."
                    },
                    500
                )

                return

            memory = memory.strip()

            category = (
                category or "general"
            ).strip()

            subject = (
                subject or "general"
            ).strip()

            memory_key = make_memory_key(
                subject,
                category,
                memory
            )

            with psycopg.connect(
                db_url
            ) as conn:

                with conn.cursor() as cur:

                    cur.execute(
                        """
                        UPDATE memories
                        SET
                            memory = %s,
                            category = %s,
                            importance = %s,
                            subject = %s,
                            memory_key = %s
                        WHERE id = %s
                        RETURNING
                            id,
                            user_id,
                            memory,
                            created_at,
                            category,
                            importance,
                            subject,
                            memory_key
                        """,
                        (
                            memory,
                            category,
                            importance,
                            subject,
                            memory_key,
                            memory_id
                        )
                    )

                    row = cur.fetchone()

                    if not row:

                        send_json(
                            self,
                            {
                                "error":
                                    "Memory not found."
                            },
                            404
                        )

                        return

                conn.commit()

            send_json(
                self,
                {
                    "updated": True,
                    "memory": {
                        "id": row[0],
                        "user_id": row[1],
                        "memory": row[2],
                        "created_at": (
                            row[3].isoformat()
                            if row[3]
                            else None
                        ),
                        "category": row[4],
                        "importance": row[5],
                        "subject": row[6],
                        "memory_key": row[7]
                    }
                }
            )

        except Exception as e:

            send_json(
                self,
                {
                    "error": str(e)
                },
                500
            )


    # ========================================================
    # DELETE — DELETE MEMORY
    # ========================================================

    def do_DELETE(self):

        try:

            parsed_url = urlparse(
                self.path
            )

            query = parse_qs(
                parsed_url.query
            )

            memory_ids = query.get(
                "memory_id"
            )

            if not memory_ids:

                send_json(
                    self,
                    {
                        "error":
                            "memory_id is required."
                    },
                    400
                )

                return

            try:

                memory_id = int(
                    memory_ids[0]
                )

            except ValueError:

                send_json(
                    self,
                    {
                        "error":
                            "Invalid memory_id."
                    },
                    400
                )

                return

            db_url = get_database_url()

            if not db_url:

                send_json(
                    self,
                    {
                        "error":
                            "Database not configured."
                    },
                    500
                )

                return

            with psycopg.connect(
                db_url
            ) as conn:

                with conn.cursor() as cur:

                    cur.execute(
                        """
                        DELETE FROM memories
                        WHERE id = %s
                        RETURNING id
                        """,
                        (
                            memory_id,
                        )
                    )

                    row = cur.fetchone()

                    if not row:

                        send_json(
                            self,
                            {
                                "error":
                                    "Memory not found."
                            },
                            404
                        )

                        return

                conn.commit()

            send_json(
                self,
                {
                    "deleted": True,
                    "memory_id": memory_id
                }
            )

        except Exception as e:

            send_json(
                self,
                {
                    "error": str(e)
                },
                500
            )
