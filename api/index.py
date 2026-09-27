import json
import os
import re
import urllib.request
import urllib.error
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler

import psycopg


# ============================================================
# BASIC HELPERS
# ============================================================

def send_json(handler, data, status=200):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")

    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.end_headers()

    handler.wfile.write(body)


def get_database_url():
    possible_names = [
        "DATABASE_URL",
        "POSTGRES_URL",
        "POSTGRES_PRISMA_URL",
        "POSTGRES_URL_NON_POOLING",
        "STORAGE_POSTGRES_URL",
        "STORAGE_DATABASE_URL",
    ]

    for name in possible_names:
        value = os.environ.get(name)

        if value:
            return value

    return None


def get_connection():
    database_url = get_database_url()

    if not database_url:
        raise Exception("Database connection string not found")

    return psycopg.connect(database_url)


# ============================================================
# GROQ
# ============================================================

def groq_request(messages, temperature=0.2):

    api_key = os.environ.get("GROQ_API_KEY")

    if not api_key:
        raise Exception("GROQ_API_KEY is missing")

    payload = {
        "model": "openai/gpt-oss-120b",
        "messages": messages,
        "temperature": temperature,
    }

    request = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key,
            "User-Agent": "Mozilla/5.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=60
        ) as response:

            raw = response.read().decode("utf-8")

            data = json.loads(raw)

            return data["choices"][0]["message"]["content"]

    except urllib.error.HTTPError as error:

        details = error.read().decode("utf-8")

        raise Exception(
            "Groq API error " +
            str(error.code) +
            ": " +
            details
        )


# ============================================================
# CONVERSATIONS
# ============================================================

def save_conversation(
    user_id,
    role,
    message,
    session_id="default",
    title="New Chat"
):

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                INSERT INTO conversations
                (
                    user_id,
                    role,
                    message,
                    session_id,
                    title
                )
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    user_id,
                    role,
                    message,
                    session_id,
                    title,
                )
            )

        conn.commit()


def get_conversation_history(
    user_id,
    session_id="default",
    limit=20
):

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    role,
                    message,
                    created_at
                FROM conversations
                WHERE user_id = %s
                  AND session_id = %s
                ORDER BY id DESC
                LIMIT %s
                """,
                (
                    user_id,
                    session_id,
                    limit,
                )
            )

            rows = cur.fetchall()


    rows.reverse()


    return [
        {
            "role": row[0],
            "message": row[1],
            "created_at": row[2].isoformat()
                if row[2]
                else None,
        }
        for row in rows
    ]


def get_all_conversations(
    user_id,
    limit=500
):

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    id,
                    role,
                    message,
                    created_at,
                    session_id,
                    title
                FROM conversations
                WHERE user_id = %s
                ORDER BY id ASC
                LIMIT %s
                """,
                (
                    user_id,
                    limit,
                )
            )

            rows = cur.fetchall()


    return [
        {
            "id": row[0],
            "role": row[1],
            "message": row[2],
            "created_at": row[3].isoformat()
                if row[3]
                else None,
            "session_id": row[4] or "default",
            "title": row[5] or "New Chat",
        }
        for row in rows
    ]


def get_sessions(user_id):

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    session_id,
                    COALESCE(
                        MAX(title),
                        'New Chat'
                    ) AS title,
                    MAX(created_at) AS last_message_at,
                    COUNT(*) AS message_count
                FROM conversations
                WHERE user_id = %s
                GROUP BY session_id
                ORDER BY MAX(created_at) DESC
                """,
                (user_id,)
            )

            rows = cur.fetchall()


    sessions = []

    for row in rows:

        sessions.append(
            {
                "session_id": row[0] or "default",
                "title": row[1] or "New Chat",
                "last_message_at":
                    row[2].isoformat()
                    if row[2]
                    else None,
                "message_count": row[3],
            }
        )


    if not sessions:

        sessions.append(
            {
                "session_id": "default",
                "title": "New Chat",
                "last_message_at": None,
                "message_count": 0,
            }
        )


    return sessions


# ============================================================
# MEMORY
# ============================================================

def get_memories(
    user_id,
    message="",
    session_id="default",
    limit=50
):

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    id,
                    memory,
                    created_at,
                    category,
                    importance,
                    subject,
                    memory_key,
                    session_id
                FROM memories
                WHERE user_id = %s
                  AND (
                        session_id = %s
                        OR session_id = 'default'
                  )
                ORDER BY
                    CASE
                        WHEN session_id = %s THEN 0
                        ELSE 1
                    END,
                    importance DESC,
                    created_at DESC
                LIMIT %s
                """,
                (
                    user_id,
                    session_id,
                    session_id,
                    limit,
                )
            )

            rows = cur.fetchall()


    return [
        {
            "id": row[0],
            "memory": row[1],
            "created_at": row[2].isoformat()
                if row[2]
                else None,
            "category": row[3] or "general",
            "importance": row[4] or 5,
            "subject": row[5] or "general",
            "memory_key": row[6],
            "session_id": row[7] or "default",
        }
        for row in rows
    ]


def get_subject_memories(
    user_id,
    subject,
    session_id="default",
    limit=30
):

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    id,
                    memory,
                    category,
                    importance,
                    subject,
                    memory_key,
                    session_id
                FROM memories
                WHERE user_id = %s
                  AND subject = %s
                  AND (
                        session_id = %s
                        OR session_id = 'default'
                  )
                ORDER BY
                    CASE
                        WHEN session_id = %s THEN 0
                        ELSE 1
                    END,
                    importance DESC,
                    created_at DESC
                LIMIT %s
                """,
                (
                    user_id,
                    subject,
                    session_id,
                    session_id,
                    limit,
                )
            )

            rows = cur.fetchall()


    return [
        {
            "id": row[0],
            "memory": row[1],
            "category": row[2] or "general",
            "importance": row[3] or 5,
            "subject": row[4] or "general",
            "memory_key": row[5],
            "session_id": row[6] or "default",
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

    normalized = " ".join(
        str(memory)
        .lower()
        .strip()
        .split()
    )

    raw = (
        str(subject).lower().strip()
        + "|"
        + str(category).lower().strip()
        + "|"
        + normalized
    )

    return raw[:1000]


# ============================================================
# JSON CLEANING
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
    new_memory,
    existing_memories
):

    if not existing_memories:
        return None


    existing_text = "\n".join(
        [
            f"{item['id']}: {item['memory']}"
            for item in existing_memories
        ]
    )


    prompt = f"""
You are checking whether a new personal memory
means essentially the same thing as an existing memory.

NEW MEMORY:
{new_memory}

EXISTING MEMORIES:
{existing_text}

Return ONLY JSON:

{{
  "duplicate_id": null
}}

OR:

{{
  "duplicate_id": 123
}}

Use duplicate_id only when the meaning is substantially
the same. Do not mark memories as duplicates just because
they are about the same project.
"""


    response =
        groq_request(
            [
                {
                    "role": "system",
                    "content":
                        "You are a precise memory deduplication system."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0
        )


    try:

        data =
            json.loads(
                clean_json_response(
                    response
                )
            )

        return data.get(
            "duplicate_id"
        )

    except Exception:

        return None


# ============================================================
# SAVE MEMORY
# ============================================================

def save_memory(
    user_id,
    memory,
    category="general",
    importance=5,
    subject="general",
    session_id="default"
):

    memory_key =
        make_memory_key(
            subject,
            category,
            memory
        )


    existing_memories =
        get_subject_memories(
            user_id,
            subject,
            session_id=session_id
        )


    duplicate_id =
        find_semantic_duplicate(
            memory,
            existing_memories
        )


    with get_connection() as conn:

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
                        memory_key = %s,
                        session_id = %s
                    WHERE id = %s
                    RETURNING
                        id,
                        memory,
                        created_at,
                        category,
                        importance,
                        subject,
                        memory_key,
                        session_id
                    """,
                    (
                        memory,
                        category,
                        importance,
                        subject,
                        memory_key,
                        session_id,
                        duplicate_id,
                    )
                )

            else:

                cur.execute(
                    """
                    SELECT id
                    FROM memories
                    WHERE user_id = %s
                      AND memory_key = %s
                    LIMIT 1
                    """,
                    (
                        user_id,
                        memory_key,
                    )
                )

                exact =
                    cur.fetchone()


                if exact:

                    cur.execute(
                        """
                        UPDATE memories
                        SET
                            memory = %s,
                            category = %s,
                            importance = %s,
                            subject = %s,
                            session_id = %s
                        WHERE id = %s
                        RETURNING
                            id,
                            memory,
                            created_at,
                            category,
                            importance,
                            subject,
                            memory_key,
                            session_id
                        """,
                        (
                            memory,
                            category,
                            importance,
                            subject,
                            session_id,
                            exact[0],
                        )
                    )

                else:

                    cur.execute(
                        """
                        INSERT INTO memories
                        (
                            user_id,
                            memory,
                            category,
                            importance,
                            subject,
                            memory_key,
                            session_id
                        )
                        VALUES
                        (
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s
                        )
                        RETURNING
                            id,
                            memory,
                            created_at,
                            category,
                            importance,
                            subject,
                            memory_key,
                            session_id
                        """,
                        (
                            user_id,
                            memory,
                            category,
                            importance,
                            subject,
                            memory_key,
                            session_id,
                        )
                    )


            row =
                cur.fetchone()


        conn.commit()


    return {
        "id": row[0],
        "memory": row[1],
        "created_at":
            row[2].isoformat()
            if row[2]
            else None,
        "category": row[3],
        "importance": row[4],
        "subject": row[5],
        "memory_key": row[6],
        "session_id": row[7] or "default",
    }


# ============================================================
# MEMORY ANALYSIS
# ============================================================

def analyze_memory(
    user_message,
    current_subject="general"
):

    prompt = f"""
Analyze the user's message and decide whether it contains
a durable personal fact, preference, project detail, goal,
business information, decision, relationship detail,
work information, or instruction worth remembering.

Current conversation subject:
{current_subject}

User message:
{user_message}

Do NOT store:
- greetings
- casual conversation
- temporary questions
- generic information
- ordinary requests that are not about the user's own facts

If it should be remembered, return ONLY JSON:

{{
  "remember": true,
  "memory": "A concise factual statement about the user",
  "category": "personal|preference|project|business|goal|relationship|work|decision|instruction|general",
  "importance": 1,
  "subject": "A concise subject name"
}}

If it should not be remembered:

{{
  "remember": false
}}

Importance:
1-3 = low
4-6 = medium
7-8 = high
9-10 = critical

Do not invent facts.
Only extract information explicitly stated by the user.
"""


    response =
        groq_request(
            [
                {
                    "role": "system",
                    "content":
                        "You are a personal memory extraction system. Never invent user facts."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0
        )


    try:

        return json.loads(
            clean_json_response(
                response
            )
        )

    except Exception:

        return {
            "remember": False
        }


# ============================================================
# SUBJECT NORMALIZATION
# ============================================================

def normalize_subject(
    subject
):

    if not subject:
        return "general"


    subject =
        str(subject).strip()


    if not subject:
        return "general"


    return subject[:200]


# ============================================================
# REQUEST HANDLER
# ============================================================

class handler(
    BaseHTTPRequestHandler
):

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
            "GET,POST,PUT,DELETE,OPTIONS"
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

        parsed =
            urlparse(
                self.path
            )


        params =
            parse_qs(
                parsed.query
            )


        user_id =
            params.get(
                "user_id",
                ["default_user"]
            )[0]


        # ----------------------------------------------------
        # MEMORIES
        # ----------------------------------------------------

        if params.get("memories") == ["true"]:

            session_id =
                params.get(
                    "session_id",
                    ["default"]
                )[0]


            try:

                memories =
                    get_memories(
                        user_id,
                        session_id=session_id,
                        limit=200
                    )


                send_json(
                    self,
                    {
                        "memories":
                            memories,
                        "count":
                            len(memories),
                    }
                )

            except Exception as error:

                send_json(
                    self,
                    {
                        "error":
                            str(error)
                    },
                    500
                )


            return


        # ----------------------------------------------------
        # ALL CONVERSATIONS
        # ----------------------------------------------------

        if params.get("conversations") == ["true"]:

            try:

                conversations =
                    get_all_conversations(
                        user_id
                    )


                send_json(
                    self,
                    {
                        "conversations":
                            conversations,
                        "count":
                            len(conversations),
                    }
                )

            except Exception as error:

                send_json(
                    self,
                    {
                        "error":
                            str(error)
                    },
                    500
                )


            return


        # ----------------------------------------------------
        # SESSIONS
        # ----------------------------------------------------

        if params.get("sessions") == ["true"]:

            try:

                sessions =
                    get_sessions(
                        user_id
                    )


                send_json(
                    self,
                    {
                        "sessions":
                            sessions,
                        "count":
                            len(sessions),
                    }
                )

            except Exception as error:

                send_json(
                    self,
                    {
                        "error":
                            str(error)
                    },
                    500
                )


            return


        # ----------------------------------------------------
        # SINGLE SESSION
        # ----------------------------------------------------

        if params.get("session") == ["true"]:

            session_id =
                params.get(
                    "session_id",
                    ["default"]
                )[0]


            try:

                history =
                    get_conversation_history(
                        user_id,
                        session_id=session_id,
                        limit=100
                    )


                send_json(
                    self,
                    {
                        "session_id":
                            session_id,

                        "history":
                            history,

                        "count":
                            len(history),
                    }
                )

            except Exception as error:

                send_json(
                    self,
                    {
                        "error":
                            str(error)
                    },
                    500
                )


            return


        # ----------------------------------------------------
        # HEALTH
        # ----------------------------------------------------

        send_json(
            self,
            {
                "name":
                    "Dusra Brain",

                "status":
                    "online",

                "groq_key_detected":
                    bool(
                        os.environ.get(
                            "GROQ_API_KEY"
                        )
                    ),

                "database_detected":
                    bool(
                        get_database_url()
                    ),
            }
        )


    # ========================================================
    # POST
    # ========================================================

    def do_POST(self):

        try:

            content_length =
                int(
                    self.headers.get(
                        "Content-Length",
                        0
                    )
                )


            raw_body =
                self.rfile.read(
                    content_length
                )


            body =
                json.loads(
                    raw_body.decode(
                        "utf-8"
                    )
                )


            message =
                str(
                    body.get(
                        "message",
                        ""
                    )
                ).strip()


            user_id =
                body.get(
                    "user_id",
                    "default_user"
                )


            session_id =
                body.get(
                    "session_id",
                    "default"
                )


            title =
                body.get(
                    "title",
                    "New Chat"
                )


            session_id =
                str(
                    session_id or "default"
                )


            title =
                str(
                    title or "New Chat"
                )


            if not message:

                send_json(
                    self,
                    {
                        "error":
                            "Message is required"
                    },
                    400
                )

                return


            # ------------------------------------------------
            # SAVE USER MESSAGE
            # ------------------------------------------------

            save_conversation(
                user_id,
                "user",
                message,
                session_id,
                title
            )


            # ------------------------------------------------
            # LOAD SESSION HISTORY
            # ------------------------------------------------

            history =
                get_conversation_history(
                    user_id,
                    session_id=session_id,
                    limit=20
                )


            # ------------------------------------------------
            # LOAD RELEVANT MEMORIES
            # ------------------------------------------------

            memories =
                get_memories(
                    user_id,
                    message=message,
                    session_id=session_id,
                    limit=50
                )


            memory_text =
                "\n".join(
                    [
                        (
                            f"- {item['memory']} "
                            f"(subject: {item['subject']}, "
                            f"category: {item['category']}, "
                            f"importance: {item['importance']}, "
                            f"session: {item['session_id']})"
                        )
                        for item in memories
                    ]
                )


            if not memory_text:

                memory_text =
                    "No stored memories available."


            # ------------------------------------------------
            # CONVERSATION HISTORY
            # ------------------------------------------------

            history_text =
                "\n".join(
                    [
                        f"{item['role']}: {item['message']}"
                        for item in history
                    ]
                )


            if not history_text:

                history_text =
                    "No previous conversation."


            # ------------------------------------------------
            # AI SYSTEM
            # ------------------------------------------------

            system_prompt = f"""
You are Dusra Brain, a personal AI brain and memory assistant.

You are currently working inside this conversation session:

Session ID:
{session_id}

Session title:
{title}

IMPORTANT MEMORY RULES:

1. Stored memories are actual facts explicitly provided by the user.

2. Never invent personal facts.

3. Do not assume something about the user just because it
sounds plausible.

4. For personal, business, project, work, preference, or goal
questions, use stored memories and the current conversation.

5. Prefer memories from the current session.

6. Default-session memories may also be used when relevant.

7. If the available memories do not contain the answer,
say that you do not have enough stored information.

8. Do not claim to remember something that is not in memory
or conversation history.

9. Keep answers natural and useful.

STORED MEMORIES:

{memory_text}

CURRENT CONVERSATION HISTORY:

{history_text}
"""


            # ------------------------------------------------
            # GROQ RESPONSE
            # ------------------------------------------------

            response =
                groq_request(
                    [
                        {
                            "role":
                                "system",

                            "content":
                                system_prompt
                        },

                        {
                            "role":
                                "user",

                            "content":
                                message
                        }
                    ],
                    temperature=0.2
                )


            # ------------------------------------------------
            # SAVE ASSISTANT RESPONSE
            # ------------------------------------------------

            save_conversation(
                user_id,
                "assistant",
                response,
                session_id,
                title
            )


            # ------------------------------------------------
            # MEMORY EXTRACTION
            # ------------------------------------------------

            try:

                analysis =
                    analyze_memory(
                        message,
                        current_subject=title
                    )


                if analysis.get(
                    "remember"
                ):

                    memory_text_value =
                        str(
                            analysis.get(
                                "memory",
                                ""
                            )
                        ).strip()


                    category =
                        str(
                            analysis.get(
                                "category",
                                "general"
                            )
                        ).strip()


                    importance =
                        int(
                            analysis.get(
                                "importance",
                                5
                            )
                        )


                    subject =
                        normalize_subject(
                            analysis.get(
                                "subject",
                                title
                            )
                        )


                    if memory_text_value:

                        save_memory(
                            user_id=user_id,

                            memory=
                                memory_text_value,

                            category=
                                category,

                            importance=
                                importance,

                            subject=
                                subject,

                            session_id=
                                session_id
                        )

            except Exception:

                pass


            send_json(
                self,
                {
                    "response":
                        response,

                    "session_id":
                        session_id,

                    "title":
                        title,
                }
            )


        except Exception as error:

            send_json(
                self,
                {
                    "error":
                        str(error)
                },
                500
            )


    # ========================================================
    # PUT
    # ========================================================

    def do_PUT(self):

        try:

            content_length =
                int(
                    self.headers.get(
                        "Content-Length",
                        0
                    )
                )


            raw_body =
                self.rfile.read(
                    content_length
                )


            body =
                json.loads(
                    raw_body.decode(
                        "utf-8"
                    )
                )


            memory_id =
                body.get(
                    "id"
                )


            memory =
                str(
                    body.get(
                        "memory",
                        ""
                    )
                ).strip()


            category =
                str(
                    body.get(
                        "category",
                        "general"
                    )
                ).strip()


            importance =
                int(
                    body.get(
                        "importance",
                        5
                    )
                )


            subject =
                normalize_subject(
                    body.get(
                        "subject",
                        "general"
                    )
                )


            if not memory_id:

                send_json(
                    self,
                    {
                        "error":
                            "Memory ID is required"
                    },
                    400
                )

                return


            if not memory:

                send_json(
                    self,
                    {
                        "error":
                            "Memory cannot be empty"
                    },
                    400
                )

                return


            memory_key =
                make_memory_key(
                    subject,
                    category,
                    memory
                )


            with get_connection() as conn:

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
                            memory,
                            created_at,
                            category,
                            importance,
                            subject,
                            memory_key,
                            session_id
                        """,
                        (
                            memory,
                            category,
                            importance,
                            subject,
                            memory_key,
                            memory_id,
                        )
                    )


                    row =
                        cur.fetchone()


                conn.commit()


            if not row:

                send_json(
                    self,
                    {
                        "error":
                            "Memory not found"
                    },
                    404
                )

                return


            result = {
                "id": row[0],
                "memory": row[1],
                "created_at":
                    row[2].isoformat()
                    if row[2]
                    else None,
                "category": row[3],
                "importance": row[4],
                "subject": row[5],
                "memory_key": row[6],
                "session_id": row[7] or "default",
            }


            send_json(
                self,
                {
                    "memory":
                        result
                }
            )


        except Exception as error:

            send_json(
                self,
                {
                    "error":
                        str(error)
                },
                500
            )


    # ========================================================
    # DELETE
    # ========================================================

    def do_DELETE(self):

        try:

            parsed =
                urlparse(
                    self.path
                )


            params =
                parse_qs(
                    parsed.query
                )


            memory_id =
                params.get(
                    "memory_id",
                    [None]
                )[0]


            if not memory_id:

                send_json(
                    self,
                    {
                        "error":
                            "memory_id is required"
                    },
                    400
                )

                return


            with get_connection() as conn:

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


                    deleted =
                        cur.fetchone()


                conn.commit()


            if not deleted:

                send_json(
                    self,
                    {
                        "error":
                            "Memory not found"
                    },
                    404
                )

                return


            send_json(
                self,
                {
                    "success":
                        True,

                    "deleted_id":
                        deleted[0]
                }
            )


        except Exception as error:

            send_json(
                self,
                {
                    "error":
                        str(error)
                },
                500
            )
