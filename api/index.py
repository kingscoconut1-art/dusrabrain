import json
import os
import re
import urllib.request
import urllib.error
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler

import psycopg


# ============================================================
# RESPONSE HELPERS
# ============================================================

def send_json(handler, data, status=200):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")

    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Content-Length", str(len(body)))
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
        "STORAGE_DATABASE_URL",
    ]

    for name in names:
        value = os.environ.get(name)

        if value:
            return value

    return None


def get_connection():
    database_url = get_database_url()

    if not database_url:
        raise Exception("Database URL not found")

    return psycopg.connect(database_url)


# ============================================================
# GROQ
# ============================================================

def groq_request(messages, temperature=0.2, max_tokens=1200):
    api_key = os.environ.get("GROQ_API_KEY")

    if not api_key:
        raise Exception("GROQ_API_KEY not configured")

    url = "https://api.groq.com/openai/v1/chat/completions"

    payload = {
        "model": "openai/gpt-oss-120b",
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens
    }

    data = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=data,
        method="POST"
    )

    request.add_header(
        "Content-Type",
        "application/json"
    )

    request.add_header(
        "Authorization",
        "Bearer " + api_key
    )

    request.add_header(
        "User-Agent",
        "Mozilla/5.0"
    )

    try:

        with urllib.request.urlopen(
            request,
            timeout=60
        ) as response:

            response_data =
                response.read().decode("utf-8")

            return json.loads(response_data)

    except urllib.error.HTTPError as error:

        error_body = ""

        try:
            error_body = error.read().decode("utf-8")
        except Exception:
            pass

        raise Exception(
            "Groq API error "
            + str(error.code)
            + ": "
            + error_body
        )

    except Exception as error:

        raise Exception(
            "Groq request failed: "
            + str(error)
        )


# ============================================================
# CONVERSATION MEMORY
# ============================================================

def save_conversation(
    user_id,
    role,
    message
):
    connection = get_connection()

    try:

        with connection.cursor() as cursor:

            cursor.execute(
                """
                INSERT INTO conversations
                (user_id, role, message)
                VALUES (%s, %s, %s)
                """,
                (
                    user_id,
                    role,
                    message
                )
            )

        connection.commit()

    finally:

        connection.close()


def get_conversation_history(
    user_id,
    limit=20
):
    connection = get_connection()

    try:

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT role, message, created_at
                FROM conversations
                WHERE user_id = %s
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                (
                    user_id,
                    limit
                )
            )

            rows = cursor.fetchall()

            rows.reverse()

            return [
                {
                    "role": row[0],
                    "message": row[1],
                    "created_at": row[2].isoformat()
                    if row[2]
                    else None
                }
                for row in rows
            ]

    finally:

        connection.close()


def get_all_conversations(
    user_id,
    limit=200
):
    connection = get_connection()

    try:

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT id, role, message, created_at
                FROM conversations
                WHERE user_id = %s
                ORDER BY created_at ASC, id ASC
                LIMIT %s
                """,
                (
                    user_id,
                    limit
                )
            )

            rows = cursor.fetchall()

            return [
                {
                    "id": row[0],
                    "role": row[1],
                    "message": row[2],
                    "created_at": row[3].isoformat()
                    if row[3]
                    else None
                }
                for row in rows
            ]

    finally:

        connection.close()


# ============================================================
# MEMORY RETRIEVAL
# ============================================================

def get_memories(
    user_id,
    message="",
    limit=50
):
    connection = get_connection()

    try:

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    id,
                    memory,
                    category,
                    importance,
                    subject,
                    memory_key,
                    created_at
                FROM memories
                WHERE user_id = %s
                ORDER BY importance DESC, created_at DESC
                LIMIT %s
                """,
                (
                    user_id,
                    limit
                )
            )

            rows = cursor.fetchall()

            memories = []

            for row in rows:

                memories.append(
                    {
                        "id": row[0],
                        "memory": row[1],
                        "category": row[2],
                        "importance": row[3],
                        "subject": row[4],
                        "memory_key": row[5],
                        "created_at":
                            row[6].isoformat()
                            if row[6]
                            else None
                    }
                )

            return memories

    finally:

        connection.close()


def get_subject_memories(
    user_id,
    subject
):
    connection = get_connection()

    try:

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    id,
                    memory,
                    category,
                    importance,
                    subject,
                    memory_key,
                    created_at
                FROM memories
                WHERE user_id = %s
                AND LOWER(subject) = LOWER(%s)
                ORDER BY importance DESC, created_at DESC
                LIMIT 50
                """,
                (
                    user_id,
                    subject
                )
            )

            rows = cursor.fetchall()

            return [
                {
                    "id": row[0],
                    "memory": row[1],
                    "category": row[2],
                    "importance": row[3],
                    "subject": row[4],
                    "memory_key": row[5],
                    "created_at":
                        row[6].isoformat()
                        if row[6]
                        else None
                }
                for row in rows
            ]

    finally:

        connection.close()


# ============================================================
# MEMORY KEY
# ============================================================

def make_memory_key(
    subject,
    category,
    memory
):
    value = (
        str(subject or "general")
        + "|"
        + str(category or "general")
        + "|"
        + str(memory or "")
    )

    value = value.lower().strip()

    value = re.sub(
        r"\s+",
        " ",
        value
    )

    return value


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

    text = text.strip()

    start = text.find("{")
    end = text.rfind("}")

    if start >= 0 and end >= 0:

        text = text[start:end + 1]

    return text


# ============================================================
# SEMANTIC DUPLICATE CHECK
# ============================================================

def find_semantic_duplicate(
    user_id,
    subject,
    new_memory
):
    existing = get_subject_memories(
        user_id,
        subject
    )

    if not existing:
        return None

    existing_text = "\n".join(
        [
            str(item["id"])
            + ": "
            + str(item["memory"])
            for item in existing
        ]
    )

    prompt = f"""
You are checking whether a new personal memory
is semantically the same as an existing memory.

New memory:
{new_memory}

Existing memories:
{existing_text}

Return ONLY valid JSON:

{{
  "duplicate_id": null
}}

If the new memory means essentially the same thing
as one existing memory, return its numeric id.

If it is genuinely new or materially different,
return null.

Do not explain.
"""

    try:

        result = groq_request(
            [
                {
                    "role": "system",
                    "content":
                        "You detect semantic duplicate memories."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0,
            max_tokens=200
        )

        text = result["choices"][0]["message"]["content"]

        parsed = json.loads(
            clean_json_response(text)
        )

        duplicate_id =
            parsed.get("duplicate_id")

        if duplicate_id:

            try:
                return int(duplicate_id)
            except Exception:
                return None

        return None

    except Exception:

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
    memory_key = make_memory_key(
        subject,
        category,
        memory
    )

    connection = get_connection()

    try:

        with connection.cursor() as cursor:

            # Exact duplicate
            cursor.execute(
                """
                SELECT id
                FROM memories
                WHERE user_id = %s
                AND memory_key = %s
                LIMIT 1
                """,
                (
                    user_id,
                    memory_key
                )
            )

            existing =
                cursor.fetchone()


            if existing:

                memory_id =
                    existing[0]

                cursor.execute(
                    """
                    UPDATE memories
                    SET memory = %s,
                        category = %s,
                        importance = %s,
                        subject = %s,
                        memory_key = %s
                    WHERE id = %s
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

                connection.commit()

                return memory_id


        # Semantic duplicate
        duplicate_id =
            find_semantic_duplicate(
                user_id,
                subject,
                memory
            )


        if duplicate_id:

            with connection.cursor() as cursor:

                cursor.execute(
                    """
                    UPDATE memories
                    SET memory = %s,
                        category = %s,
                        importance = %s,
                        subject = %s,
                        memory_key = %s
                    WHERE id = %s
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

            connection.commit()

            return duplicate_id


        with connection.cursor() as cursor:

            cursor.execute(
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
                (%s, %s, %s, %s, %s, %s)
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

            memory_id =
                cursor.fetchone()[0]

        connection.commit()

        return memory_id

    finally:

        connection.close()


# ============================================================
# MEMORY ANALYSIS
# ============================================================

def analyze_memory(
    user_message
):
    prompt = f"""
Analyze the following user message.

Decide whether it contains a durable fact,
preference, project, business fact, goal,
decision, instruction, relationship fact,
or other information that should be remembered
for future conversations.

Do NOT remember casual questions,
temporary requests, greetings,
or ordinary conversation.

User message:
{user_message}

Return ONLY valid JSON:

{{
  "should_remember": true,
  "memory": "clean concise statement",
  "category": "personal|preference|project|business|goal|relationship|work|decision|instruction|general",
  "importance": 1,
  "subject": "short subject"
}}

If it should NOT be remembered:

{{
  "should_remember": false
}}
"""

    result = groq_request(
        [
            {
                "role": "system",
                "content":
                    "You are a personal memory extraction engine."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        temperature=0,
        max_tokens=500
    )

    text =
        result["choices"][0]["message"]["content"]

    return json.loads(
        clean_json_response(text)
    )


# ============================================================
# SUBJECT NORMALIZATION
# ============================================================

def normalize_subject(subject):

    if not subject:
        return "general"

    subject =
        str(subject).strip()

    if not subject:
        return "general"

    return subject[:100]


# ============================================================
# MAIN HANDLER
# ============================================================

class handler(BaseHTTPRequestHandler):


    # --------------------------------------------------------
    # OPTIONS
    # --------------------------------------------------------

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


    # --------------------------------------------------------
    # GET
    # --------------------------------------------------------

    def do_GET(self):

        try:

            parsed =
                urlparse(self.path)

            query =
                parse_qs(parsed.query)


            # ----------------------------------------------
            # MEMORY DASHBOARD
            # ----------------------------------------------

            if query.get("memories") == ["true"]:

                user_id =
                    query.get(
                        "user_id",
                        ["default_user"]
                    )[0]


                memories =
                    get_memories(
                        user_id,
                        limit=200
                    )


                send_json(
                    self,
                    {
                        "memories": memories,
                        "count": len(memories)
                    }
                )

                return


            # ----------------------------------------------
            # CONVERSATION HISTORY
            # ----------------------------------------------

            if query.get("conversations") == ["true"]:

                user_id =
                    query.get(
                        "user_id",
                        ["default_user"]
                    )[0]


                conversations =
                    get_all_conversations(
                        user_id,
                        limit=500
                    )


                send_json(
                    self,
                    {
                        "conversations":
                            conversations,

                        "count":
                            len(conversations)
                    }
                )

                return


            # ----------------------------------------------
            # HEALTH CHECK
            # ----------------------------------------------

            database_detected =
                bool(get_database_url())

            groq_detected =
                bool(
                    os.environ.get(
                        "GROQ_API_KEY"
                    )
                )


            send_json(
                self,
                {
                    "name":
                        "Dusra Brain",

                    "status":
                        "online",

                    "groq_key_detected":
                        groq_detected,

                    "database_detected":
                        database_detected
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


    # --------------------------------------------------------
    # POST
    # --------------------------------------------------------

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
                str(
                    body.get(
                        "user_id",
                        "default_user"
                    )
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


            # ----------------------------------------------
            # SAVE USER MESSAGE
            # ----------------------------------------------

            save_conversation(
                user_id,
                "user",
                message
            )


            # ----------------------------------------------
            # RETRIEVE MEMORY
            # ----------------------------------------------

            memories =
                get_memories(
                    user_id,
                    message,
                    limit=50
                )


            # ----------------------------------------------
            # RETRIEVE CONVERSATION HISTORY
            # ----------------------------------------------

            conversation_history =
                get_conversation_history(
                    user_id,
                    limit=20
                )


            # ----------------------------------------------
            # MEMORY TEXT
            # ----------------------------------------------

            if memories:

                memory_text =
                    "\n".join(
                        [
                            "- "
                            + str(item["memory"])
                            + " ["
                            + str(item["subject"])
                            + "]"
                            for item in memories
                        ]
                    )

            else:

                memory_text =
                    "No stored memories are available."


            # ----------------------------------------------
            # CONVERSATION TEXT
            # ----------------------------------------------

            history_messages = []


            for item in conversation_history:

                role =
                    item["role"]

                content =
                    item["message"]


                if role not in [
                    "user",
                    "assistant"
                ]:

                    continue


                history_messages.append(
                    {
                        "role":
                            role,

                        "content":
                            content
                    }
                )


            # ----------------------------------------------
            # SYSTEM PROMPT
            # ----------------------------------------------

            system_prompt = f"""
You are Dusra Brain, a personal AI brain
and memory assistant.

Your job is to help the user while using
their stored memories and recent conversation
when relevant.

IMPORTANT MEMORY RULES:

1. Stored memories are actual user facts.
2. Do not invent personal facts.
3. Do not assume personal information.
4. For personal, project, business, work,
   or relationship questions, rely only on
   stored memories and the current conversation.
5. If the available information is insufficient,
   say that you don't have enough information.
6. Do not claim that something is remembered
   unless it is actually present in the provided
   memory or conversation context.
7. Answer naturally and directly.

STORED USER MEMORIES:

{memory_text}

RECENT CONVERSATION:

Use the conversation history supplied in the
conversation messages below to maintain context.
"""


            # ----------------------------------------------
            # BUILD GROQ MESSAGES
            # ----------------------------------------------

            groq_messages = [
                {
                    "role":
                        "system",

                    "content":
                        system_prompt
                }
            ]


            # Add recent conversation
            for item in history_messages:

                groq_messages.append(
                    {
                        "role":
                            item["role"],

                        "content":
                            item["content"]
                    }
                )


            # Current message
            groq_messages.append(
                {
                    "role":
                        "user",

                    "content":
                        message
                }
            )


            # ----------------------------------------------
            # AI RESPONSE
            # ----------------------------------------------

            result =
                groq_request(
                    groq_messages,
                    temperature=0.3,
                    max_tokens=1200
                )


            assistant_message =
                result[
                    "choices"
                ][
                    0
                ][
                    "message"
                ][
                    "content"
                ].strip()


            # ----------------------------------------------
            # SAVE ASSISTANT MESSAGE
            # ----------------------------------------------

            save_conversation(
                user_id,
                "assistant",
                assistant_message
            )


            # ----------------------------------------------
            # MEMORY EXTRACTION
            # ----------------------------------------------

            memory_saved =
                False

            memory_error =
                None

            extracted_memory =
                None


            try:

                analysis =
                    analyze_memory(
                        message
                    )


                if analysis.get(
                    "should_remember",
                    False
                ):

                    extracted_memory =
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
                        ).strip().lower()


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
                                "general"
                            )
                        )


                    if not extracted_memory:

                        raise Exception(
                            "Memory extraction returned empty memory"
                        )


                    if importance < 1:

                        importance = 1


                    if importance > 10:

                        importance = 10


                    valid_categories = [
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
                    ]


                    if category not in valid_categories:

                        category =
                            "general"


                    save_memory(
                        user_id,
                        extracted_memory,
                        category,
                        importance,
                        subject
                    )


                    memory_saved =
                        True


            except Exception as error:

                memory_error =
                    str(error)


            # ----------------------------------------------
            # RESPONSE
            # ----------------------------------------------

            send_json(
                self,
                {
                    "response":
                        assistant_message,

                    "memory_saved":
                        memory_saved,

                    "memory_error":
                        memory_error,

                    "memory":
                        extracted_memory
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


    # --------------------------------------------------------
    # PUT
    # --------------------------------------------------------

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
                int(
                    body.get(
                        "id"
                    )
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


            subject =
                normalize_subject(
                    body.get(
                        "subject",
                        "general"
                    )
                )


            importance =
                int(
                    body.get(
                        "importance",
                        5
                    )
                )


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


            if importance < 1:

                importance = 1


            if importance > 10:

                importance = 10


            memory_key =
                make_memory_key(
                    subject,
                    category,
                    memory
                )


            connection =
                get_connection()


            try:

                with connection.cursor() as cursor:

                    cursor.execute(
                        """
                        UPDATE memories
                        SET memory = %s,
                            category = %s,
                            importance = %s,
                            subject = %s,
                            memory_key = %s
                        WHERE id = %s
                        RETURNING
                            id,
                            memory,
                            category,
                            importance,
                            subject,
                            memory_key,
                            created_at
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


                    row =
                        cursor.fetchone()


                connection.commit()


            finally:

                connection.close()


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


            updated_memory = {
                "id":
                    row[0],

                "memory":
                    row[1],

                "category":
                    row[2],

                "importance":
                    row[3],

                "subject":
                    row[4],

                "memory_key":
                    row[5],

                "created_at":
                    row[6].isoformat()
                    if row[6]
                    else None
            }


            send_json(
                self,
                {
                    "success":
                        True,

                    "memory":
                        updated_memory
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


    # --------------------------------------------------------
    # DELETE
    # --------------------------------------------------------

    def do_DELETE(self):

        try:

            parsed =
                urlparse(self.path)

            query =
                parse_qs(
                    parsed.query
                )


            memory_id =
                query.get(
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


            connection =
                get_connection()


            try:

                with connection.cursor() as cursor:

                    cursor.execute(
                        """
                        DELETE FROM memories
                        WHERE id = %s
                        """,
                        (
                            int(memory_id),
                        )
                    )


                    deleted =
                        cursor.rowcount


                connection.commit()


            finally:

                connection.close()


            if deleted == 0:

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
                        int(memory_id)
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
