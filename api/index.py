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
    handler.send_header(
        "Content-Type",
        "application/json; charset=utf-8"
    )
    handler.send_header(
        "Access-Control-Allow-Origin",
        "*"
    )
    handler.send_header(
        "Access-Control-Allow-Headers",
        "Content-Type"
    )
    handler.send_header(
        "Access-Control-Allow-Methods",
        "GET, POST, DELETE, OPTIONS"
    )
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


# ============================================================
# GROQ
# ============================================================

def groq_request(api_key, messages, temperature=0.2):

    url = "https://api.groq.com/openai/v1/chat/completions"

    payload = {
        "model": "openai/gpt-oss-120b",
        "messages": messages,
        "temperature": temperature
    }

    data = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Mozilla/5.0"
        }
    )

    try:

        with urllib.request.urlopen(
            request,
            timeout=60
        ) as response:

            raw = response.read().decode("utf-8")

            result = json.loads(raw)

            return result[
                "choices"
            ][0][
                "message"
            ][
                "content"
            ]

    except urllib.error.HTTPError as e:

        error_body = e.read().decode(
            "utf-8",
            errors="replace"
        )

        raise Exception(
            f"Groq HTTP {e.code}: {error_body}"
        )

    except Exception as e:

        raise Exception(
            f"Groq request failed: {str(e)}"
        )


# ============================================================
# CONVERSATION MEMORY
# ============================================================

def save_conversation(
    user_id,
    role,
    message
):

    db_url = get_database_url()

    if not db_url:
        return False

    try:

        with psycopg.connect(db_url) as conn:

            with conn.cursor() as cur:

                cur.execute(
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

        return True

    except Exception:

        return False


def get_conversation_history(
    user_id,
    limit=20
):

    db_url = get_database_url()

    if not db_url:
        return []

    try:

        with psycopg.connect(db_url) as conn:

            with conn.cursor() as cur:

                cur.execute(
                    """
                    SELECT role, message
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
                "content": row[1]
            }
            for row in rows
        ]

    except Exception:

        return []


# ============================================================
# LONG-TERM MEMORY
# ============================================================

def get_memories(
    user_id,
    message=""
):

    db_url = get_database_url()

    if not db_url:
        return []

    try:

        with psycopg.connect(db_url) as conn:

            with conn.cursor() as cur:

                cur.execute(
                    """
                    SELECT
                        id,
                        memory,
                        category,
                        importance,
                        subject,
                        created_at
                    FROM memories
                    WHERE user_id = %s
                    ORDER BY importance DESC, created_at DESC
                    LIMIT 50
                    """,
                    (user_id,)
                )

                rows = cur.fetchall()

        message_lower = message.lower()

        memories = []

        for row in rows:

            item = {
                "id": row[0],
                "memory": row[1],
                "category": row[2],
                "importance": row[3],
                "subject": row[4],
                "created_at": row[5]
            }

            subject = str(
                row[4] or ""
            ).lower()

            if (
                subject
                and subject != "general"
                and subject in message_lower
            ):
                item["_subject_match"] = True

            else:

                item["_subject_match"] = False

            memories.append(item)

        memories.sort(
            key=lambda x: (
                x["_subject_match"],
                x["importance"] or 0,
                x["created_at"]
            ),
            reverse=True
        )

        for item in memories:

            item.pop(
                "_subject_match",
                None
            )

        return memories

    except Exception:

        return []


def get_subject_memories(
    user_id,
    subject
):

    db_url = get_database_url()

    if not db_url:
        return []

    try:

        with psycopg.connect(db_url) as conn:

            with conn.cursor() as cur:

                cur.execute(
                    """
                    SELECT
                        id,
                        memory,
                        category,
                        importance,
                        subject,
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

                rows = cur.fetchall()

        return rows

    except Exception:

        return []


# ============================================================
# MEMORY DUPLICATE DETECTION
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
    )

    text = text.lower().strip()

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    text = re.sub(
        r"[^\w\s|]",
        "",
        text
    )

    return text


def find_semantic_duplicate(
    api_key,
    new_memory,
    existing_memories
):

    if not existing_memories:
        return None

    existing_text = []

    for row in existing_memories:

        existing_text.append(
            f"ID {row[0]}: {row[1]}"
        )

    prompt = f"""
You are a memory deduplication system.

Determine whether the NEW MEMORY means essentially the
same thing as any EXISTING MEMORY.

NEW MEMORY:
{new_memory}

EXISTING MEMORIES:
{chr(10).join(existing_text)}

Return ONLY valid JSON:

{{
  "duplicate_id": null
}}

OR:

{{
  "duplicate_id": 123
}}

Rules:

- Use semantic meaning, not exact wording.
- Different wording with the same fact is a duplicate.
- Do not mark related but different facts as duplicates.
- If there is no clear duplicate, return null.
"""

    try:

        response = groq_request(
            api_key,
            [
                {
                    "role": "system",
                    "content": (
                        "You are a precise memory "
                        "deduplication system."
                    )
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0
        )

        cleaned = clean_json_response(
            response
        )

        result = json.loads(
            cleaned
        )

        duplicate_id = result.get(
            "duplicate_id"
        )

        if duplicate_id:

            return int(
                duplicate_id
            )

    except Exception:

        pass

    return None


# ============================================================
# SAVE LONG-TERM MEMORY
# ============================================================

def save_memory(
    user_id,
    memory,
    category,
    importance,
    subject,
    api_key
):

    db_url = get_database_url()

    if not db_url:

        return {
            "saved": False,
            "error": "Database not available"
        }

    memory_key = make_memory_key(
        subject,
        category,
        memory
    )

    try:

        with psycopg.connect(db_url) as conn:

            with conn.cursor() as cur:

                # --------------------------------------------
                # EXACT DUPLICATE
                # --------------------------------------------

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
                        memory_key
                    )
                )

                exact_duplicate = cur.fetchone()

                if exact_duplicate:

                    cur.execute(
                        """
                        UPDATE memories
                        SET
                            memory = %s,
                            category = %s,
                            importance = %s,
                            subject = %s
                        WHERE id = %s
                        """,
                        (
                            memory,
                            category,
                            importance,
                            subject,
                            exact_duplicate[0]
                        )
                    )

                    return {
                        "saved": True,
                        "action": (
                            "updated_exact_duplicate"
                        ),
                        "id": exact_duplicate[0]
                    }

                # --------------------------------------------
                # SEMANTIC DUPLICATE
                # --------------------------------------------

                subject_memories = (
                    get_subject_memories(
                        user_id,
                        subject
                    )
                )

                duplicate_id = (
                    find_semantic_duplicate(
                        api_key,
                        memory,
                        subject_memories
                    )
                )

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

                    return {
                        "saved": True,
                        "action": (
                            "updated_semantic_duplicate"
                        ),
                        "id": duplicate_id
                    }

                # --------------------------------------------
                # NEW MEMORY
                # --------------------------------------------

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

                new_id = cur.fetchone()[0]

                return {
                    "saved": True,
                    "action": "created",
                    "id": new_id
                }

    except Exception as e:

        return {
            "saved": False,
            "error": str(e)
        }


# ============================================================
# JSON CLEANER
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

    if (
        start != -1
        and end != -1
    ):

        text = text[
            start:end + 1
        ]

    return text


# ============================================================
# INTELLIGENT MEMORY ANALYSIS
# ============================================================

def analyze_memory(
    api_key,
    message
):

    prompt = f"""
You are the long-term memory system for a personal AI called Dusra Brain.

Analyze the user's message.

USER MESSAGE:
{message}

Decide whether this message contains a useful long-term personal fact,
preference, project, business, goal, relationship, instruction, decision,
or other information that should be remembered.

DO NOT remember:

- greetings
- casual conversation
- simple questions
- temporary requests
- general knowledge
- things that are only relevant to this single conversation

REMEMBER things such as:

- personal facts
- preferences
- important instructions
- projects
- businesses
- goals
- decisions
- relationships
- long-term plans

If the message should NOT be remembered, return:

{{
  "remember": false
}}

If the message SHOULD be remembered, return ONLY:

{{
  "remember": true,
  "memory": "A clean factual statement describing what the user wants remembered.",
  "category": "personal|preference|project|business|goal|relationship|work|decision|instruction|general",
  "importance": 1,
  "subject": "The specific person, project, business, product, organization, or topic that this memory is primarily about."
}}

IMPORTANCE:

Importance must be an integer from 1 to 10.

Use higher importance for information that is likely to remain
useful over a long period.

SUBJECT RULES:

- Identify the most specific subject explicitly mentioned in the message.
- If the message mentions "Carbon Mandi", use "Carbon Mandi".
- If the message mentions "Evolve India", use "Evolve India".
- If the message mentions "Dusra Brain", use "Dusra Brain".
- If the message mentions another clearly named project, business,
  product, organization, or person, use that specific name.
- Do NOT use "general" when a specific subject is clearly present.
- Do NOT invent a subject that is not supported by the message.
- Keep the subject short and consistent.
- Use the same name consistently when the user refers to the same
  project or business.
- If no specific subject exists, use "general".

CATEGORY RULES:

Use:

"project"
for a named project or long-term project.

"business"
for a business, company, commercial venture, or business activity.

"goal"
for a personal or professional objective.

"preference"
for something the user likes, dislikes, or prefers.

"instruction"
for a persistent instruction about how Dusra Brain should behave.

"decision"
for an important decision already made.

"work"
for professional information that does not fit the other categories.

"relationship"
for important information about a relationship.

"personal"
for important personal facts.

"general"
only when none of the above categories fit.

MEMORY RULES:

- Never invent information.
- Do not add facts that are not present in the user message.
- Keep the memory concise.
- Write the memory as a factual statement.
- Preserve the meaning of the user's statement.
"""

    try:

        response = groq_request(
            api_key,
            [
                {
                    "role": "system",
                    "content": (
                        "You are a precise long-term "
                        "memory extraction system."
                    )
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0
        )

        cleaned = clean_json_response(
            response
        )

        result = json.loads(
            cleaned
        )

        return result

    except Exception as e:

        return {
            "remember": False,
            "error": str(e)
        }


# ============================================================
# HTTP HANDLER
# ============================================================

class handler(BaseHTTPRequestHandler):

    # ========================================================
    # OPTIONS
    # ========================================================

    def do_OPTIONS(self):

        send_json(
            self,
            {
                "status": "ok"
            }
        )

    # ========================================================
    # DELETE MEMORY
    # ========================================================

    def do_DELETE(self):

        try:

            db_url = get_database_url()

            if not db_url:

                send_json(
                    self,
                    {
                        "error": (
                            "Database not detected"
                        )
                    },
                    500
                )

                return

            parsed_url = urlparse(
                self.path
            )

            query = parse_qs(
                parsed_url.query
            )

            memory_id = query.get(
                "memory_id",
                [None]
            )[0]

            if not memory_id:

                send_json(
                    self,
                    {
                        "error": (
                            "memory_id is required"
                        )
                    },
                    400
                )

                return

            try:

                memory_id = int(
                    memory_id
                )

            except ValueError:

                send_json(
                    self,
                    {
                        "error": (
                            "Invalid memory_id"
                        )
                    },
                    400
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
                        (memory_id,)
                    )

                    deleted = cur.fetchone()

            if not deleted:

                send_json(
                    self,
                    {
                        "deleted": False,
                        "error": (
                            "Memory not found"
                        )
                    },
                    404
                )

                return

            send_json(
                self,
                {
                    "deleted": True,
                    "memory_id": deleted[0]
                }
            )

        except Exception as e:

            send_json(
                self,
                {
                    "deleted": False,
                    "error": str(e)
                },
                500
            )

    # ========================================================
    # GET
    # ========================================================

    def do_GET(self):

        try:

            db_url = get_database_url()

            # ------------------------------------------------
            # MEMORY DASHBOARD API
            # ------------------------------------------------

            if "memories=true" in self.path:

                if not db_url:

                    send_json(
                        self,
                        {
                            "name": "Dusra Brain",
                            "memories": [],
                            "count": 0,
                            "error": (
                                "Database not detected"
                            )
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
                                memory,
                                category,
                                importance,
                                subject,
                                created_at
                            FROM memories
                            ORDER BY importance DESC,
                                     created_at DESC
                            LIMIT 200
                            """
                        )

                        rows = cur.fetchall()

                memories = []

                for row in rows:

                    memories.append(
                        {
                            "id": row[0],
                            "memory": row[1],
                            "category": row[2],
                            "importance": row[3],
                            "subject": row[4],
                            "created_at": (
                                row[5].isoformat()
                                if row[5]
                                else None
                            )
                        }
                    )

                send_json(
                    self,
                    {
                        "name": "Dusra Brain",
                        "memories": memories,
                        "count": len(memories)
                    }
                )

                return

            # ------------------------------------------------
            # NORMAL HEALTH CHECK
            # ------------------------------------------------

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
                        db_url
                    )
                }
            )

        except Exception as e:

            send_json(
                self,
                {
                    "name": "Dusra Brain",
                    "status": "error",
                    "error": str(e)
                },
                500
            )

    # ========================================================
    # POST
    # ========================================================

    def do_POST(self):

        conversation_user_saved = False
        conversation_assistant_saved = False
        conversation_error = None
        memory_error = None

        try:

            # ------------------------------------------------
            # READ REQUEST
            # ------------------------------------------------

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

            message = str(
                data.get(
                    "message",
                    ""
                )
            ).strip()

            user_id = str(
                data.get(
                    "user_id",
                    "default_user"
                )
            ).strip()

            if not message:

                send_json(
                    self,
                    {
                        "error": (
                            "Message is required"
                        )
                    },
                    400
                )

                return

            # ------------------------------------------------
            # API KEY
            # ------------------------------------------------

            api_key = os.environ.get(
                "GROQ_API_KEY"
            )

            if not api_key:

                send_json(
                    self,
                    {
                        "error": (
                            "GROQ_API_KEY is not configured"
                        )
                    },
                    500
                )

                return

            # ------------------------------------------------
            # SAVE USER CONVERSATION
            # ------------------------------------------------

            try:

                conversation_user_saved = (
                    save_conversation(
                        user_id,
                        "user",
                        message
                    )
                )

            except Exception as e:

                conversation_error = str(e)

            # ------------------------------------------------
            # GET LONG-TERM MEMORIES
            # ------------------------------------------------

            memories = get_memories(
                user_id,
                message
            )

            memory_text = ""

            if memories:

                memory_lines = []

                for item in memories:

                    memory_lines.append(
                        f"- {item['memory']} "
                        f"(category: "
                        f"{item['category']}, "
                        f"importance: "
                        f"{item['importance']}, "
                        f"subject: "
                        f"{item['subject']})"
                    )

                memory_text = "\n".join(
                    memory_lines
                )

            else:

                memory_text = (
                    "No stored long-term memories."
                )

            # ------------------------------------------------
            # GET RECENT CONVERSATION
            # ------------------------------------------------

            conversation_history = (
                get_conversation_history(
                    user_id,
                    limit=20
                )
            )

            # ------------------------------------------------
            # SYSTEM PROMPT
            # ------------------------------------------------

            system_prompt = """
You are Dusra Brain, a personal AI brain and memory assistant.

You help the user think, remember, organize information,
and work on their projects.

You have access to:

1. Long-term memories
2. Recent conversation history
3. The current user message

IMPORTANT PERSONAL-FACT RULES:

- Stored memories represent facts explicitly provided by the user.
- For questions about the user's personal life, projects,
  businesses, goals, preferences, decisions, or relationships,
  use only information contained in stored memories,
  recent conversation, or the current message.
- Never invent personal facts.
- Never assume missing details.
- Never expand a stored fact with information that was not provided.
- If there is not enough information, say that clearly.

CONVERSATION RULES:

- Use recent conversation to understand context.
- Do not repeat questions the user already answered.
- Answer naturally and directly.
- Keep answers useful and concise unless the user asks for detail.

MEMORY RULES:

- Long-term memory is separate from recent conversation.
- Do not claim that something is stored long-term unless it actually
  appears in the provided long-term memories.
"""

            # ------------------------------------------------
            # BUILD MESSAGES
            # ------------------------------------------------

            messages = [
                {
                    "role": "system",
                    "content": system_prompt
                }
            ]

            # ------------------------------------------------
            # LONG-TERM MEMORY CONTEXT
            # ------------------------------------------------

            messages.append(
                {
                    "role": "system",
                    "content": (
                        "LONG-TERM MEMORIES:\n"
                        + memory_text
                    )
                }
            )

            # ------------------------------------------------
            # RECENT CONVERSATION
            # ------------------------------------------------

            for item in conversation_history:

                role = item.get(
                    "role"
                )

                content = item.get(
                    "content"
                )

                if role in [
                    "user",
                    "assistant"
                ]:

                    messages.append(
                        {
                            "role": role,
                            "content": content
                        }
                    )

            # ------------------------------------------------
            # CURRENT MESSAGE
            # ------------------------------------------------

            messages.append(
                {
                    "role": "user",
                    "content": message
                }
            )

            # ------------------------------------------------
            # ASK GROQ
            # ------------------------------------------------

            answer = groq_request(
                api_key,
                messages,
                temperature=0.3
            )

            # ------------------------------------------------
            # SAVE ASSISTANT CONVERSATION
            # ------------------------------------------------

            try:

                conversation_assistant_saved = (
                    save_conversation(
                        user_id,
                        "assistant",
                        answer
                    )
                )

            except Exception as e:

                if conversation_error:

                    conversation_error += (
                        " | " + str(e)
                    )

                else:

                    conversation_error = str(e)

            # ------------------------------------------------
            # ANALYZE LONG-TERM MEMORY
            # ------------------------------------------------

            memory_result = analyze_memory(
                api_key,
                message
            )

            memory_saved = False
            memory_action = None
            memory_id = None

            if memory_result.get(
                "remember"
            ) is True:

                memory_text_value = str(
                    memory_result.get(
                        "memory",
                        ""
                    )
                ).strip()

                category = str(
                    memory_result.get(
                        "category",
                        "general"
                    )
                ).strip()

                subject = str(
                    memory_result.get(
                        "subject",
                        "general"
                    )
                ).strip()

                # --------------------------------------------
                # SUBJECT NORMALIZATION
                # --------------------------------------------

                if not subject:

                    subject = "general"

                subject_lower = (
                    subject.lower().strip()
                )

                known_subjects = {
                    "carbon mandi": "Carbon Mandi",
                    "evolve india": "Evolve India",
                    "dusra brain": "Dusra Brain"
                }

                if subject_lower in known_subjects:

                    subject = known_subjects[
                        subject_lower
                    ]

                try:

                    importance = int(
                        memory_result.get(
                            "importance",
                            5
                        )
                    )

                except Exception:

                    importance = 5

                importance = max(
                    1,
                    min(
                        10,
                        importance
                    )
                )

                if memory_text_value:

                    save_result = save_memory(
                        user_id,
                        memory_text_value,
                        category,
                        importance,
                        subject,
                        api_key
                    )

                    memory_saved = (
                        save_result.get(
                            "saved",
                            False
                        )
                    )

                    memory_action = (
                        save_result.get(
                            "action"
                        )
                    )

                    memory_id = (
                        save_result.get(
                            "id"
                        )
                    )

                    if not memory_saved:

                        memory_error = (
                            save_result.get(
                                "error"
                            )
                        )

            # ------------------------------------------------
            # FINAL RESPONSE
            # ------------------------------------------------

            send_json(
                self,
                {
                    "reply": answer,

                    "conversation_user_saved":
                        conversation_user_saved,

                    "conversation_assistant_saved":
                        conversation_assistant_saved,

                    "conversation_error":
                        conversation_error,

                    "conversation_messages_used":
                        len(
                            conversation_history
                        ),

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

        except json.JSONDecodeError:

            send_json(
                self,
                {
                    "error": (
                        "Invalid JSON request"
                    )
                },
                400
            )

        except Exception as e:

            send_json(
                self,
                {
                    "error": str(e)
                },
                500
            )
