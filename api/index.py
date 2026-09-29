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
        ensure_ascii=False
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
        "GET,POST,PUT,DELETE,OPTIONS"
    )

    handler.send_header(
        "Access-Control-Allow-Headers",
        "Content-Type"
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
        raise Exception(
            "Database connection string not found"
        )

    return psycopg.connect(
        database_url
    )


# ============================================================
# GROQ
# ============================================================

def groq_request(
    messages,
    temperature=0.2,
    max_completion_tokens=None,
    retry_429=True
):

    api_key = os.environ.get(
        "GROQ_API_KEY"
    )

    if not api_key:
        raise Exception(
            "GROQ_API_KEY is missing"
        )

    payload = {
        "model": "openai/gpt-oss-120b",
        "messages": messages,
        "temperature": temperature,
    }

    if max_completion_tokens is not None:
        payload["max_completion_tokens"] = int(
            max(1, max_completion_tokens)
        )

    request = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",

        data=json.dumps(
            payload
        ).encode("utf-8"),

        headers={
            "Content-Type":
                "application/json",

            "Authorization":
                "Bearer " + api_key,

            "User-Agent":
                "Mozilla/5.0",
        },

        method="POST"
    )

    try:

        with urllib.request.urlopen(
            request,
            timeout=60
        ) as response:

            raw = response.read().decode(
                "utf-8"
            )

            data = json.loads(
                raw
            )

            return data[
                "choices"
            ][0][
                "message"
            ][
                "content"
            ]

    except urllib.error.HTTPError as error:

        details = error.read().decode(
            "utf-8"
        )

        if (
            error.code == 429
            and retry_429
        ):
            retry_after = 8

            try:
                retry_after = int(
                    float(
                        error.headers.get(
                            "retry-after",
                            "8"
                        )
                    )
                )
            except Exception:
                pass

            retry_after = max(
                1,
                min(12, retry_after)
            )

            import time
            time.sleep(retry_after)

            return groq_request(
                messages,
                temperature=temperature,
                max_completion_tokens=max_completion_tokens,
                retry_429=False
            )

        raise Exception(
            "Groq API error "
            + str(error.code)
            + ": "
            + details
        )


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
            "created_at":
                row[2].isoformat()
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
            "created_at":
                row[3].isoformat()
                if row[3]
                else None,
            "session_id":
                row[4] or "default",
            "title":
                row[5] or "New Chat",
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
                (
                    user_id,
                )
            )

            rows = cur.fetchall()

    sessions = []

    for row in rows:

        sessions.append(
            {
                "session_id":
                    row[0] or "default",

                "title":
                    row[1] or "New Chat",

                "last_message_at":
                    row[2].isoformat()
                    if row[2]
                    else None,

                "message_count":
                    row[3],
            }
        )

    if not sessions:

        sessions.append(
            {
                "session_id":
                    "default",

                "title":
                    "New Chat",

                "last_message_at":
                    None,

                "message_count":
                    0,
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
                  AND
                  (
                      session_id = %s
                      OR session_id = 'default'
                  )
                ORDER BY
                    CASE
                        WHEN session_id = %s
                        THEN 0
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
            "created_at":
                row[2].isoformat()
                if row[2]
                else None,
            "category":
                row[3] or "general",
            "importance":
                row[4] or 5,
            "subject":
                row[5] or "general",
            "memory_key":
                row[6],
            "session_id":
                row[7] or "default",
        }
        for row in rows
    ]


def get_all_user_memories(
    user_id,
    limit=500
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
                ORDER BY
                    importance DESC,
                    created_at DESC
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
            "memory": row[1],
            "created_at":
                row[2].isoformat()
                if row[2]
                else None,
            "category":
                row[3] or "general",
            "importance":
                row[4] or 5,
            "subject":
                row[5] or "general",
            "memory_key":
                row[6],
            "session_id":
                row[7] or "default",
        }
        for row in rows
    ]


def get_memory_subjects(user_id):

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT DISTINCT subject
                FROM memories
                WHERE user_id = %s
                  AND subject IS NOT NULL
                  AND subject <> ''
                ORDER BY subject
                """,
                (
                    user_id,
                )
            )

            rows = cur.fetchall()

    return [
        row[0]
        for row in rows
        if row[0]
    ]


# ============================================================
# SUBJECT DETECTION
# ============================================================

def detect_subject(
    user_message,
    available_subjects
):

    if not available_subjects:
        return None

    subjects_text = "\n".join(
        [
            "- " + subject
            for subject in available_subjects
        ]
    )

    prompt = f"""
Identify whether the user's message refers to
one of the stored subjects below.

USER MESSAGE:
{user_message}

AVAILABLE SUBJECTS:
{subjects_text}

Rules:

1. Return the exact subject name if the user is clearly
asking about or referring to that subject.

2. Match obvious variations and abbreviations.

3. If there is no clear subject match, return null.

Return ONLY JSON:

{{
  "subject": null
}}

or:

{{
  "subject": "Exact Subject Name"
}}
"""

    try:

        response = groq_request(
            [
                {
                    "role":
                        "system",

                    "content":
                        "You identify subjects from stored personal memory."
                },

                {
                    "role":
                        "user",

                    "content":
                        prompt
                }
            ],
            temperature=0
        )

        data = json.loads(
            clean_json_response(
                response
            )
        )

        detected = data.get(
            "subject"
        )

        if not detected:
            return None

        for subject in available_subjects:

            if subject.lower() == str(
                detected
            ).strip().lower():

                return subject

        return None

    except Exception:

        return None


def get_subject_memories(
    user_id,
    subject,
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
                  AND subject = %s
                ORDER BY
                    CASE
                        WHEN session_id = %s
                        THEN 0
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
                    limit,
                )
            )

            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "memory": row[1],
            "created_at":
                row[2].isoformat()
                if row[2]
                else None,
            "category":
                row[3] or "general",
            "importance":
                row[4] or 5,
            "subject":
                row[5] or "general",
            "memory_key":
                row[6],
            "session_id":
                row[7] or "default",
        }
        for row in rows
    ]


def get_relevant_memories(
    user_id,
    message,
    session_id="default",
    limit=50
):

    base_memories = get_memories(
        user_id,
        message=message,
        session_id=session_id,
        limit=limit
    )

    subjects = get_memory_subjects(
        user_id
    )

    detected_subject = detect_subject(
        message,
        subjects
    )

    if detected_subject:

        subject_memories = get_subject_memories(
            user_id,
            detected_subject,
            session_id=session_id,
            limit=50
        )

        combined = []

        seen_ids = set()

        for item in subject_memories:

            if item["id"] not in seen_ids:

                combined.append(item)

                seen_ids.add(
                    item["id"]
                )

        for item in base_memories:

            if item["id"] not in seen_ids:

                combined.append(item)

                seen_ids.add(
                    item["id"]
                )

        return combined[:limit]

    return base_memories


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

Return ONLY JSON.

If duplicate:

{{
  "duplicate_id": 123
}}

If not duplicate:

{{
  "duplicate_id": null
}}

Only mark a memory as duplicate when the meaning
is substantially the same.
"""

    try:

        response = groq_request(
            [
                {
                    "role":
                        "system",

                    "content":
                        "You are a precise memory deduplication system."
                },

                {
                    "role":
                        "user",

                    "content":
                        prompt
                }
            ],
            temperature=0
        )

        data = json.loads(
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


# ============================================================
# PHASE 6 — MEMORY VERSIONING
# ============================================================

def ensure_memory_versions_table():

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_versions
                (
                    id SERIAL PRIMARY KEY,
                    memory_id INTEGER NOT NULL,
                    user_id TEXT NOT NULL,
                    version_number INTEGER NOT NULL,
                    memory TEXT NOT NULL,
                    category TEXT DEFAULT 'general',
                    importance INTEGER DEFAULT 5,
                    subject TEXT DEFAULT 'general',
                    memory_key TEXT,
                    session_id TEXT DEFAULT 'default',
                    change_type TEXT NOT NULL,
                    change_reason TEXT,
                    is_current BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(memory_id, version_number)
                )
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_memory_versions_memory_id
                ON memory_versions(memory_id)
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_memory_versions_user_id
                ON memory_versions(user_id)
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_memory_versions_subject
                ON memory_versions(subject)
                """
            )

        conn.commit()


def get_next_memory_version_number(
    cur,
    memory_id
):

    cur.execute(
        """
        SELECT
            COALESCE(
                MAX(version_number),
                0
            )
        FROM memory_versions
        WHERE memory_id = %s
        """,
        (
            memory_id,
        )
    )

    row = cur.fetchone()

    return int(row[0] or 0) + 1


def record_memory_version(
    cur,
    memory_id,
    user_id,
    memory,
    category,
    importance,
    subject,
    memory_key,
    session_id,
    change_type,
    change_reason
):

    version_number = get_next_memory_version_number(
        cur,
        memory_id
    )

    cur.execute(
        """
        UPDATE memory_versions
        SET is_current = FALSE
        WHERE memory_id = %s
        """,
        (
            memory_id,
        )
    )

    cur.execute(
        """
        INSERT INTO memory_versions
        (
            memory_id,
            user_id,
            version_number,
            memory,
            category,
            importance,
            subject,
            memory_key,
            session_id,
            change_type,
            change_reason,
            is_current
        )
        VALUES
        (
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            TRUE
        )
        RETURNING
            id,
            version_number,
            created_at
        """,
        (
            memory_id,
            user_id,
            version_number,
            memory,
            category,
            importance,
            subject,
            memory_key,
            session_id,
            change_type,
            change_reason,
        )
    )

    row = cur.fetchone()

    return {
        "id": row[0],
        "version_number": row[1],
        "created_at":
            row[2].isoformat()
            if row[2]
            else None,
    }



def seed_existing_memory_versions(user_id=None):
    """Create Version 1 baselines for memories that predate versioning.

    This copies existing memory state into memory_versions only.
    It never changes or deletes rows in memories.
    """
    ensure_memory_versions_table()

    with get_connection() as conn:

        with conn.cursor() as cur:

            if user_id:

                cur.execute(
                    """
                    SELECT
                        id,
                        user_id,
                        memory,
                        category,
                        importance,
                        subject,
                        memory_key,
                        session_id
                    FROM memories
                    WHERE user_id = %s
                    ORDER BY id
                    """,
                    (
                        user_id,
                    )
                )

            else:

                cur.execute(
                    """
                    SELECT
                        id,
                        user_id,
                        memory,
                        category,
                        importance,
                        subject,
                        memory_key,
                        session_id
                    FROM memories
                    ORDER BY id
                    """
                )

            rows = cur.fetchall()

            seeded = 0

            for row in rows:

                memory_id = int(
                    row[0]
                )

                cur.execute(
                    """
                    SELECT 1
                    FROM memory_versions
                    WHERE memory_id = %s
                    LIMIT 1
                    """,
                    (
                        memory_id,
                    )
                )

                if cur.fetchone():
                    continue

                record_memory_version(
                    cur,
                    memory_id,
                    row[1],
                    memory=str(
                        row[2] or ""
                    ),
                    category=row[3] or "general",
                    importance=int(
                        row[4] or 5
                    ),
                    subject=row[5] or "general",
                    memory_key=row[6],
                    session_id=row[7] or "default",
                    change_type="created",
                    change_reason="initial_version_baseline",
                )

                seeded += 1

        conn.commit()

    return {
        "seeded": seeded,
        "user_id": user_id,
    }



def update_memory_with_version(
    user_id,
    memory_id,
    memory=None,
    category=None,
    importance=None,
    subject=None,
    session_id=None,
    change_reason="memory_updated"
):

    """Explicitly update one memory while preserving its previous state.

    This is the controlled Version 1 -> Version 2 pathway.
    The previous state is written to memory_versions before the live
    memory row is changed. No memory is deleted.
    """

    ensure_memory_versions_table()

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    id,
                    user_id,
                    memory,
                    category,
                    importance,
                    subject,
                    memory_key,
                    session_id
                FROM memories
                WHERE id = %s
                  AND user_id = %s
                FOR UPDATE
                """,
                (
                    int(memory_id),
                    user_id,
                )
            )

            current = cur.fetchone()

            if not current:

                return {
                    "updated": False,
                    "error":
                        "Memory not found."
                }

            current_memory = str(
                current[2] or ""
            )
            current_category = (
                current[3]
                or "general"
            )
            current_importance = int(
                current[4]
                or 5
            )
            current_subject = (
                current[5]
                or "general"
            )
            current_memory_key = current[6]
            current_session_id = (
                current[7]
                or "default"
            )

            new_memory = (
                current_memory
                if memory is None
                else str(memory).strip()
            )

            new_category = (
                current_category
                if category is None
                else str(category).strip()
            )

            new_importance = (
                current_importance
                if importance is None
                else int(importance)
            )

            new_subject = (
                current_subject
                if subject is None
                else str(subject).strip()
            )

            new_session_id = (
                current_session_id
                if session_id is None
                else str(session_id).strip()
            )

            new_memory_key = make_memory_key(
                new_subject,
                new_category,
                new_memory
            )

            changed = any(
                [
                    current_memory != new_memory,
                    current_category != new_category,
                    current_importance != new_importance,
                    current_subject != new_subject,
                    current_memory_key != new_memory_key,
                    current_session_id != new_session_id,
                ]
            )

            if not changed:

                conn.commit()

                return {
                    "updated": False,
                    "changed": False,
                    "memory_id": int(memory_id),
                    "message":
                        "No changes detected.",
                    "current_version":
                        get_memory_versions(
                            user_id,
                            memory_id=int(memory_id)
                        )[0]
                        if get_memory_versions(
                            user_id,
                            memory_id=int(memory_id)
                        )
                        else None,
                }

            # Preserve the exact previous state first.
            previous_version = record_memory_version(
                cur,
                int(memory_id),
                user_id,
                memory=current_memory,
                category=current_category,
                importance=current_importance,
                subject=current_subject,
                memory_key=current_memory_key,
                session_id=current_session_id,
                change_type="previous",
                change_reason=change_reason,
            )

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
                  AND user_id = %s
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
                    new_memory,
                    new_category,
                    new_importance,
                    new_subject,
                    new_memory_key,
                    new_session_id,
                    int(memory_id),
                    user_id,
                )
            )

            updated_row = cur.fetchone()

            current_version = record_memory_version(
                cur,
                int(memory_id),
                user_id,
                memory=new_memory,
                category=new_category,
                importance=new_importance,
                subject=new_subject,
                memory_key=new_memory_key,
                session_id=new_session_id,
                change_type="updated",
                change_reason=change_reason,
            )

        conn.commit()

    return {
        "updated": True,
        "changed": True,
        "memory_id": int(memory_id),
        "previous_version": previous_version,
        "current_version": current_version,
        "memory": {
            "id": updated_row[0],
            "memory": updated_row[1],
            "created_at":
                updated_row[2].isoformat()
                if updated_row[2]
                else None,
            "category": updated_row[3],
            "importance": updated_row[4],
            "subject": updated_row[5],
            "memory_key": updated_row[6],
            "session_id": updated_row[7],
        },
        "memory_deleted": False,
    }


def get_memory_versions(
    user_id,
    memory_id=None,
    subject=""
):

    ensure_memory_versions_table()
    seed_existing_memory_versions(
        user_id=user_id
    )

    with get_connection() as conn:

        with conn.cursor() as cur:

            if memory_id is not None:

                cur.execute(
                    """
                    SELECT
                        mv.id,
                        mv.memory_id,
                        mv.version_number,
                        mv.memory,
                        mv.category,
                        mv.importance,
                        mv.subject,
                        mv.memory_key,
                        mv.session_id,
                        mv.change_type,
                        mv.change_reason,
                        mv.is_current,
                        mv.created_at
                    FROM memory_versions mv
                    INNER JOIN memories m
                        ON m.id = mv.memory_id
                    WHERE mv.user_id = %s
                      AND mv.memory_id = %s
                    ORDER BY
                        mv.version_number DESC
                    """,
                    (
                        user_id,
                        int(memory_id),
                    )
                )

            elif subject:

                cur.execute(
                    """
                    SELECT
                        mv.id,
                        mv.memory_id,
                        mv.version_number,
                        mv.memory,
                        mv.category,
                        mv.importance,
                        mv.subject,
                        mv.memory_key,
                        mv.session_id,
                        mv.change_type,
                        mv.change_reason,
                        mv.is_current,
                        mv.created_at
                    FROM memory_versions mv
                    INNER JOIN memories m
                        ON m.id = mv.memory_id
                    WHERE mv.user_id = %s
                      AND LOWER(mv.subject) = LOWER(%s)
                    ORDER BY
                        mv.memory_id,
                        mv.version_number DESC
                    """,
                    (
                        user_id,
                        subject,
                    )
                )

            else:

                cur.execute(
                    """
                    SELECT
                        mv.id,
                        mv.memory_id,
                        mv.version_number,
                        mv.memory,
                        mv.category,
                        mv.importance,
                        mv.subject,
                        mv.memory_key,
                        mv.session_id,
                        mv.change_type,
                        mv.change_reason,
                        mv.is_current,
                        mv.created_at
                    FROM memory_versions mv
                    INNER JOIN memories m
                        ON m.id = mv.memory_id
                    WHERE mv.user_id = %s
                    ORDER BY
                        mv.memory_id,
                        mv.version_number DESC
                    LIMIT 500
                    """,
                    (
                        user_id,
                    )
                )

            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "memory_id": row[1],
            "version_number": row[2],
            "memory": row[3],
            "category": row[4] or "general",
            "importance": row[5] or 5,
            "subject": row[6] or "general",
            "memory_key": row[7],
            "session_id": row[8] or "default",
            "change_type": row[9],
            "change_reason": row[10],
            "is_current": bool(row[11]),
            "created_at":
                row[12].isoformat()
                if row[12]
                else None,
        }
        for row in rows
    ]


def get_memory_version_summary(
    user_id
):

    ensure_memory_versions_table()

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    COUNT(*) AS version_count,
                    COUNT(
                        DISTINCT memory_id
                    ) AS memory_count,
                    COALESCE(
                        MAX(version_number),
                        0
                    ) AS highest_version
                FROM memory_versions
                WHERE user_id = %s
                """,
                (
                    user_id,
                )
            )

            row = cur.fetchone()

    return {
        "version_count": int(
            row[0] or 0
        ),
        "memory_count": int(
            row[1] or 0
        ),
        "highest_version": int(
            row[2] or 0
        ),
    }


def save_memory(
    user_id,
    memory,
    category="general",
    importance=5,
    subject="general",
    session_id="default"
):

    ensure_memory_versions_table()

    memory_key = make_memory_key(
        subject,
        category,
        memory
    )

    existing_memories = get_subject_memories(
        user_id,
        subject,
        session_id=session_id
    )

    duplicate_id = find_semantic_duplicate(
        memory,
        existing_memories
    )

    with get_connection() as conn:

        with conn.cursor() as cur:

            target_id = None

            if duplicate_id:
                target_id = int(
                    duplicate_id
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

                exact = cur.fetchone()

                if exact:
                    target_id = int(
                        exact[0]
                    )

            if target_id is not None:

                # Read the complete current state before changing it.
                cur.execute(
                    """
                    SELECT
                        id,
                        user_id,
                        memory,
                        category,
                        importance,
                        subject,
                        memory_key,
                        session_id
                    FROM memories
                    WHERE id = %s
                      AND user_id = %s
                    FOR UPDATE
                    """,
                    (
                        target_id,
                        user_id,
                    )
                )

                current = cur.fetchone()

                if not current:
                    target_id = None

                else:

                    current_memory = str(
                        current[2] or ""
                    )
                    current_category = (
                        current[3]
                        or "general"
                    )
                    current_importance = int(
                        current[4]
                        or 5
                    )
                    current_subject = (
                        current[5]
                        or "general"
                    )
                    current_memory_key = (
                        current[6]
                    )
                    current_session_id = (
                        current[7]
                        or "default"
                    )

                    new_session_id = (
                        session_id
                        or "default"
                    )

                    has_changed = any(
                        [
                            current_memory != str(
                                memory or ""
                            ),
                            current_category != category,
                            current_importance != int(
                                importance
                            ),
                            current_subject != subject,
                            current_memory_key != memory_key,
                            current_session_id != new_session_id,
                        ]
                    )

                    if has_changed:

                        record_memory_version(
                            cur,
                            target_id,
                            user_id,
                            memory=current_memory,
                            category=current_category,
                            importance=current_importance,
                            subject=current_subject,
                            memory_key=current_memory_key,
                            session_id=current_session_id,
                            change_type="previous",
                            change_reason="superseded_by_update",
                        )

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
                                new_session_id,
                                target_id,
                            )
                        )

                        row = cur.fetchone()

                        record_memory_version(
                            cur,
                            target_id,
                            user_id,
                            memory=str(
                                memory or ""
                            ),
                            category=category,
                            importance=int(
                                importance
                            ),
                            subject=subject,
                            memory_key=memory_key,
                            session_id=new_session_id,
                            change_type="updated",
                            change_reason="memory_updated",
                        )

                    else:

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
                            WHERE id = %s
                            """,
                            (
                                target_id,
                            )
                        )

                        row = cur.fetchone()

                        # If this memory existed before versioning was
                        # introduced, create its initial baseline now.
                        cur.execute(
                            """
                            SELECT 1
                            FROM memory_versions
                            WHERE memory_id = %s
                            LIMIT 1
                            """,
                            (
                                target_id,
                            )
                        )

                        if not cur.fetchone():

                            record_memory_version(
                                cur,
                                target_id,
                                user_id,
                                memory=str(
                                    row[1] or ""
                                ),
                                category=row[3] or "general",
                                importance=int(
                                    row[4] or 5
                                ),
                                subject=row[5] or "general",
                                memory_key=row[6],
                                session_id=row[7] or "default",
                                change_type="created",
                                change_reason="versioning_baseline",
                            )

            if target_id is None:

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
                        session_id or "default",
                    )
                )

                row = cur.fetchone()

                record_memory_version(
                    cur,
                    row[0],
                    user_id,
                    memory=str(
                        memory or ""
                    ),
                    category=category,
                    importance=int(
                        importance
                    ),
                    subject=subject,
                    memory_key=memory_key,
                    session_id=session_id or "default",
                    change_type="created",
                    change_reason="memory_created",
                )

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
        "session_id":
            row[7] or "default",
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

Never invent facts.
Only extract information explicitly stated by the user.
"""

    try:

        response = groq_request(
            [
                {
                    "role":
                        "system",

                    "content":
                        "You are a personal memory extraction system. Never invent user facts."
                },

                {
                    "role":
                        "user",

                    "content":
                        prompt
                }
            ],
            temperature=0
        )

        return json.loads(
            clean_json_response(
                response
            )
        )

    except Exception:

        return {
            "remember":
                False
        }


def normalize_subject(subject):

    if not subject:
        return "general"

    subject = str(
        subject
    ).strip()

    if not subject:
        return "general"

    return subject[:200]


# ============================================================
# BRAIN ENTITIES
# ============================================================

def extract_brain_structure(
    user_message,
    current_subject="general"
):

    prompt = f"""
You are the structured knowledge extraction engine
for a personal AI brain called Dusra Brain.

Extract ONLY facts explicitly stated by the user.

USER MESSAGE:
{user_message}

CURRENT SUBJECT:
{current_subject}

Identify important entities.

Allowed entity types:
- person
- company
- project
- product
- organization
- location
- goal
- decision
- preference
- other

For every entity provide:

name
type
description
importance

Then identify relationships between entities.

A relationship must connect two entities from the
entities list.

Examples:

Evolve India -> brings -> Evolve Lubricants
Evolve Lubricants -> originates_from -> USA
Evolve India -> operates_in -> India

Do NOT invent relationships.

Return ONLY JSON in this format:

{{
  "entities": [
    {{
      "name": "Evolve India",
      "type": "project",
      "description": "Project explicitly mentioned by the user",
      "importance": 8
    }}
  ],
  "relationships": [
    {{
      "from": "Evolve India",
      "relationship": "brings",
      "to": "Evolve Lubricants",
      "confidence": 8
    }}
  ]
}}

If nothing meaningful can be extracted:

{{
  "entities": [],
  "relationships": []
}}
"""

    try:

        response = groq_request(
            [
                {
                    "role":
                        "system",

                    "content":
                        "You extract structured personal knowledge. Never invent facts."
                },

                {
                    "role":
                        "user",

                    "content":
                        prompt
                }
            ],
            temperature=0
        )

        data = json.loads(
            clean_json_response(
                response
            )
        )

        if not isinstance(data, dict):
            return {
                "entities": [],
                "relationships": []
            }

        return data

    except Exception:

        return {
            "entities": [],
            "relationships": []
        }


def normalize_entity_type(entity_type):

    allowed = {
        "person",
        "company",
        "project",
        "product",
        "organization",
        "location",
        "goal",
        "decision",
        "preference",
        "other",
    }

    value = str(
        entity_type or "other"
    ).strip().lower()

    if value not in allowed:
        return "other"

    return value


def save_brain_entity(
    user_id,
    entity_type,
    name,
    description="",
    importance=5
):

    entity_type = normalize_entity_type(
        entity_type
    )

    name = str(
        name or ""
    ).strip()

    description = str(
        description or ""
    ).strip()

    try:
        importance = int(
            importance
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

    if not name:
        return None

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                INSERT INTO brain_entities
                (
                    user_id,
                    entity_type,
                    name,
                    description,
                    importance,
                    updated_at
                )
                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    CURRENT_TIMESTAMP
                )
                ON CONFLICT
                (
                    user_id,
                    entity_type,
                    name
                )
                DO UPDATE SET
                    description =
                        CASE
                            WHEN EXCLUDED.description <> ''
                            THEN EXCLUDED.description
                            ELSE brain_entities.description
                        END,
                    importance =
                        GREATEST(
                            brain_entities.importance,
                            EXCLUDED.importance
                        ),
                    updated_at =
                        CURRENT_TIMESTAMP
                RETURNING
                    id,
                    user_id,
                    entity_type,
                    name,
                    description,
                    importance,
                    created_at,
                    updated_at
                """,
                (
                    user_id,
                    entity_type,
                    name,
                    description,
                    importance,
                )
            )

            row = cur.fetchone()

        conn.commit()

    return {
        "id": row[0],
        "user_id": row[1],
        "entity_type": row[2],
        "name": row[3],
        "description": row[4],
        "importance": row[5],
        "created_at":
            row[6].isoformat()
            if row[6]
            else None,
        "updated_at":
            row[7].isoformat()
            if row[7]
            else None,
    }


def save_brain_relationship(
    user_id,
    from_entity_id,
    relationship,
    to_entity_id,
    confidence=5
):

    relationship = str(
        relationship or ""
    ).strip()

    if not relationship:
        return None

    try:
        confidence = int(
            confidence
        )
    except Exception:
        confidence = 5

    confidence = max(
        1,
        min(
            10,
            confidence
        )
    )

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    id,
                    confidence
                FROM brain_relationships
                WHERE user_id = %s
                  AND from_entity_id = %s
                  AND relationship = %s
                  AND to_entity_id = %s
                LIMIT 1
                """,
                (
                    user_id,
                    from_entity_id,
                    relationship,
                    to_entity_id,
                )
            )

            existing = cur.fetchone()

            if existing:

                cur.execute(
                    """
                    UPDATE brain_relationships
                    SET
                        confidence = GREATEST(
                            confidence,
                            %s
                        )
                    WHERE id = %s
                    RETURNING id
                    """,
                    (
                        confidence,
                        existing[0],
                    )
                )

            else:

                cur.execute(
                    """
                    INSERT INTO brain_relationships
                    (
                        user_id,
                        from_entity_id,
                        relationship,
                        to_entity_id,
                        confidence
                    )
                    VALUES
                    (
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
                        from_entity_id,
                        relationship,
                        to_entity_id,
                        confidence,
                    )
                )

            row = cur.fetchone()

        conn.commit()

    return row[0] if row else None


def save_brain_structure(
    user_id,
    user_message,
    current_subject="general"
):

    structure = extract_brain_structure(
        user_message,
        current_subject
    )

    entities = structure.get(
        "entities",
        []
    )

    relationships = structure.get(
        "relationships",
        []
    )

    entity_map = {}

    saved_entities = []

    for entity in entities:

        if not isinstance(
            entity,
            dict
        ):
            continue

        saved = save_brain_entity(
            user_id=user_id,

            entity_type=entity.get(
                "type",
                "other"
            ),

            name=entity.get(
                "name",
                ""
            ),

            description=entity.get(
                "description",
                ""
            ),

            importance=entity.get(
                "importance",
                5
            )
        )

        if saved:

            key = saved["name"].strip().lower()

            entity_map[key] = saved

            saved_entities.append(
                saved
            )

    saved_relationships = []

    for relation in relationships:

        if not isinstance(
            relation,
            dict
        ):
            continue

        from_name = str(
            relation.get(
                "from",
                ""
            )
        ).strip().lower()

        to_name = str(
            relation.get(
                "to",
                ""
            )
        ).strip().lower()

        if not from_name or not to_name:
            continue

        from_entity = entity_map.get(
            from_name
        )

        to_entity = entity_map.get(
            to_name
        )

        if not from_entity:

            with get_connection() as conn:

                with conn.cursor() as cur:

                    cur.execute(
                        """
                        SELECT
                            id,
                            user_id,
                            entity_type,
                            name,
                            description,
                            importance
                        FROM brain_entities
                        WHERE user_id = %s
                          AND LOWER(name) = %s
                        ORDER BY importance DESC
                        LIMIT 1
                        """,
                        (
                            user_id,
                            from_name,
                        )
                    )

                    row = cur.fetchone()

            if row:

                from_entity = {
                    "id": row[0],
                    "user_id": row[1],
                    "entity_type": row[2],
                    "name": row[3],
                    "description": row[4],
                    "importance": row[5],
                }

        if not to_entity:

            with get_connection() as conn:

                with conn.cursor() as cur:

                    cur.execute(
                        """
                        SELECT
                            id,
                            user_id,
                            entity_type,
                            name,
                            description,
                            importance
                        FROM brain_entities
                        WHERE user_id = %s
                          AND LOWER(name) = %s
                        ORDER BY importance DESC
                        LIMIT 1
                        """,
                        (
                            user_id,
                            to_name,
                        )
                    )

                    row = cur.fetchone()

            if row:

                to_entity = {
                    "id": row[0],
                    "user_id": row[1],
                    "entity_type": row[2],
                    "name": row[3],
                    "description": row[4],
                    "importance": row[5],
                }

        if not from_entity or not to_entity:
            continue

        relation_id = save_brain_relationship(
            user_id=user_id,
            from_entity_id=from_entity["id"],
            relationship=relation.get(
                "relationship",
                "related_to"
            ),
            to_entity_id=to_entity["id"],
            confidence=relation.get(
                "confidence",
                5
            )
        )

        if relation_id:

            saved_relationships.append(
                {
                    "id":
                        relation_id,

                    "from":
                        from_entity["name"],

                    "relationship":
                        relation.get(
                            "relationship",
                            "related_to"
                        ),

                    "to":
                        to_entity["name"],
                }
            )

    return {
        "entities":
            saved_entities,

        "relationships":
            saved_relationships,
    }


# ============================================================
# BRAIN READ
# ============================================================

def get_brain_entities(
    user_id,
    limit=500
):

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    id,
                    user_id,
                    entity_type,
                    name,
                    description,
                    importance,
                    created_at,
                    updated_at
                FROM brain_entities
                WHERE user_id = %s
                ORDER BY
                    importance DESC,
                    updated_at DESC
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
            "user_id": row[1],
            "entity_type": row[2],
            "name": row[3],
            "description": row[4],
            "importance": row[5],
            "created_at":
                row[6].isoformat()
                if row[6]
                else None,
            "updated_at":
                row[7].isoformat()
                if row[7]
                else None,
        }
        for row in rows
    ]


def get_brain_relationships(
    user_id,
    limit=500
):

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    r.id,
                    r.from_entity_id,
                    f.name,
                    r.relationship,
                    r.to_entity_id,
                    t.name,
                    r.confidence,
                    r.created_at
                FROM brain_relationships r
                JOIN brain_entities f
                    ON f.id = r.from_entity_id
                JOIN brain_entities t
                    ON t.id = r.to_entity_id
                WHERE r.user_id = %s
                ORDER BY
                    r.confidence DESC,
                    r.created_at DESC
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
            "from_entity_id": row[1],
            "from":
                row[2],
            "relationship":
                row[3],
            "to_entity_id": row[4],
            "to":
                row[5],
            "confidence":
                row[6],
            "created_at":
                row[7].isoformat()
                if row[7]
                else None,
        }
        for row in rows
    ]


# ============================================================
# BRAIN INTELLIGENCE
# ============================================================

def generate_brain_intelligence(
    user_id,
    entity_name,
):

    entity_name = str(
        entity_name or ""
    ).strip()

    if not entity_name:
        return {
            "entity": None,
            "intelligence": None,
            "error": "Entity name is required",
        }

    graph = explore_brain_graph(
        user_id,
        entity_name,
        max_depth=3,
        limit=100,
    )

    if not graph.get("entity"):
        return {
            "entity": None,
            "intelligence": None,
            "error": "Entity not found",
        }

    root = graph["entity"]

    all_memories = get_all_user_memories(
        user_id,
        limit=500,
    )

    target = entity_name.lower()

    memories = []

    for memory in all_memories:

        subject = str(
            memory.get("subject") or ""
        ).lower()

        text = str(
            memory.get("memory") or ""
        ).lower()

        if (
            subject == target
            or target in text
        ):
            memories.append(memory)

    memories = memories[:30]

    source_payload = {
        "entity": root,
        "connected_entities": graph.get(
            "entities", []
        ),
        "relationships": graph.get(
            "relationships", []
        ),
        "memories": memories,
    }

    system_prompt = """
You are the Brain Intelligence layer of Dusra Brain.

Your job is to synthesize only the information explicitly provided in the
source data. Do not invent facts, motivations, plans, dates, people, numbers,
or relationships. Do not treat a missing detail as true.

Return valid JSON only with exactly these keys:
{
  "summary": "short factual summary",
  "key_facts": ["fact 1", "fact 2"],
  "current_state": ["current known state 1"],
  "open_questions": ["question 1", "question 2"],
  "confidence": 1
}

Rules:
- summary must be 1-3 sentences.
- key_facts must contain only supported facts.
- current_state must describe only what the stored data supports.
- open_questions should contain useful unanswered questions only when the data
  shows that the information is missing. If none are justified, return [].
- confidence is an integer from 1 to 10 representing how complete the supplied
  evidence is for understanding the entity, not how important the entity is.
"""

    user_prompt = (
        "Create a factual intelligence summary for this Dusra Brain entity.\n\n"
        + json.dumps(
            source_payload,
            ensure_ascii=False,
            default=str,
        )
    )

    raw = groq_request(
        [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ],
        temperature=0.1,
    )

    cleaned = clean_json_response(raw)

    try:
        intelligence = json.loads(cleaned)
    except Exception:
        intelligence = {
            "summary": str(raw).strip(),
            "key_facts": [],
            "current_state": [],
            "open_questions": [],
            "confidence": 1,
        }

    return {
        "entity": root,
        "intelligence": intelligence,
        "evidence_count": len(memories),
        "relationship_count": len(
            graph.get("relationships", [])
        ),
        "entity_count": len(
            graph.get("entities", [])
        ),
    }


# ============================================================
# EVIDENCE RESOLUTION
# ============================================================

def resolve_insight_evidence(user_id, evidence_refs):
    """Resolve model-generated evidence references to stored records."""

    if not isinstance(evidence_refs, list):
        evidence_refs = []

    memory_ids = []
    relationship_ids = []

    for ref in evidence_refs:
        text = str(ref or "").strip().lower()

        match = re.search(r"memory\s*(\d+)", text)
        if match:
            memory_ids.append(int(match.group(1)))
            continue

        match = re.search(r"relationship(?:\s+id)?\s*(\d+)", text)
        if match:
            relationship_ids.append(int(match.group(1)))

    memories = []
    relationships = []

    with get_connection() as conn:
        with conn.cursor() as cur:

            if memory_ids:
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
                      AND id = ANY(%s)
                    ORDER BY created_at DESC, id DESC
                    """,
                    (user_id, memory_ids),
                )

                for row in cur.fetchall():
                    memories.append({
                        "id": row[0],
                        "memory": row[1],
                        "category": row[2],
                        "importance": row[3],
                        "subject": row[4],
                        "created_at": row[5].isoformat() if row[5] else None,
                    })

            if relationship_ids:
                cur.execute(
                    """
                    SELECT
                        r.id,
                        f.name,
                        f.entity_type,
                        r.relationship,
                        t.name,
                        t.entity_type,
                        r.confidence,
                        r.created_at
                    FROM brain_relationships r
                    JOIN brain_entities f
                        ON f.id = r.from_entity_id
                    JOIN brain_entities t
                        ON t.id = r.to_entity_id
                    WHERE r.user_id = %s
                      AND r.id = ANY(%s)
                    ORDER BY r.created_at DESC, r.id DESC
                    """,
                    (user_id, relationship_ids),
                )

                for row in cur.fetchall():
                    relationships.append({
                        "id": row[0],
                        "from": row[1],
                        "from_type": row[2],
                        "relationship": row[3],
                        "to": row[4],
                        "to_type": row[5],
                        "confidence": row[6],
                        "created_at": row[7].isoformat() if row[7] else None,
                    })

    return {
        "memories": memories,
        "relationships": relationships,
    }


def enrich_project_insights_with_evidence(user_id, insights):
    enriched = []

    if not isinstance(insights, list):
        return enriched

    for insight in insights:
        if not isinstance(insight, dict):
            continue

        evidence_refs = insight.get("evidence", [])
        evidence_details = resolve_insight_evidence(
            user_id,
            evidence_refs,
        )

        item = dict(insight)
        item["evidence_details"] = evidence_details
        enriched.append(item)

    return enriched



# ============================================================
# BRAIN PROJECT INSIGHTS
# ============================================================

def generate_project_insights(
    user_id,
    entity_name,
):

    entity_name = str(
        entity_name or ""
    ).strip()

    if not entity_name:
        return {
            "entity": None,
            "insights": [],
            "error": "Entity name is required",
        }

    graph = explore_brain_graph(
        user_id,
        entity_name,
        max_depth=3,
        limit=100,
    )

    if not graph.get("entity"):
        return {
            "entity": None,
            "insights": [],
            "error": "Entity not found",
        }

    all_memories = get_all_user_memories(
        user_id,
        limit=500,
    )

    target = entity_name.lower()
    memories = []

    for memory in all_memories:
        subject = str(
            memory.get("subject") or ""
        ).lower()
        text = str(
            memory.get("memory") or ""
        ).lower()

        if (
            subject == target
            or target in text
        ):
            memories.append(memory)

    memories = memories[:40]

    source_payload = {
        "entity": graph.get("entity"),
        "entities": graph.get("entities", []),
        "relationships": graph.get("relationships", []),
        "memories": memories,
    }

    system_prompt = """
You are the Project Insights layer of Dusra Brain.

Analyze only the supplied stored evidence. Do not invent facts, dates,
partners, budgets, milestones, intentions, risks, or completed actions.
Do not turn an unanswered question into a fact.

Return valid JSON only with exactly these keys:
{
  "insights": [
    {
      "type": "evidence_gap|connection|progress|focus",
      "title": "short title",
      "insight": "factual insight or clearly labeled suggested focus",
      "evidence": ["short evidence reference"]
    }
  ],
  "suggested_next_focus": ["optional focus 1", "optional focus 2"],
  "confidence": 1
}

Rules:
- Produce 1-5 insights only when supported by the evidence.
- "connection" identifies an explicit relationship between stored entities.
- "progress" may be used only when the evidence explicitly shows a change,
  stage, milestone, or action over time.
- "evidence_gap" identifies important information that is visibly missing.
- "focus" is a suggested area to clarify or work on; it must be phrased as a
  suggestion, not as a claim that the user intends to do it.
- Every insight must cite one or more short pieces of supplied evidence.
- suggested_next_focus contains suggestions, not facts.
- confidence is an integer from 1 to 10 representing evidence completeness.
- If the evidence is too limited for an insight, omit it rather than guess.
"""

    user_prompt = (
        "Generate evidence-grounded project insights for this Dusra Brain "
        "entity.\n\n"
        + json.dumps(
            source_payload,
            ensure_ascii=False,
            default=str,
        )
    )

    raw = groq_request(
        [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ],
        temperature=0.1,
    )

    cleaned = clean_json_response(raw)

    try:
        result = json.loads(cleaned)
    except Exception:
        result = {
            "insights": [],
            "suggested_next_focus": [],
            "confidence": 1,
        }

    if not isinstance(result, dict):
        result = {
            "insights": [],
            "suggested_next_focus": [],
            "confidence": 1,
        }

    insights = enrich_project_insights_with_evidence(
        user_id,
        result.get("insights", []),
    )

    return {
        "entity": graph.get("entity"),
        "insights": insights,
        "suggested_next_focus": result.get(
            "suggested_next_focus", []
        ),
        "confidence": result.get("confidence", 1),
        "evidence_count": len(memories),
        "relationship_count": len(
            graph.get("relationships", [])
        ),
        "entity_count": len(
            graph.get("entities", [])
        ),
    }



# ============================================================
# STEP 20 — BRAIN LEARNING & RELATIONSHIP DISCOVERY
# ============================================================

def discover_brain_relationships(
    user_id,
    limit=20,
):
    """
    Review stored memories and existing brain entities/relationships,
    then propose evidence-grounded relationships that are not already
    represented in the structured Brain.

    Step 20 is deliberately proposal-only:
    it NEVER writes a relationship to the database.
    The returned evidence can be reviewed before a future approval layer
    decides whether to persist a relationship.
    """

    entities = get_brain_entities(user_id)
    relationships = get_brain_relationships(user_id)
    memories = get_all_user_memories(user_id, limit=500)

    if len(memories) < 2:
        return {
            "proposals": [],
            "message": "Not enough stored memories to discover cross-memory relationships.",
            "confidence": 1,
            "evidence_count": len(memories),
            "entity_count": len(entities),
            "relationship_count": len(relationships),
        }

    source_payload = {
        "entities": entities[:200],
        "relationships": relationships[:300],
        "memories": memories[:300],
    }

    system_prompt = """
You are the Brain Learning and Relationship Discovery layer of Dusra Brain.

Your task is to identify ONLY evidence-grounded relationships that could be
added to the structured Brain because stored memories explicitly support them.

IMPORTANT:
- This is a proposal system, not an automatic writer.
- NEVER invent a relationship.
- NEVER assume a partnership, ownership, funding, employment, location,
  product relationship, causation, strategy, or intention.
- A proposal must be supported by either:
  1. one stored memory that clearly mentions both concepts/entities, OR
  2. multiple explicit brain relationships that form a clear chain.
- Shared generic words such as "India", "project", "business", "AI", or
  "company" are NOT enough by themselves.
- Do not propose a relationship that already exists in the supplied
  relationships.
- Prefer meaningful relationships involving named entities already present
  in the structured Brain.
- If evidence is insufficient, return no proposal.

Return valid JSON only with exactly these keys:
{
  "proposals": [
    {
      "from_entity": "exact entity name",
      "to_entity": "exact entity name",
      "relationship": "short factual relationship",
      "reason": "brief explanation grounded in evidence",
      "evidence": ["memory 13", "relationship id 2"],
      "confidence": 1
    }
  ],
  "suggested_followups": ["optional suggestion"],
  "confidence": 1
}

Rules:
- 0-10 proposals.
- Use exact entity names from the supplied entities.
- confidence is an integer from 1 to 10.
- Only return proposals with confidence >= 7.
- Evidence references must point only to supplied memory IDs or relationship IDs.
- Do not return duplicate proposals in reverse direction unless the relationship
  itself is genuinely directional.
"""

    raw = groq_request(
        [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": (
                    "Review this stored Dusra Brain evidence and identify "
                    "new relationship proposals that are not already represented.\n\n"
                    + json.dumps(
                        source_payload,
                        ensure_ascii=False,
                        default=str,
                    )
                ),
            },
        ],
        temperature=0.1,
    )

    cleaned = clean_json_response(raw)

    try:
        result = json.loads(cleaned)
    except Exception:
        result = {
            "proposals": [],
            "suggested_followups": [],
            "confidence": 1,
        }

    if not isinstance(result, dict):
        result = {
            "proposals": [],
            "suggested_followups": [],
            "confidence": 1,
        }

    proposals = (
        result.get("proposals", [])
        if isinstance(result.get("proposals", []), list)
        else []
    )

    existing_signatures = set()

    for relationship in relationships:
        existing_signatures.add(
            (
                str(relationship.get("from") or "").strip().lower(),
                str(relationship.get("relationship") or "").strip().lower(),
                str(relationship.get("to") or "").strip().lower(),
            )
        )

    entity_names = {
        str(entity.get("name") or "").strip().lower()
        for entity in entities
        if str(entity.get("name") or "").strip()
    }

    safe_proposals = []

    for proposal in proposals[:10]:
        if not isinstance(proposal, dict):
            continue

        from_name = str(
            proposal.get("from_entity") or ""
        ).strip()

        to_name = str(
            proposal.get("to_entity") or ""
        ).strip()

        relationship_name = str(
            proposal.get("relationship") or ""
        ).strip()

        reason = str(
            proposal.get("reason") or ""
        ).strip()

        evidence = (
            proposal.get("evidence", [])
            if isinstance(proposal.get("evidence", []), list)
            else []
        )

        try:
            confidence = int(
                proposal.get("confidence", 1)
            )
        except Exception:
            confidence = 1

        confidence = max(
            1,
            min(
                10,
                confidence,
            ),
        )

        if not from_name or not to_name or not relationship_name:
            continue

        if from_name.lower() not in entity_names:
            continue

        if to_name.lower() not in entity_names:
            continue

        signature = (
            from_name.lower(),
            relationship_name.lower(),
            to_name.lower(),
        )

        if signature in existing_signatures:
            continue

        if confidence < 7:
            continue

        if not evidence:
            continue

        safe_proposals.append(
            {
                "from_entity": from_name,
                "to_entity": to_name,
                "relationship": relationship_name,
                "reason": reason,
                "evidence": [
                    str(item)
                    for item in evidence[:10]
                ],
                "confidence": confidence,
            }
        )

    # STEP 24 — apply the conservative evidence-quality gate before
    # previously reviewed proposals are filtered.
    safe_proposals = apply_brain_learning_quality_gate(
        user_id,
        safe_proposals,
        memories,
    )

    reviewed_signatures = get_reviewed_relationship_signatures(
        user_id
    )

    entity_by_name = {
        str(entity.get("name") or "").strip().lower(): entity
        for entity in entities
    }

    filtered_proposals = []

    for proposal in safe_proposals:

        from_entity = entity_by_name.get(
            str(
                proposal.get("from_entity") or ""
            ).strip().lower()
        )

        to_entity = entity_by_name.get(
            str(
                proposal.get("to_entity") or ""
            ).strip().lower()
        )

        if not from_entity or not to_entity:
            continue

        signature = (
            int(from_entity["id"]),
            str(
                proposal.get("relationship") or ""
            ).strip().lower(),
            int(to_entity["id"]),
        )

        if signature in reviewed_signatures:
            continue

        filtered_proposals.append(
            proposal
        )

    safe_proposals = filtered_proposals[:20]

    return {
        "proposals": safe_proposals,
        "suggested_followups": (
            result.get("suggested_followups", [])
            if isinstance(
                result.get("suggested_followups", []),
                list,
            )
            else []
        ),
        "confidence": max(
            1,
            min(
                10,
                int(result.get("confidence", 1) or 1),
            ),
        ),
        "evidence_count": len(memories),
        "entity_count": len(entities),
        "relationship_count": len(relationships),
        "proposal_only": True,
        "auto_saved": False,
        "quality_gate": "strict_evidence_v1",
        "quality_gate_description": (
            "Only direct evidence or explicit relationship-chain evidence "
            "is eligible for Brain Learning proposals."
        ),
    }


# ============================================================
# STEP 24 — BRAIN LEARNING EVIDENCE QUALITY GATE
# ============================================================

def apply_brain_learning_quality_gate(
    user_id,
    proposals,
    memories,
):
    """
    Step 24 adds a deterministic evidence-quality gate after the AI
    relationship discovery step.

    The gate is intentionally conservative:
    - a proposal must have at least one verifiable memory/relationship reference
    - memory evidence must contain both named entities when a memory is cited
    - inference-heavy relationship language is blocked
    - unsupported claims such as ownership, partnership, funding, employment,
      or intent are blocked unless the evidence is explicitly represented
    - confidence is capped when the evidence is indirect
    - nothing is written to the database
    """

    if not isinstance(proposals, list):
        return []

    memory_by_id = {
        int(memory.get("id")): memory
        for memory in memories
        if memory.get("id") is not None
    }

    # These relationship forms are especially prone to turning a statement
    # into a stronger claim than the stored evidence actually supports.
    blocked_relationship_terms = {
        "owns",
        "owned_by",
        "partner",
        "partners_with",
        "partnered_with",
        "funds",
        "funded_by",
        "invests_in",
        "invested_in",
        "employs",
        "employed_by",
        "works_for",
        "works_with",
        "founded_by",
        "founded",
        "controls",
        "subsidiary_of",
        "acquired_by",
        "acquired",
        "operates_in",
        "based_in",
        "located_in",
        "headquartered_in",
    }

    inference_markers = (
        "implies",
        "implying",
        "suggests",
        "suggesting",
        "indicates",
        "indicating",
        "likely",
        "appears to",
        "could mean",
        "may mean",
        "therefore",
        "which means",
        "presumably",
        "possibly",
    )

    verified = []

    for proposal in proposals:
        if not isinstance(proposal, dict):
            continue

        from_name = str(
            proposal.get("from_entity") or ""
        ).strip()

        to_name = str(
            proposal.get("to_entity") or ""
        ).strip()

        relationship_name = str(
            proposal.get("relationship") or ""
        ).strip()

        reason = str(
            proposal.get("reason") or ""
        ).strip()

        evidence = (
            proposal.get("evidence", [])
            if isinstance(proposal.get("evidence", []), list)
            else []
        )

        if not from_name or not to_name or not relationship_name:
            continue

        relationship_key = (
            relationship_name
            .strip()
            .lower()
            .replace(" ", "_")
            .replace("-", "_")
        )

        if relationship_key in blocked_relationship_terms:
            continue

        reason_lower = reason.lower()

        # If the model itself describes the relationship as an inference,
        # do not promote it into the structured Brain.
        if any(
            marker in reason_lower
            for marker in inference_markers
        ):
            continue

        verified_memory_evidence = []

        for item in evidence:
            reference = str(item or "").strip()

            match = re.match(
                r"^memory\s+(\d+)$",
                reference,
                re.IGNORECASE,
            )

            if not match:
                continue

            memory_id = int(match.group(1))
            memory = memory_by_id.get(memory_id)

            if not memory:
                continue

            memory_text = str(
                memory.get("memory") or ""
            ).lower()

            from_present = (
                from_name.lower()
                in memory_text
            )

            to_present = (
                to_name.lower()
                in memory_text
            )

            # A single memory must explicitly mention both entities.
            if from_present and to_present:
                verified_memory_evidence.append(
                    reference
                )

        # Relationship-chain evidence is allowed only when it is explicitly
        # supplied by the discovery model. It is not enough by itself to
        # create a new relationship if the proposal is inference-heavy.
        verified_relationship_evidence = [
            str(item).strip()
            for item in evidence
            if re.match(
                r"^relationship\s+\d+$",
                str(item or "").strip(),
                re.IGNORECASE,
            )
        ]

        if not verified_memory_evidence and not verified_relationship_evidence:
            continue

        # A proposal backed only by relationship-chain evidence is kept at a
        # maximum of 7 unless the reason is explicitly factual.
        try:
            confidence = int(
                proposal.get("confidence", 1)
            )
        except Exception:
            confidence = 1

        confidence = max(
            1,
            min(
                10,
                confidence,
            ),
        )

        if (
            not verified_memory_evidence
            and verified_relationship_evidence
        ):
            confidence = min(
                confidence,
                7,
            )

        if confidence < 7:
            continue

        verified.append(
            {
                "from_entity": from_name,
                "to_entity": to_name,
                "relationship": relationship_name,
                "reason": reason,
                "evidence": (
                    verified_memory_evidence
                    + verified_relationship_evidence
                )[:10],
                "confidence": confidence,
                "evidence_quality": (
                    "direct"
                    if verified_memory_evidence
                    else "relationship_chain"
                ),
            }
        )

    return verified


# ============================================================

# ============================================================
# CROSS-MEMORY INTELLIGENCE
# ============================================================

def generate_cross_memory_intelligence(
    user_id,
    entity_name="",
):
    """Find evidence-grounded connections across the user's stored brain."""

    target_name = str(entity_name or "").strip()

    entities = get_brain_entities(user_id)
    relationships = get_brain_relationships(user_id)
    memories = get_all_user_memories(user_id, limit=500)

    target = None
    target_from_memory = False

    if target_name:
        # First prefer a structured Brain entity.
        for entity in entities:
            if str(entity.get("name") or "").lower() == target_name.lower():
                target = entity
                break

        # If the subject is not yet a structured entity, fall back to the
        # user's stored memories. This keeps Cross-Memory Intelligence useful
        # for important projects that have memories but no brain entity yet.
        if target is None:
            matching_memories = [
                memory
                for memory in memories
                if str(memory.get("subject") or "").strip().lower()
                == target_name.lower()
                or target_name.lower()
                in str(memory.get("subject") or "").strip().lower()
            ]

            if matching_memories:
                target_from_memory = True
                target = {
                    "id": None,
                    "user_id": user_id,
                    "entity_type": "subject",
                    "name": target_name,
                    "description": (
                        "Subject represented by stored user memories; "
                        "no structured Brain entity has been created yet."
                    ),
                    "importance": max(
                        int(memory.get("importance") or 1)
                        for memory in matching_memories
                    ),
                    "created_at": min(
                        str(memory.get("created_at") or "")
                        for memory in matching_memories
                    ),
                    "updated_at": max(
                        str(memory.get("created_at") or "")
                        for memory in matching_memories
                    ),
                    "source": "memory_subject",
                    "memory_ids": [
                        memory.get("id")
                        for memory in matching_memories
                    ],
                }

            else:
                return {
                    "entity": None,
                    "connections": [],
                    "confidence": 1,
                    "evidence_count": len(memories),
                    "entity_count": len(entities),
                    "relationship_count": len(relationships),
                    "error": "Entity or memory subject not found",
                }

    target_memories = []
    if target_name:
        target_memories = [
            memory
            for memory in memories
            if str(memory.get("subject") or "").strip().lower()
            == target_name.lower()
            or target_name.lower()
            in str(memory.get("subject") or "").strip().lower()
        ]

    source_payload = {
        "target_entity": target,
        "target_source": (
            "memory_subject" if target_from_memory else "brain_entity"
        ),
        "target_memories": target_memories[:100],
        "entities": entities[:200],
        "relationships": relationships[:300],
        "memories": memories[:300],
    }

    system_prompt = """
You are the Cross-Memory Intelligence layer of Dusra Brain.

Your job is to find meaningful connections between separately stored
entities, projects, products, people, organizations, locations, goals,
decisions, or other subjects in the supplied evidence.

Use ONLY the supplied stored evidence. Do not invent relationships, shared
ownership, partnerships, funding, causation, strategy, timelines, or intent.
A connection is valid only when it is explicitly supported by:
1. a stored memory that mentions both relevant concepts/entities, OR
2. one or more explicit stored brain relationships that create a clear chain.

Do not treat two entities as connected merely because they both mention India,
the same generic category, or a common word. Shared words alone are not proof.

If a target entity is supplied, prioritize connections involving that entity.
If no target entity is supplied, return only the strongest cross-entity
connections across the user's stored brain.

Return valid JSON only with exactly these keys:
{
  "connections": [
    {
      "type": "cross_entity|shared_evidence|relationship_chain",
      "title": "short factual title",
      "from_entity": "entity name",
      "to_entity": "entity name",
      "connection": "brief factual explanation",
      "evidence": ["memory 1", "relationship id 2"]
    }
  ],
  "suggested_followups": ["optional suggestion"],
  "confidence": 1
}

Rules:
- Return 0-8 connections.
- Never manufacture a connection to fill the list.
- Prefer direct evidence over long inferred chains.
- Every connection must include at least one evidence reference.
- Evidence references must use the exact form "memory N" or
  "relationship id N" when possible.
- suggested_followups are suggestions only, never facts.
- confidence is 1-10 and represents how strongly the supplied evidence
  supports the returned cross-memory connections.
"""

    user_prompt = (
        "Find evidence-grounded cross-memory connections in Dusra Brain.\n\n"
        + json.dumps(
            source_payload,
            ensure_ascii=False,
            default=str,
        )
    )

    raw = groq_request(
        [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ],
        temperature=0.1,
    )

    cleaned = clean_json_response(raw)

    try:
        result = json.loads(cleaned)
    except Exception:
        result = {
            "connections": [],
            "suggested_followups": [],
            "confidence": 1,
        }

    if not isinstance(result, dict):
        result = {
            "connections": [],
            "suggested_followups": [],
            "confidence": 1,
        }

    enriched_connections = []

    for item in result.get("connections", []):
        if not isinstance(item, dict):
            continue

        evidence = item.get("evidence", [])
        item = dict(item)
        item["evidence_details"] = resolve_insight_evidence(
            user_id,
            evidence,
        )
        enriched_connections.append(item)

    return {
        "entity": target,
        "connections": enriched_connections,
        "suggested_followups": result.get(
            "suggested_followups", []
        ),
        "confidence": result.get("confidence", 1),
        "evidence_count": len(memories),
        "entity_count": len(entities),
        "relationship_count": len(relationships),
    }


# ============================================================
# BRAIN EXPLORER
# ============================================================

def explore_brain(
    user_id,
    entity_name,
    limit=100
):

    entity_name = str(
        entity_name or ""
    ).strip()

    if not entity_name:
        return {
            "entity": None,
            "connections": []
        }

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    id,
                    entity_type,
                    name,
                    description,
                    importance
                FROM brain_entities
                WHERE user_id = %s
                  AND LOWER(name) = LOWER(%s)
                ORDER BY importance DESC
                LIMIT 1
                """,
                (
                    user_id,
                    entity_name,
                )
            )

            entity = cur.fetchone()

            if not entity:
                return {
                    "entity": None,
                    "connections": []
                }

            entity_data = {
                "id": entity[0],
                "entity_type": entity[1],
                "name": entity[2],
                "description": entity[3],
                "importance": entity[4],
            }

            cur.execute(
                """
                SELECT
                    r.id,
                    f.name,
                    f.entity_type,
                    r.relationship,
                    t.name,
                    t.entity_type,
                    r.confidence
                FROM brain_relationships r
                JOIN brain_entities f
                    ON f.id = r.from_entity_id
                JOIN brain_entities t
                    ON t.id = r.to_entity_id
                WHERE r.user_id = %s
                  AND (
                      r.from_entity_id = %s
                      OR r.to_entity_id = %s
                  )
                ORDER BY r.confidence DESC
                LIMIT %s
                """,
                (
                    user_id,
                    entity[0],
                    entity[0],
                    limit,
                )
            )

            rows = cur.fetchall()

    connections = []

    for row in rows:

        connections.append(
            {
                "id": row[0],
                "from": row[1],
                "from_type": row[2],
                "relationship": row[3],
                "to": row[4],
                "to_type": row[5],
                "confidence": row[6],
            }
        )

    return {
        "entity": entity_data,
        "connections": connections,
    }



# ============================================================
# BRAIN GRAPH EXPLORER
# ============================================================

def explore_brain_graph(
    user_id,
    entity_name,
    max_depth=3,
    limit=100,
):

    entity_name = (entity_name or "").strip()

    if not entity_name:
        return {
            "error": "Entity name is required"
        }

    try:
        max_depth = int(max_depth)
    except Exception:
        max_depth = 3

    max_depth = max(1, min(max_depth, 6))

    try:
        limit = int(limit)
    except Exception:
        limit = 100

    limit = max(1, min(limit, 500))

    with get_connection() as conn:

        with conn.cursor() as cur:

            # Find the starting entity using a case-insensitive exact match.
            cur.execute(
                """
                SELECT
                    id,
                    entity_type,
                    name,
                    description,
                    importance
                FROM brain_entities
                WHERE user_id = %s
                  AND LOWER(name) = LOWER(%s)
                LIMIT 1
                """,
                (
                    user_id,
                    entity_name,
                )
            )

            start = cur.fetchone()

            if not start:
                return {
                    "entity": None,
                    "entities": [],
                    "relationships": [],
                    "depth": max_depth,
                    "error": "Entity not found"
                }

            start_entity = {
                "id": start[0],
                "entity_type": start[1],
                "name": start[2],
                "description": start[3],
                "importance": start[4],
                "depth": 0,
            }

            entities_by_id = {
                start[0]: start_entity
            }

            relationships = []
            seen_relationships = set()
            frontier = [start[0]]
            visited = {start[0]}

            for current_depth in range(max_depth):

                if not frontier or len(entities_by_id) >= limit:
                    break

                next_frontier = []

                for current_id in frontier:

                    cur.execute(
                        """
                        SELECT
                            r.id,
                            r.from_entity_id,
                            f.entity_type,
                            f.name,
                            r.relationship,
                            r.to_entity_id,
                            t.entity_type,
                            t.name,
                            r.confidence
                        FROM brain_relationships r
                        JOIN brain_entities f
                            ON f.id = r.from_entity_id
                        JOIN brain_entities t
                            ON t.id = r.to_entity_id
                        WHERE r.user_id = %s
                          AND (
                              r.from_entity_id = %s
                              OR r.to_entity_id = %s
                          )
                        ORDER BY r.confidence DESC, r.id ASC
                        """,
                        (
                            user_id,
                            current_id,
                            current_id,
                        )
                    )

                    rows = cur.fetchall()

                    for row in rows:

                        relationship_id = row[0]

                        if relationship_id in seen_relationships:
                            continue

                        seen_relationships.add(
                            relationship_id
                        )

                        relationships.append({
                            "id": row[0],
                            "from_entity_id": row[1],
                            "from_type": row[2],
                            "from": row[3],
                            "relationship": row[4],
                            "to_entity_id": row[5],
                            "to_type": row[6],
                            "to": row[7],
                            "confidence": row[8],
                            "depth": current_depth + 1,
                        })

                        other_id = (
                            row[5]
                            if row[1] == current_id
                            else row[1]
                        )

                        if (
                            other_id not in visited
                            and len(entities_by_id) < limit
                        ):

                            entity_id = other_id

                            if row[1] == entity_id:
                                entity_type = row[2]
                                name = row[3]
                            else:
                                entity_type = row[6]
                                name = row[7]

                            cur.execute(
                                """
                                SELECT
                                    id,
                                    entity_type,
                                    name,
                                    description,
                                    importance
                                FROM brain_entities
                                WHERE user_id = %s
                                  AND id = %s
                                LIMIT 1
                                """,
                                (
                                    user_id,
                                    entity_id,
                                )
                            )

                            entity_row = cur.fetchone()

                            if entity_row:
                                visited.add(entity_id)
                                entities_by_id[entity_id] = {
                                    "id": entity_row[0],
                                    "entity_type": entity_row[1],
                                    "name": entity_row[2],
                                    "description": entity_row[3],
                                    "importance": entity_row[4],
                                    "depth": current_depth + 1,
                                }
                                next_frontier.append(entity_id)

                        if len(relationships) >= limit:
                            break

                    if len(relationships) >= limit:
                        break

                frontier = next_frontier

    return {
        "entity": start_entity,
        "entities": list(entities_by_id.values()),
        "relationships": relationships[:limit],
        "depth": max_depth,
        "entity_count": len(entities_by_id),
        "relationship_count": min(
            len(relationships),
            limit,
        ),
    }


def get_brain_learning_review_history(user_id, status="", limit=100):

    ensure_brain_learning_reviews_table()

    status = str(status or "").strip().lower()

    try:
        limit = int(limit)
    except Exception:
        limit = 100

    limit = max(1, min(500, limit))

    with get_connection() as conn:

        with conn.cursor() as cur:

            where = ["r.user_id = %s"]
            values = [user_id]

            if status in ("approved", "rejected"):
                where.append("r.status = %s")
                values.append(status)

            values.append(limit)

            cur.execute(
                f"""
                SELECT
                    r.id,
                    r.from_entity_id,
                    fe.name,
                    fe.entity_type,
                    r.relationship,
                    r.to_entity_id,
                    te.name,
                    te.entity_type,
                    r.status,
                    r.evidence,
                    r.reason,
                    r.confidence,
                    r.reviewed_at
                FROM brain_learning_reviews r
                LEFT JOIN brain_entities fe
                    ON fe.id = r.from_entity_id
                   AND fe.user_id = r.user_id
                LEFT JOIN brain_entities te
                    ON te.id = r.to_entity_id
                   AND te.user_id = r.user_id
                WHERE {" AND ".join(where)}
                ORDER BY r.reviewed_at DESC, r.id DESC
                LIMIT %s
                """,
                tuple(values)
            )

            rows = cur.fetchall()

    reviews = []

    for row in rows:

        evidence = row[9]

        if isinstance(evidence, str):
            try:
                evidence = json.loads(evidence)
            except Exception:
                evidence = []

        reviews.append({
            "id": int(row[0]),
            "from_entity_id": row[1],
            "from_entity": row[2],
            "from_entity_type": row[3],
            "relationship": row[4],
            "to_entity_id": row[5],
            "to_entity": row[6],
            "to_entity_type": row[7],
            "status": row[8],
            "evidence": evidence or [],
            "reason": row[10] or "",
            "confidence": int(row[11] or 0),
            "reviewed_at": row[12].isoformat() if row[12] else None,
        })

    return reviews


def get_brain_learning_review_summary(user_id):

    ensure_brain_learning_reviews_table()

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE status = 'approved') AS approved,
                    COUNT(*) FILTER (WHERE status = 'rejected') AS rejected,
                    COUNT(DISTINCT from_entity_id || ':' || relationship || ':' || to_entity_id)
                        FILTER (WHERE status = 'approved') AS approved_relationships
                FROM brain_learning_reviews
                WHERE user_id = %s
                """,
                (user_id,)
            )

            row = cur.fetchone()

    return {
        "total": int(row[0] or 0),
        "approved": int(row[1] or 0),
        "rejected": int(row[2] or 0),
        "approved_relationships": int(row[3] or 0),
    }

# ============================================================
# STEP 21 — BRAIN LEARNING APPROVAL LAYER
# ============================================================

def ensure_brain_learning_reviews_table():

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS brain_learning_reviews
                (
                    id SERIAL PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    from_entity_id INTEGER NOT NULL,
                    relationship TEXT NOT NULL,
                    to_entity_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    evidence JSONB,
                    reason TEXT,
                    confidence INTEGER DEFAULT 5,
                    reviewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE
                    (
                        user_id,
                        from_entity_id,
                        relationship,
                        to_entity_id
                    )
                )
                """
            )

        conn.commit()


def get_reviewed_relationship_signatures(user_id):

    ensure_brain_learning_reviews_table()

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    from_entity_id,
                    LOWER(relationship),
                    to_entity_id,
                    status
                FROM brain_learning_reviews
                WHERE user_id = %s
                """,
                (user_id,)
            )

            rows = cur.fetchall()

    return {
        (
            int(row[0]),
            str(row[1]).strip().lower(),
            int(row[2]),
        ): str(row[3]).strip().lower()
        for row in rows
    }


def find_brain_entity_by_name(user_id, name):

    name = str(name or "").strip()

    if not name:
        return None

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    id,
                    user_id,
                    entity_type,
                    name,
                    description,
                    importance
                FROM brain_entities
                WHERE user_id = %s
                  AND LOWER(name) = LOWER(%s)
                ORDER BY importance DESC
                LIMIT 1
                """,
                (user_id, name)
            )

            row = cur.fetchone()

    if not row:
        return None

    return {
        "id": row[0],
        "user_id": row[1],
        "entity_type": row[2],
        "name": row[3],
        "description": row[4],
        "importance": row[5],
    }


def verify_brain_learning_evidence(user_id, evidence):

    if not isinstance(evidence, list):
        return []

    verified = []

    for item in evidence:

        reference = str(item or "").strip()

        memory_match = re.match(
            r"^memory\s+(\d+)$",
            reference,
            re.IGNORECASE
        )

        if memory_match:

            memory_id = int(memory_match.group(1))

            with get_connection() as conn:

                with conn.cursor() as cur:

                    cur.execute(
                        """
                        SELECT id
                        FROM memories
                        WHERE id = %s
                          AND user_id = %s
                        LIMIT 1
                        """,
                        (memory_id, user_id)
                    )

                    if cur.fetchone():

                        verified.append(
                            "memory " + str(memory_id)
                        )

                        continue

        relationship_match = re.match(
            r"^relationship\s+id\s+(\d+)$",
            reference,
            re.IGNORECASE
        )

        if relationship_match:

            relationship_id = int(
                relationship_match.group(1)
            )

            with get_connection() as conn:

                with conn.cursor() as cur:

                    cur.execute(
                        """
                        SELECT id
                        FROM brain_relationships
                        WHERE id = %s
                          AND user_id = %s
                        LIMIT 1
                        """,
                        (relationship_id, user_id)
                    )

                    if cur.fetchone():

                        verified.append(
                            "relationship id " +
                            str(relationship_id)
                        )

                        continue

    return verified


def approve_brain_learning_proposal(user_id, proposal):

    if not isinstance(proposal, dict):
        return {
            "approved": False,
            "error": "Invalid proposal."
        }

    from_name = str(
        proposal.get("from_entity", "")
    ).strip()

    to_name = str(
        proposal.get("to_entity", "")
    ).strip()

    relationship = str(
        proposal.get("relationship", "")
    ).strip()

    reason = str(
        proposal.get("reason", "")
    ).strip()

    evidence = proposal.get("evidence", [])

    try:
        confidence = int(
            proposal.get("confidence", 5)
        )
    except Exception:
        confidence = 5

    confidence = max(1, min(10, confidence))

    if not from_name or not to_name or not relationship:
        return {
            "approved": False,
            "error": "Proposal is missing an entity or relationship."
        }

    from_entity = find_brain_entity_by_name(
        user_id,
        from_name
    )

    to_entity = find_brain_entity_by_name(
        user_id,
        to_name
    )

    if not from_entity or not to_entity:
        return {
            "approved": False,
            "error": "Both entities must already exist in the structured Brain."
        }

    verified_evidence = verify_brain_learning_evidence(
        user_id,
        evidence
    )

    if not verified_evidence:
        return {
            "approved": False,
            "error": "The proposal has no verifiable stored evidence."
        }

    ensure_brain_learning_reviews_table()

    reviews = get_reviewed_relationship_signatures(user_id)

    signature = (
        int(from_entity["id"]),
        relationship.lower(),
        int(to_entity["id"]),
    )

    if reviews.get(signature) == "approved":
        return {
            "approved": True,
            "already_approved": True,
            "relationship_id": None,
            "from": from_entity["name"],
            "relationship": relationship,
            "to": to_entity["name"],
        }

    if reviews.get(signature) == "rejected":
        return {
            "approved": False,
            "error": "This proposal was previously rejected."
        }

    relation_id = save_brain_relationship(
        user_id=user_id,
        from_entity_id=from_entity["id"],
        relationship=relationship,
        to_entity_id=to_entity["id"],
        confidence=confidence
    )

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                INSERT INTO brain_learning_reviews
                (
                    user_id,
                    from_entity_id,
                    relationship,
                    to_entity_id,
                    status,
                    evidence,
                    reason,
                    confidence
                )
                VALUES
                (
                    %s, %s, %s, %s,
                    'approved',
                    %s::jsonb,
                    %s,
                    %s
                )
                ON CONFLICT
                (
                    user_id,
                    from_entity_id,
                    relationship,
                    to_entity_id
                )
                DO UPDATE SET
                    status = 'approved',
                    evidence = EXCLUDED.evidence,
                    reason = EXCLUDED.reason,
                    confidence = EXCLUDED.confidence,
                    reviewed_at = CURRENT_TIMESTAMP
                """,
                (
                    user_id,
                    from_entity["id"],
                    relationship,
                    to_entity["id"],
                    json.dumps(verified_evidence),
                    reason,
                    confidence,
                )
            )

        conn.commit()

    return {
        "approved": True,
        "already_approved": False,
        "relationship_id": relation_id,
        "from": from_entity["name"],
        "relationship": relationship,
        "to": to_entity["name"],
        "confidence": confidence,
        "evidence": verified_evidence,
    }


def reject_brain_learning_proposal(user_id, proposal):

    if not isinstance(proposal, dict):
        return {
            "rejected": False,
            "error": "Invalid proposal."
        }

    from_name = str(
        proposal.get("from_entity", "")
    ).strip()

    to_name = str(
        proposal.get("to_entity", "")
    ).strip()

    relationship = str(
        proposal.get("relationship", "")
    ).strip()

    if not from_name or not to_name or not relationship:
        return {
            "rejected": False,
            "error": "Proposal is missing an entity or relationship."
        }

    from_entity = find_brain_entity_by_name(
        user_id,
        from_name
    )

    to_entity = find_brain_entity_by_name(
        user_id,
        to_name
    )

    if not from_entity or not to_entity:
        return {
            "rejected": False,
            "error": "Both entities must already exist in the structured Brain."
        }

    ensure_brain_learning_reviews_table()

    signature = (
        int(from_entity["id"]),
        relationship.lower(),
        int(to_entity["id"]),
    )

    reviews = get_reviewed_relationship_signatures(user_id)

    if reviews.get(signature) == "approved":
        return {
            "rejected": False,
            "error": "This relationship is already approved and saved."
        }

    try:
        confidence = int(
            proposal.get("confidence", 5)
        )
    except Exception:
        confidence = 5

    confidence = max(1, min(10, confidence))

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                INSERT INTO brain_learning_reviews
                (
                    user_id,
                    from_entity_id,
                    relationship,
                    to_entity_id,
                    status,
                    evidence,
                    reason,
                    confidence
                )
                VALUES
                (
                    %s, %s, %s, %s,
                    'rejected',
                    %s::jsonb,
                    %s,
                    %s
                )
                ON CONFLICT
                (
                    user_id,
                    from_entity_id,
                    relationship,
                    to_entity_id
                )
                DO UPDATE SET
                    status = 'rejected',
                    reviewed_at = CURRENT_TIMESTAMP
                """,
                (
                    user_id,
                    from_entity["id"],
                    relationship,
                    to_entity["id"],
                    json.dumps(
                        proposal.get("evidence", [])
                    ),
                    str(
                        proposal.get("reason", "")
                    ),
                    confidence,
                )
            )

        conn.commit()

    return {
        "rejected": True,
        "from": from_entity["name"],
        "relationship": relationship,
        "to": to_entity["name"],
    }




# ============================================================
# PHASE 6 — STEP 2F — MEMORY CONSOLIDATION REVIEW HISTORY
# ============================================================

def get_memory_consolidation_reviews(user_id, subject="", status="", limit=100):

    ensure_memory_consolidation_reviews_table()

    subject = str(subject or "").strip()
    status = str(status or "").strip().lower()

    try:
        limit = int(limit)
    except Exception:
        limit = 100

    limit = max(1, min(500, limit))

    with get_connection() as conn:

        with conn.cursor() as cur:

            where = ["user_id = %s"]
            values = [user_id]

            if subject:
                where.append("subject = %s")
                values.append(subject)

            if status in ("approved", "rejected"):
                where.append("status = %s")
                values.append(status)

            values.append(limit)

            cur.execute(
                f"""
                SELECT
                    id,
                    source_memory_ids,
                    canonical_memory,
                    canonical_memory_id,
                    subject,
                    category,
                    reason,
                    confidence,
                    status,
                    created_at,
                    reviewed_at
                FROM memory_consolidation_reviews
                WHERE {" AND ".join(where)}
                ORDER BY reviewed_at DESC, id DESC
                LIMIT %s
                """,
                tuple(values)
            )

            rows = cur.fetchall()

    reviews = []

    for row in rows:
        source_ids = row[1]

        if isinstance(source_ids, str):
            try:
                source_ids = json.loads(source_ids)
            except Exception:
                source_ids = []

        reviews.append(
            {
                "id": int(row[0]),
                "source_memory_ids": source_ids or [],
                "canonical_memory": row[2],
                "canonical_memory_id": row[3],
                "subject": row[4] or "general",
                "category": row[5] or "general",
                "reason": row[6] or "",
                "confidence": int(row[7] or 0),
                "status": row[8],
                "created_at": row[9].isoformat() if row[9] else None,
                "reviewed_at": row[10].isoformat() if row[10] else None,
            }
        )

    return reviews


def get_memory_consolidation_review_summary(user_id):

    ensure_memory_consolidation_reviews_table()

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE status = 'approved') AS approved,
                    COUNT(*) FILTER (WHERE status = 'rejected') AS rejected,
                    COUNT(DISTINCT canonical_memory_id) FILTER (WHERE canonical_memory_id IS NOT NULL) AS canonical_memories
                FROM memory_consolidation_reviews
                WHERE user_id = %s
                """,
                (user_id,)
            )

            row = cur.fetchone()

    return {
        "total": int(row[0] or 0),
        "approved": int(row[1] or 0),
        "rejected": int(row[2] or 0),
        "canonical_memories": int(row[3] or 0),
    }


# ============================================================
# PHASE 6 — STEP 2E — MEMORY CONSOLIDATION APPROVAL
# ============================================================

def ensure_memory_consolidation_reviews_table():

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_consolidation_reviews
                (
                    id SERIAL PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    source_memory_ids JSONB NOT NULL,
                    canonical_memory TEXT NOT NULL,
                    canonical_memory_id INTEGER,
                    subject TEXT DEFAULT 'general',
                    category TEXT DEFAULT 'general',
                    reason TEXT,
                    confidence INTEGER DEFAULT 5,
                    status TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    reviewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_memory_consolidation_reviews_user
                ON memory_consolidation_reviews(user_id)
                """
            )

        conn.commit()


def approve_memory_consolidation_proposal(user_id, proposal):

    if not isinstance(proposal, dict):
        return {
            "approved": False,
            "error": "Invalid consolidation proposal."
        }

    memory_ids = proposal.get("memory_ids", [])

    if not isinstance(memory_ids, list):
        return {
            "approved": False,
            "error": "memory_ids must be a list."
        }

    try:
        memory_ids = list(
            dict.fromkeys(
                int(memory_id)
                for memory_id in memory_ids
            )
        )
    except Exception:
        return {
            "approved": False,
            "error": "Invalid memory IDs."
        }

    if len(memory_ids) < 2:
        return {
            "approved": False,
            "error": "At least two source memories are required."
        }

    canonical_memory = str(
        proposal.get("canonical_memory", "") or ""
    ).strip()

    subject = str(
        proposal.get("subject", "general") or "general"
    ).strip()

    category = str(
        proposal.get("category", "general") or "general"
    ).strip()

    reason = str(
        proposal.get("reason", "") or ""
    ).strip()

    if not canonical_memory:
        return {
            "approved": False,
            "error": "Canonical memory is required."
        }

    try:
        confidence = int(
            proposal.get("confidence", 5)
        )
    except Exception:
        confidence = 5

    confidence = max(1, min(10, confidence))

    ensure_memory_versions_table()
    ensure_memory_consolidation_reviews_table()

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    id,
                    memory,
                    subject,
                    category,
                    importance,
                    memory_key,
                    session_id
                FROM memories
                WHERE user_id = %s
                  AND id = ANY(%s)
                ORDER BY id
                """,
                (
                    user_id,
                    memory_ids,
                )
            )

            source_rows = cur.fetchall()

            if len(source_rows) != len(memory_ids):
                return {
                    "approved": False,
                    "error":
                        "One or more source memories could not be verified."
                }

            cur.execute(
                """
                SELECT
                    canonical_memory_id,
                    status
                FROM memory_consolidation_reviews
                WHERE user_id = %s
                  AND source_memory_ids = %s::jsonb
                  AND status = 'approved'
                ORDER BY id DESC
                LIMIT 1
                """,
                (
                    user_id,
                    json.dumps(sorted(memory_ids)),
                )
            )

            existing_review = cur.fetchone()

            if existing_review:
                return {
                    "approved": True,
                    "already_approved": True,
                    "canonical_memory_id": existing_review[0],
                    "message":
                        "This consolidation proposal was already approved."
                }

            importance = max(
                int(row[4] or 5)
                for row in source_rows
            )

            session_id = source_rows[0][6] or "default"

            memory_key = make_memory_key(
                subject,
                category,
                canonical_memory
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
                RETURNING id
                """,
                (
                    user_id,
                    canonical_memory,
                    category,
                    importance,
                    subject,
                    memory_key,
                    session_id,
                )
            )

            canonical_memory_id = int(
                cur.fetchone()[0]
            )

            canonical_version = record_memory_version(
                cur,
                canonical_memory_id,
                user_id,
                memory=canonical_memory,
                category=category,
                importance=importance,
                subject=subject,
                memory_key=memory_key,
                session_id=session_id,
                change_type="created",
                change_reason="memory_consolidation_approved",
            )

            cur.execute(
                """
                INSERT INTO memory_consolidation_reviews
                (
                    user_id,
                    source_memory_ids,
                    canonical_memory,
                    canonical_memory_id,
                    subject,
                    category,
                    reason,
                    confidence,
                    status
                )
                VALUES
                (
                    %s,
                    %s::jsonb,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    'approved'
                )
                """,
                (
                    user_id,
                    json.dumps(sorted(memory_ids)),
                    canonical_memory,
                    canonical_memory_id,
                    subject,
                    category,
                    reason,
                    confidence,
                )
            )

        conn.commit()

    return {
        "approved": True,
        "already_approved": False,
        "canonical_memory_id": canonical_memory_id,
        "canonical_version": canonical_version,
        "source_memory_ids": memory_ids,
        "source_memories_preserved": True,
        "memory_rows_deleted": 0,
        "message":
            "Memory consolidation approved. A canonical memory was created and all original memories were preserved."
    }


def reject_memory_consolidation_proposal(user_id, proposal):

    if not isinstance(proposal, dict):
        return {
            "rejected": False,
            "error": "Invalid consolidation proposal."
        }

    memory_ids = proposal.get("memory_ids", [])

    if not isinstance(memory_ids, list):
        return {
            "rejected": False,
            "error": "memory_ids must be a list."
        }

    try:
        memory_ids = list(
            dict.fromkeys(
                int(memory_id)
                for memory_id in memory_ids
            )
        )
    except Exception:
        return {
            "rejected": False,
            "error": "Invalid memory IDs."
        }

    if len(memory_ids) < 2:
        return {
            "rejected": False,
            "error": "At least two source memories are required."
        }

    canonical_memory = str(
        proposal.get("canonical_memory", "") or ""
    ).strip()

    subject = str(
        proposal.get("subject", "general") or "general"
    ).strip()

    category = str(
        proposal.get("category", "general") or "general"
    ).strip()

    reason = str(
        proposal.get("reason", "") or ""
    ).strip()

    try:
        confidence = int(
            proposal.get("confidence", 5)
        )
    except Exception:
        confidence = 5

    confidence = max(1, min(10, confidence))

    ensure_memory_consolidation_reviews_table()

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT id
                FROM memory_consolidation_reviews
                WHERE user_id = %s
                  AND source_memory_ids = %s::jsonb
                  AND status = 'approved'
                LIMIT 1
                """,
                (
                    user_id,
                    json.dumps(sorted(memory_ids)),
                )
            )

            if cur.fetchone():
                return {
                    "rejected": False,
                    "error": "This consolidation was already approved."
                }

            cur.execute(
                """
                INSERT INTO memory_consolidation_reviews
                (
                    user_id,
                    source_memory_ids,
                    canonical_memory,
                    subject,
                    category,
                    reason,
                    confidence,
                    status
                )
                VALUES
                (
                    %s,
                    %s::jsonb,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    'rejected'
                )
                """,
                (
                    user_id,
                    json.dumps(sorted(memory_ids)),
                    canonical_memory,
                    subject,
                    category,
                    reason,
                    confidence,
                )
            )

        conn.commit()

    return {
        "rejected": True,
        "source_memory_ids": memory_ids,
        "memory_rows_modified": 0,
        "memory_rows_deleted": 0,
        "message":
            "Consolidation rejected. No memory was changed."
    }


# ============================================================
# REQUEST HANDLER
# ============================================================


# ============================================================
# PHASE 6 — STEP 1A — MEMORY CONSOLIDATION PROPOSALS
# ============================================================

def build_memory_consolidation_proposals(user_id, subject="", limit=30):
    """Find consolidation candidates deterministically, then use a small AI
    call only to write the information-preserving canonical memory.

    Proposal only: no memory is changed here.
    """
    all_memories = get_all_user_memories(user_id, limit=500)
    subject = str(subject or "").strip()

    # Group by normalized subject. Also allow an explicit subject request.
    grouped = {}

    for item in all_memories:
        item_subject = str(
            item.get("subject", "general") or "general"
        ).strip()

        key = re.sub(
            r"\s+",
            " ",
            item_subject.lower()
        )

        if subject:
            requested = re.sub(
                r"\s+",
                " ",
                subject.lower()
            )

            memory_text = str(
                item.get("memory", "") or ""
            ).lower()

            if (
                requested not in key
                and requested not in memory_text
            ):
                continue

        grouped.setdefault(key, []).append(item)

    # Deterministically select only subjects with repeated memories.
    candidate_groups = []

    for key, group in grouped.items():
        if len(group) < 2:
            continue

        group = sorted(
            group,
            key=lambda item: (
                int(item.get("importance", 5) or 5),
                str(item.get("created_at", "") or ""),
                int(item.get("id", 0) or 0),
            ),
            reverse=True,
        )

        # Keep the request small.
        group = group[:5]

        candidate_groups.append(group)

    # If an explicit subject was requested, prioritize that group.
    if subject:
        candidate_groups.sort(
            key=lambda group: 0 if any(
                subject.lower()
                in str(
                    item.get("subject", "")
                ).lower()
                for item in group
            ) else 1
        )

    candidate_groups = candidate_groups[:6]

    if not candidate_groups:
        return {
            "proposals": [],
            "suggested_followups": [],
            "confidence": 10,
            "memory_count": len(all_memories),
            "candidate_group_count": 0,
            "proposal_only": True,
            "auto_saved": False,
        }

    proposals = []

    for group in candidate_groups:

        # Deterministic evidence payload. The AI sees only one small group.
        source_payload = []

        for item in group:
            source_payload.append({
                "id": int(item.get("id")),
                "memory": str(
                    item.get("memory", "") or ""
                ),
                "subject": str(
                    item.get(
                        "subject",
                        "general"
                    ) or "general"
                ),
                "category": str(
                    item.get(
                        "category",
                        "general"
                    ) or "general"
                ),
                "importance": int(
                    item.get(
                        "importance",
                        5
                    ) or 5
                ),
            })

        # Fast deterministic guard: only ask AI if the memories actually
        # share meaningful subject/content overlap.
        combined_subjects = [
            str(
                item.get(
                    "subject",
                    ""
                ) or ""
            ).strip().lower()
            for item in group
        ]

        normalized_subjects = [
            re.sub(
                r"\s+",
                " ",
                value
            )
            for value in combined_subjects
            if value
        ]

        shared_subject = (
            len(set(normalized_subjects)) == 1
            and bool(normalized_subjects)
        )

        if not shared_subject:
            text_tokens = []

            for item in group:
                tokens = {
                    token.lower()
                    for token in re.findall(
                        r"[A-Za-z0-9_'-]+",
                        str(
                            item.get(
                                "memory",
                                ""
                            ) or ""
                        )
                    )
                    if len(token) >= 5
                }

                text_tokens.append(tokens)

            if len(text_tokens) >= 2:
                shared_tokens = set.intersection(
                    *text_tokens
                )
            else:
                shared_tokens = set()

            if len(shared_tokens) < 2:
                continue

        system_prompt = """
You are Dusra Brain's memory consolidation writer.

The supplied memories were already grouped as likely related.
Decide whether they express one durable underlying fact.

Rules:
- Use ONLY information explicitly present in the supplied memories.
- Never invent or infer facts.
- Preserve useful specific details.
- Do not reduce detailed evidence to a generic statement.
- If the memories are not actually the same underlying fact, return
  {"consolidate":false}.
- If they are the same underlying fact, create one concise canonical
  memory that preserves the important supported details.
- Cite every source memory ID used.

Return ONLY JSON:
{
  "consolidate": true,
  "canonical_memory": "...",
  "reason": "...",
  "confidence": 1
}
"""

        user_prompt = (
            "Candidate memories:\n"
            + json.dumps(
                source_payload,
                ensure_ascii=False,
                default=str,
            )
        )

        decision = None

        try:
            raw = groq_request(
                [
                    {
                        "role": "system",
                        "content": system_prompt,
                    },
                    {
                        "role": "user",
                        "content": user_prompt,
                    },
                ],
                temperature=0.0,
                max_completion_tokens=350,
            )

            cleaned = clean_json_response(raw)
            parsed = json.loads(cleaned)

            if isinstance(parsed, dict):
                decision = parsed

        except Exception:
            # AI failure is handled by the deterministic safety fallback below.
            decision = None

        canonical = ""
        reason = ""
        confidence = 7

        if isinstance(decision, dict) and decision.get("consolidate") is True:
            canonical = str(
                decision.get(
                    "canonical_memory",
                    ""
                ) or ""
            ).strip()

            reason = str(
                decision.get(
                    "reason",
                    ""
                ) or ""
            ).strip()

            try:
                confidence = max(
                    1,
                    min(
                        10,
                        int(
                            decision.get(
                                "confidence",
                                7
                            )
                        )
                    )
                )
            except Exception:
                confidence = 7

        # ---------------------------------------------------------------
        # SAFE DETERMINISTIC FALLBACK
        # ---------------------------------------------------------------
        # If the AI does not return a usable consolidation proposal,
        # create one only when the evidence is unambiguously repetitive:
        #
        # 1. Same subject, AND
        # 2. At least two memories share meaningful content, AND
        # 3. We can construct the canonical memory entirely from existing
        #    source text.
        #
        # This is still proposal-only. Nothing is written or deleted.
        if not canonical or not reason:
            if shared_subject and len(group) >= 2:
                memories_text = [
                    str(
                        item.get(
                            "memory",
                            ""
                        ) or ""
                    ).strip()
                    for item in group
                ]

                memories_text = [
                    value
                    for value in memories_text
                    if value
                ]

                # Only use the fallback when there are at least two
                # non-empty source memories.
                if len(memories_text) >= 2:
                    # Start with the most informative existing memory.
                    canonical = max(
                        memories_text,
                        key=len
                    )

                    # Preserve a complementary source when it contains
                    # meaningful information absent from the selected
                    # canonical memory.
                    canonical_lower = canonical.lower()

                    additions = []
                    for value in memories_text:
                        value_lower = value.lower()

                        if value == canonical:
                            continue

                        # Extract clauses after common conjunctions so that
                        # useful details can be preserved without inventing
                        # new facts.
                        clauses = re.split(
                            r"\s+(?:and|while|but|with|focused on)\s+",
                            value,
                            flags=re.IGNORECASE,
                        )

                        for clause in clauses:
                            clause = clause.strip(
                                " .;,:"
                            )

                            if len(clause) < 12:
                                continue

                            words = {
                                token.lower()
                                for token in re.findall(
                                    r"[A-Za-z0-9_'-]+",
                                    clause
                                )
                                if len(token) >= 5
                            }

                            existing_words = {
                                token.lower()
                                for token in re.findall(
                                    r"[A-Za-z0-9_'-]+",
                                    canonical
                                )
                                if len(token) >= 5
                            }

                            if (
                                words
                                and len(
                                    words - existing_words
                                ) >= 2
                            ):
                                additions.append(clause)

                    if additions:
                        # Deduplicate additions while preserving order.
                        unique_additions = []
                        seen_additions = set()

                        for addition in additions:
                            marker = addition.lower()
                            if marker in seen_additions:
                                continue
                            seen_additions.add(marker)
                            unique_additions.append(addition)

                        canonical = (
                            canonical.rstrip(". ")
                            + "; "
                            + "; ".join(
                                unique_additions[:2]
                            )
                            + "."
                        )

                    reason = (
                        "The source memories share the same subject and "
                        "contain overlapping durable information. The "
                        "canonical proposal is built only from existing "
                        "memory text; the original memories remain "
                        "unchanged as evidence."
                    )

                    confidence = 7

        if not canonical or not reason:
            # One candidate that cannot be safely consolidated must not
            # prevent other candidate groups from being reviewed.
            continue

        if len(canonical) > 600:
            canonical = canonical[:600].rstrip()

        if len(reason) > 800:
            reason = reason[:800].rstrip()

        source_memories = []

        for item in group:
            source_memories.append({
                "id": int(
                    item.get("id")
                ),
                "memory": str(
                    item.get(
                        "memory",
                        ""
                    ) or ""
                ),
                "subject": str(
                    item.get(
                        "subject",
                        "general"
                    ) or "general"
                ),
                "category": str(
                    item.get(
                        "category",
                        "general"
                    ) or "general"
                ),
                "importance": int(
                    item.get(
                        "importance",
                        5
                    ) or 5
                ),
                "created_at": item.get(
                    "created_at"
                ),
            })

        primary_subject = str(
            group[0].get(
                "subject",
                "general"
            ) or "general"
        ).strip()

        primary_category = str(
            group[0].get(
                "category",
                "general"
            ) or "general"
        ).strip()

        proposals.append({
            "type": "consolidate",
            "memory_ids": [
                item["id"]
                for item in source_memories
            ],
            "subject": primary_subject,
            "category": primary_category,
            "canonical_memory": canonical,
            "reason": reason,
            "evidence_quality": "direct",
            "confidence": confidence,
            "source_memories": source_memories,
            "evidence_count": len(source_memories),
        })

    return {
        "proposals": proposals,
        "suggested_followups": [],
        "confidence": (
            max(
                [
                    int(
                        proposal.get(
                            "confidence",
                            1
                        )
                    )
                    for proposal in proposals
                ]
                or [10]
            )
        ),
        "memory_count": len(all_memories),
        "candidate_group_count": len(candidate_groups),
        "proposal_only": True,
        "auto_saved": False,
    }


# ============================================================
# PHASE 7 — STEP 2
# RECALL INTELLIGENCE LAYER
# ============================================================

def normalize_recall_text(value):
    value = str(value or "").lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def recall_tokens(value):
    text = normalize_recall_text(value)
    tokens = [
        token
        for token in text.split()
        if len(token) >= 3
    ]
    stop_words = {
        "the", "and", "for", "with", "about", "what", "when",
        "where", "which", "who", "does", "did", "this", "that",
        "from", "into", "have", "has", "are", "was", "were",
        "you", "your", "how", "why", "can", "could", "would",
        "should", "tell", "remember", "know", "there", "their",
        "our", "its", "all", "any", "some", "more", "than",
    }
    return set(
        token
        for token in tokens
        if token not in stop_words
    )


def detect_recall_intent(message):
    """Deterministic recall intent; no AI call and no database write."""
    text = normalize_recall_text(message)
    tokens = recall_tokens(message)

    if any(
        word in tokens
        for word in {
            "relationship", "connected", "connection", "linked",
            "partner", "partnership", "collaborate", "collaboration",
        }
    ):
        return "relationship"

    if any(
        word in tokens
        for word in {
            "timeline", "history", "earlier", "previous", "before",
            "initially", "originally", "started", "latest", "recent",
        }
    ):
        return "timeline"

    if any(
        word in tokens
        for word in {
            "person", "people", "founder", "founders", "ceo", "director",
            "partner", "manager", "wife", "brother", "contact",
        }
    ):
        return "person"

    if any(
        word in tokens
        for word in {
            "product", "products", "machine", "technology", "software",
            "platform", "lubricant", "lubricants", "app", "service",
        }
    ):
        return "product"

    if any(
        word in tokens
        for word in {
            "business", "businesses", "company", "companies", "venture",
            "project", "projects", "startup", "investment", "investor",
            "funding", "budget", "sales", "revenue", "commercial",
        }
    ):
        return "business"

    if any(
        word in tokens
        for word in {
            "plan", "planning", "strategy", "launch", "launching",
            "roadmap", "phase", "goal", "goals", "next",
        }
    ):
        return "planning"

    if text:
        return "general"

    return "general"


def recall_subject_match(message, subject):
    query = normalize_recall_text(message)
    subject_text = normalize_recall_text(subject)

    if not query or not subject_text:
        return False

    if subject_text in query:
        return True

    query_words = recall_tokens(message)
    subject_words = recall_tokens(subject)

    if not subject_words:
        return False

    return len(query_words.intersection(subject_words)) >= max(
        1,
        min(2, len(subject_words))
    )


def recall_category_match(intent, category):
    category_text = normalize_recall_text(category)

    mappings = {
        "business": {
            "business", "project", "startup", "investment", "finance",
            "sales", "commercial", "venture", "company",
        },
        "product": {
            "product", "technology", "software", "service", "platform",
        },
        "person": {
            "person", "people", "contact", "relationship",
        },
        "relationship": {
            "relationship", "partnership", "people", "collaboration",
        },
        "timeline": {
            "history", "timeline", "general",
        },
        "planning": {
            "planning", "project", "business", "strategy", "general",
        },
    }

    return category_text in mappings.get(intent, set())


def recall_recency_score(created_at):
    if not created_at:
        return 0.0

    try:
        from datetime import datetime, timezone

        value = str(created_at).replace("Z", "+00:00")
        created = datetime.fromisoformat(value)

        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        age_days = max(
            0.0,
            (now - created.astimezone(timezone.utc)).total_seconds()
            / 86400.0
        )

        return max(
            0.0,
            1.0 - min(age_days, 3650.0) / 3650.0
        )

    except Exception:
        return 0.0


def score_recall_memory(message, memory, session_id, intent):
    query_tokens = recall_tokens(message)
    memory_text = str(memory.get("memory") or "")
    memory_tokens = recall_tokens(memory_text)

    overlap = query_tokens.intersection(memory_tokens)
    token_score = (
        min(
            1.0,
            len(overlap) / max(1, min(6, len(query_tokens)))
        )
        if query_tokens
        else 0.0
    )

    subject_match = recall_subject_match(
        message,
        memory.get("subject")
    )

    category_match = recall_category_match(
        intent,
        memory.get("category")
    )

    session_match = (
        str(memory.get("session_id") or "")
        == str(session_id or "")
    )

    importance = max(
        0.0,
        min(
            1.0,
            float(memory.get("importance") or 5) / 10.0
        )
    )

    recency = recall_recency_score(
        memory.get("created_at")
    )

    score = (
        token_score * 40.0
        + (30.0 if subject_match else 0.0)
        + (10.0 if category_match else 0.0)
        + (8.0 if session_match else 0.0)
        + importance * 7.0
        + recency * 5.0
    )

    reasons = []

    if subject_match:
        reasons.append("subject match")
    if overlap:
        reasons.append("keyword overlap")
    if category_match:
        reasons.append("category match")
    if session_match:
        reasons.append("current session")
    if importance >= 0.8:
        reasons.append("high importance")
    if recency >= 0.8:
        reasons.append("recent")

    return score, reasons


def rank_recall_memories(
    message,
    memories,
    session_id="default",
    limit=30
):
    """Rank already-retrieved memories without changing stored data."""
    intent = detect_recall_intent(message)
    scored = []

    for memory in memories or []:
        score, reasons = score_recall_memory(
            message,
            memory,
            session_id,
            intent
        )

        item = dict(memory)
        item["recall_score"] = round(score, 2)
        item["recall_reasons"] = reasons
        scored.append(item)

    scored.sort(
        key=lambda item: (
            float(item.get("recall_score") or 0),
            int(item.get("importance") or 0),
            str(item.get("created_at") or ""),
        ),
        reverse=True
    )

    selected = scored[:max(1, int(limit or 30))]

    return selected, {
        "intent": intent,
        "candidate_count": len(scored),
        "selected_count": len(selected),
    }


def score_recall_entity(message, entity):
    query_tokens = recall_tokens(message)
    entity_text = " ".join([
        str(entity.get("name") or ""),
        str(entity.get("description") or ""),
        str(entity.get("entity_type") or ""),
    ])
    overlap = query_tokens.intersection(
        recall_tokens(entity_text)
    )

    direct_name = recall_subject_match(
        message,
        entity.get("name")
    )

    score = min(
        1.0,
        len(overlap) / max(1, min(5, len(query_tokens)))
    ) * 70.0

    if direct_name:
        score += 30.0

    return score


def score_recall_relationship(message, relationship):
    text = " ".join([
        str(relationship.get("from") or ""),
        str(relationship.get("relationship") or ""),
        str(relationship.get("to") or ""),
    ])

    query_tokens = recall_tokens(message)
    overlap = query_tokens.intersection(
        recall_tokens(text)
    )

    score = min(
        1.0,
        len(overlap) / max(1, min(6, len(query_tokens)))
    ) * 100.0

    return score


def rank_recall_brain_context(
    message,
    entities,
    relationships,
    entity_limit=40,
    relationship_limit=40
):
    """Keep the most relevant structured Brain context for the answer model."""
    ranked_entities = []

    for item in entities or []:
        value = dict(item)
        value["recall_score"] = round(
            score_recall_entity(message, item),
            2
        )
        ranked_entities.append(value)

    ranked_entities.sort(
        key=lambda item: float(item.get("recall_score") or 0),
        reverse=True
    )

    ranked_relationships = []

    for item in relationships or []:
        value = dict(item)
        value["recall_score"] = round(
            score_recall_relationship(message, item),
            2
        )
        ranked_relationships.append(value)

    ranked_relationships.sort(
        key=lambda item: float(item.get("recall_score") or 0),
        reverse=True
    )

    return (
        ranked_entities[:max(1, int(entity_limit or 40))],
        ranked_relationships[:max(1, int(relationship_limit or 40))]
    )


def build_recall_trace(
    message,
    memories,
    brain_entities,
    brain_relationships,
    recall_meta
):
    """Small read-only trace for live verification; no sensitive extra data."""
    return {
        "intent": recall_meta.get("intent", "general"),
        "candidate_count": int(
            recall_meta.get("candidate_count", 0)
        ),
        "selected_count": int(
            recall_meta.get("selected_count", 0)
        ),
        "memory_ids": [
            int(item["id"])
            for item in memories[:10]
            if item.get("id") is not None
        ],
        "entity_ids": [
            int(item["id"])
            for item in brain_entities[:10]
            if item.get("id") is not None
        ],
        "relationship_ids": [
            int(item["id"])
            for item in brain_relationships[:10]
            if item.get("id") is not None
        ],
    }


# ============================================================
# PHASE 7 — STEP 1A
# GROUNDED ANSWER EVIDENCE TRACE
# ============================================================

def build_grounded_answer_context(
    memories,
    brain_entities,
    brain_relationships,
    history
):
    """
    Build deterministic, ID-addressable evidence context.
    No AI call.
    No database write.
    """

    sources = {
        "memory": {},
        "entity": {},
        "relationship": {},
        "conversation": {},
    }

    memory_lines = []

    for item in memories or []:

        try:
            source_id = int(item.get("id"))
        except Exception:
            continue

        memory_text = str(
            item.get("memory") or ""
        ).strip()

        if not memory_text:
            continue

        sources["memory"][source_id] = {
            "source_type": "memory",
            "source_id": source_id,
            "label": "Memory #" + str(source_id),
            "text": memory_text,
            "subject": str(
                item.get("subject") or ""
            ),
            "category": str(
                item.get("category") or ""
            ),
        }

        memory_lines.append(
            "[MEMORY "
            + str(source_id)
            + "] "
            + memory_text
            + " | subject: "
            + str(item.get("subject") or "")
            + " | category: "
            + str(item.get("category") or "")
        )

    entity_lines = []

    for item in brain_entities or []:

        try:
            source_id = int(item.get("id"))
        except Exception:
            continue

        name = str(
            item.get("name") or ""
        ).strip()

        description = str(
            item.get("description") or ""
        ).strip()

        if not name:
            continue

        sources["entity"][source_id] = {
            "source_type": "entity",
            "source_id": source_id,
            "label": "Entity #" + str(source_id),
            "text": (
                name
                + (
                    " — " + description
                    if description
                    else ""
                )
            ),
        }

        entity_lines.append(
            "[ENTITY "
            + str(source_id)
            + "] "
            + name
            + " | type: "
            + str(item.get("entity_type") or "")
            + " | description: "
            + description
        )

    relationship_lines = []

    for item in brain_relationships or []:

        try:
            source_id = int(item.get("id"))
        except Exception:
            continue

        from_name = str(
            item.get("from") or ""
        ).strip()

        relationship_name = str(
            item.get("relationship") or ""
        ).strip()

        to_name = str(
            item.get("to") or ""
        ).strip()

        if not from_name or not relationship_name or not to_name:
            continue

        relation_text = (
            from_name
            + " -> "
            + relationship_name
            + " -> "
            + to_name
        )

        sources["relationship"][source_id] = {
            "source_type": "relationship",
            "source_id": source_id,
            "label": "Relationship #" + str(source_id),
            "text": relation_text,
        }

        relationship_lines.append(
            "[RELATIONSHIP "
            + str(source_id)
            + "] "
            + relation_text
        )

    conversation_lines = []

    for index, item in enumerate(history or []):

        role = str(
            item.get("role") or ""
        ).strip()

        message_text = str(
            item.get("message") or ""
        ).strip()

        if not message_text:
            continue

        sources["conversation"][index] = {
            "source_type": "conversation",
            "source_id": index,
            "label": "Conversation #" + str(index + 1),
            "text": (
                role
                + ": "
                + message_text
            ),
        }

        conversation_lines.append(
            "[CONVERSATION "
            + str(index)
            + "] "
            + role
            + ": "
            + message_text
        )

    context = (
        "STORED MEMORIES:\n"
        + (
            "\n".join(memory_lines)
            if memory_lines
            else "None"
        )
        + "\n\nSTRUCTURED ENTITIES:\n"
        + (
            "\n".join(entity_lines)
            if entity_lines
            else "None"
        )
        + "\n\nSTRUCTURED RELATIONSHIPS:\n"
        + (
            "\n".join(relationship_lines)
            if relationship_lines
            else "None"
        )
        + "\n\nCURRENT CONVERSATION:\n"
        + (
            "\n".join(conversation_lines)
            if conversation_lines
            else "None"
        )
    )

    return context, sources


def validate_grounded_answer_trace(
    result,
    sources
):
    """
    Accept only evidence references that actually exist
    in the supplied context.
    """

    if not isinstance(result, dict):
        return {
            "answer": "",
            "evidence_trace": [],
            "grounded": False,
        }

    answer = str(
        result.get("answer") or ""
    ).strip()

    raw_evidence = result.get(
        "evidence",
        []
    )

    if not isinstance(raw_evidence, list):
        raw_evidence = []

    evidence_trace = []
    seen = set()

    for item in raw_evidence:

        if not isinstance(item, dict):
            continue

        source_type = str(
            item.get("source_type") or ""
        ).strip().lower()

        if source_type not in sources:
            continue

        try:
            source_id = int(
                item.get("source_id")
            )
        except Exception:
            continue

        key = (
            source_type,
            source_id
        )

        if key in seen:
            continue

        source = sources[source_type].get(
            source_id
        )

        if not source:
            continue

        seen.add(key)

        evidence_trace.append({
            "source_type": source["source_type"],
            "source_id": source["source_id"],
            "label": source["label"],
            "text": source["text"],
        })

    return {
        "answer": answer,
        "evidence_trace": evidence_trace[:10],
        "grounded": bool(
            answer
            and evidence_trace
        ),
    }


def generate_grounded_answer(
    message,
    session_id,
    title,
    memories,
    brain_entities,
    brain_relationships,
    history
):
    """
    Generate the answer and evidence references
    in ONE AI call.
    """

    context, sources = build_grounded_answer_context(
        memories=memories,
        brain_entities=brain_entities,
        brain_relationships=brain_relationships,
        history=history,
    )

    system_prompt = f"""
You are Dusra Brain, a personal AI brain and memory assistant.

Your job is to answer the user's question using ONLY the supplied evidence.

Current session:
{session_id}

Current session title:
{title}

GROUNDING RULES:

1. Stored memories are facts explicitly provided by the user.
2. Never invent personal facts.
3. Prefer directly relevant evidence.
4. Use current conversation when the question refers to it.
5. Use stored memories for stored user facts.
6. Use structured entities and relationships when relevant.
7. Do not mix unrelated project or business memories.
8. If the evidence is insufficient, explicitly say so.
9. Never manufacture a source ID.
10. Every evidence reference must point to a source in the supplied context.
11. Prefer the smallest set of evidence needed to support the answer.
12. Do not cite a source only because it contains a generic shared word.

OUTPUT FORMAT:

Return ONLY valid JSON.

{{
  "answer": "normal user-facing answer",
  "evidence": [
    {{
      "source_type": "memory",
      "source_id": 123
    }}
  ]
}}

ALLOWED source_type values:

memory
entity
relationship
conversation

For conversation evidence, source_id is the exact CONVERSATION index shown
in the supplied context.

If there is not enough evidence:

{{
  "answer": "I don't have enough stored information to answer that reliably.",
  "evidence": []
}}

SUPPLIED EVIDENCE:

{context}
"""

    try:

        raw = groq_request(
            [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": message,
                },
            ],
            temperature=0.1,
        )

        cleaned = clean_json_response(
            raw
        )

        result = json.loads(
            cleaned
        )

        return validate_grounded_answer_trace(
            result,
            sources
        )

    except Exception:

        return {
            "answer": (
                "I couldn't generate a grounded answer "
                "from the available stored information."
            ),
            "evidence_trace": [],
            "grounded": False,
        }


class handler(
    BaseHTTPRequestHandler
):

    # ========================================================
    # OPTIONS
    # ========================================================

    def do_OPTIONS(self):

        self.send_response(
            204
        )

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

        parsed = urlparse(
            self.path
        )

        params = parse_qs(
            parsed.query
        )

        user_id = params.get(
            "user_id",
            ["default_user"]
        )[0]


        # ----------------------------------------------------
        # PHASE 6 — INITIAL MEMORY VERSION BASELINE
        # ----------------------------------------------------

        if params.get(
            "memory_versions_seed"
        ) == ["true"]:

            try:

                result = seed_existing_memory_versions(
                    user_id=user_id
                )

                send_json(
                    self,
                    {
                        "status": "ok",
                        "message":
                            "Existing memories were preserved and "
                            "baseline versions were created.",
                        **result,
                        "memory_rows_modified": 0,
                        "memory_rows_deleted": 0,
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
        # PHASE 6 — MEMORY VERSION HISTORY
        # ----------------------------------------------------

        if params.get(
            "memory_versions"
        ) == ["true"]:

            memory_id = params.get(
                "memory_id",
                [""]
            )[0]

            subject = params.get(
                "subject",
                [""]
            )[0]

            try:

                parsed_memory_id = None

                if str(
                    memory_id or ""
                ).strip():

                    parsed_memory_id = int(
                        memory_id
                    )

                versions = get_memory_versions(
                    user_id,
                    memory_id=parsed_memory_id,
                    subject=subject,
                )

                send_json(
                    self,
                    {
                        "versions": versions,
                        "count": len(versions),
                        "summary":
                            get_memory_version_summary(
                                user_id
                            ),
                        "baseline_seeded": True,
                        "proposal_only": False,
                        "automatic_deletion": False,
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
        # PHASE 6 — MEMORY CONSOLIDATION REVIEW HISTORY
        # ----------------------------------------------------

        if params.get(
            "memory_consolidation_reviews"
        ) == ["true"]:

            subject = params.get("subject", [""])[0]
            status = params.get("status", [""])[0]

            try:
                reviews = get_memory_consolidation_reviews(
                    user_id,
                    subject=subject,
                    status=status,
                    limit=params.get("limit", ["100"])[0],
                )

                send_json(
                    self,
                    {
                        "reviews": reviews,
                        "count": len(reviews),
                        "summary": get_memory_consolidation_review_summary(user_id),
                        "read_only": True,
                    }
                )

            except Exception as error:
                send_json(
                    self,
                    {"error": str(error)},
                    500,
                )

            return


        # STEP 22 — BRAIN LEARNING REVIEW HISTORY

        # ----------------------------------------------------
        # ALL MEMORIES
        # ----------------------------------------------------

        if params.get(
            "memories"
        ) == ["true"]:

            try:

                memories = get_all_user_memories(
                    user_id,
                    limit=500
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
        # BRAIN ENTITIES
        # ----------------------------------------------------

        if params.get(
            "entities"
        ) == ["true"]:

            try:

                entities = get_brain_entities(
                    user_id
                )

                send_json(
                    self,
                    {
                        "entities":
                            entities,

                        "count":
                            len(entities),
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
        # BRAIN INTELLIGENCE
        # ----------------------------------------------------

        if params.get(
            "intelligence"
        ) == ["true"]:

            entity_name = params.get(
                "name",
                [""]
            )[0]

            try:

                result = generate_brain_intelligence(
                    user_id,
                    entity_name,
                )

                send_json(
                    self,
                    result
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
        # PHASE 6 — MEMORY CONSOLIDATION PROPOSALS
        # ----------------------------------------------------

        if params.get(
            "consolidate"
        ) == ["true"]:

            subject = params.get(
                "subject",
                [""],
            )[0]

            try:
                result = build_memory_consolidation_proposals(
                    user_id,
                    subject=subject,
                )
                send_json(self, result)
            except Exception as error:
                send_json(
                    self,
                    {"error": str(error)},
                    500,
                )
            return


        # ----------------------------------------------------
        # BRAIN LEARNING / RELATIONSHIP DISCOVERY
        # ----------------------------------------------------

        if params.get(
            "discover"
        ) == ["true"]:

            try:

                result = discover_brain_relationships(
                    user_id,
                )

                send_json(
                    self,
                    result,
                )

            except Exception as error:

                send_json(
                    self,
                    {
                        "error":
                            str(error),
                    },
                    500,
                )

            return


        # ----------------------------------------------------
        # CROSS-MEMORY INTELLIGENCE
        # ----------------------------------------------------

        if params.get(
            "cross_memory"
        ) == ["true"]:

            entity_name = params.get(
                "name",
                [""],
            )[0]

            try:

                result = generate_cross_memory_intelligence(
                    user_id,
                    entity_name,
                )

                send_json(
                    self,
                    result,
                )

            except Exception as error:

                send_json(
                    self,
                    {
                        "error":
                            str(error),
                    },
                    500,
                )

            return


        # ----------------------------------------------------
        # PROJECT INSIGHTS
        # ----------------------------------------------------

        if params.get(
            "insights"
        ) == ["true"]:

            entity_name = params.get(
                "name",
                [""],
            )[0]

            try:

                result = generate_project_insights(
                    user_id,
                    entity_name,
                )

                send_json(
                    self,
                    result,
                )

            except Exception as error:

                send_json(
                    self,
                    {
                        "error":
                            str(error),
                    },
                    500,
                )

            return


        # ----------------------------------------------------
        # BRAIN EXPLORER
        # ----------------------------------------------------

        if params.get(
            "explore"
        ) == ["true"]:

            entity_name = params.get(
                "name",
                [""]
            )[0]

            try:

                result = explore_brain(
                    user_id,
                    entity_name
                )

                send_json(
                    self,
                    result
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
        # BRAIN GRAPH EXPLORER
        # ----------------------------------------------------

        if params.get(
            "graph"
        ) == ["true"]:

            entity_name = params.get(
                "name",
                [""]
            )[0]

            depth = params.get(
                "depth",
                ["3"]
            )[0]

            limit = params.get(
                "limit",
                ["100"]
            )[0]

            try:

                result = explore_brain_graph(
                    user_id,
                    entity_name,
                    depth,
                    limit,
                )

                send_json(
                    self,
                    result
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
        # BRAIN RELATIONSHIPS
        # ----------------------------------------------------

        if params.get(
            "relationships"
        ) == ["true"]:

            try:

                relationships = get_brain_relationships(
                    user_id
                )

                send_json(
                    self,
                    {
                        "relationships":
                            relationships,

                        "count":
                            len(relationships),
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
        # CONVERSATIONS
        # ----------------------------------------------------

        if params.get(
            "conversations"
        ) == ["true"]:

            try:

                conversations = get_all_conversations(
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

        if params.get(
            "sessions"
        ) == ["true"]:

            try:

                sessions = get_sessions(
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

        if params.get(
            "session"
        ) == ["true"]:

            session_id = params.get(
                "session_id",
                ["default"]
            )[0]

            try:

                history = get_conversation_history(
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

                "memory_versioning":
                    True,

                "versioned_memory_updates":
                    True,
            }
        )


    # ========================================================
    # POST
    # ========================================================

    def do_POST(self):

        try:

            content_length = int(
                self.headers.get(
                    "Content-Length",
                    0
                )
            )

            raw_body = self.rfile.read(
                content_length
            )

            body = json.loads(
                raw_body.decode(
                    "utf-8"
                )
            )

            message = str(
                body.get(
                    "message",
                    ""
                )
            ).strip()

            user_id = body.get(
                "user_id",
                "default_user"
            )

            session_id = body.get(
                "session_id",
                "default"
            )

            title = body.get(
                "title",
                "New Chat"
            )

            action = str(
                body.get(
                    "action",
                    ""
                )
            ).strip().lower()

            if action == "update_memory_version":

                memory_id = body.get(
                    "memory_id"
                )

                if memory_id is None:

                    send_json(
                        self,
                        {
                            "updated": False,
                            "error":
                                "memory_id is required."
                        },
                        400
                    )

                    return

                try:

                    result = update_memory_with_version(
                        user_id=user_id,
                        memory_id=int(
                            memory_id
                        ),
                        memory=body.get(
                            "memory"
                        ),
                        category=body.get(
                            "category"
                        ),
                        importance=body.get(
                            "importance"
                        ),
                        subject=body.get(
                            "subject"
                        ),
                        session_id=body.get(
                            "session_id"
                        ),
                        change_reason=str(
                            body.get(
                                "change_reason",
                                "memory_updated"
                            )
                            or "memory_updated"
                        ),
                    )

                    send_json(
                        self,
                        result,
                        200
                        if result.get(
                            "updated"
                        )
                        or result.get(
                            "changed"
                        ) is False
                        else 400
                    )

                except Exception as error:

                    send_json(
                        self,
                        {
                            "updated": False,
                            "error":
                                str(error)
                        },
                        500
                    )

                return


            if action in [
                "approve_brain_learning",
                "reject_brain_learning",
            ]:

                proposal = body.get(
                    "proposal",
                    {}
                )

                if action == "approve_brain_learning":

                    result = approve_brain_learning_proposal(
                        user_id,
                        proposal
                    )

                    send_json(
                        self,
                        result,
                        200 if result.get("approved") else 400
                    )

                    return

                result = reject_brain_learning_proposal(
                    user_id,
                    proposal
                )

                send_json(
                    self,
                    result,
                    200 if result.get("rejected") else 400
                )

                return

            if action in [
                "approve_memory_consolidation",
                "reject_memory_consolidation",
            ]:

                proposal = body.get(
                    "proposal",
                    {}
                )

                if action == "approve_memory_consolidation":

                    result = approve_memory_consolidation_proposal(
                        user_id,
                        proposal
                    )

                    send_json(
                        self,
                        result,
                        200
                        if result.get("approved")
                        else 400
                    )

                    return

                result = reject_memory_consolidation_proposal(
                    user_id,
                    proposal
                )

                send_json(
                    self,
                    result,
                    200
                    if result.get("rejected")
                    else 400
                )

                return

            session_id = str(
                session_id or "default"
            )

            title = str(
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
            # SESSION HISTORY
            # ------------------------------------------------

            history = get_conversation_history(
                user_id,
                session_id=session_id,
                limit=20
            )


            # ------------------------------------------------
            # SMART MEMORY RETRIEVAL
            # ------------------------------------------------

            memories = get_relevant_memories(
                user_id,
                message,
                session_id=session_id,
                limit=50
            )


            # ------------------------------------------------
            # PHASE 7 — STEP 2
            # RECALL INTELLIGENCE — MEMORY RANKING
            # ------------------------------------------------

            memories, recall_meta = rank_recall_memories(
                message=message,
                memories=memories,
                session_id=session_id,
                limit=30
            )


            # ------------------------------------------------
            # MEMORY TEXT
            # ------------------------------------------------

            if memories:

                memory_text = "\n".join(
                    [
                        (
                            "- "
                            + item["memory"]
                            + " | subject: "
                            + item["subject"]
                            + " | category: "
                            + item["category"]
                            + " | importance: "
                            + str(
                                item["importance"]
                            )
                            + " | session: "
                            + item["session_id"]
                        )
                        for item in memories
                    ]
                )

            else:

                memory_text = (
                    "No stored memories available."
                )


            # ------------------------------------------------
            # BRAIN CONTEXT
            # ------------------------------------------------

            try:

                brain_entities = get_brain_entities(
                    user_id,
                    limit=100
                )

                brain_relationships = get_brain_relationships(
                    user_id,
                    limit=100
                )

            except Exception:

                brain_entities = []

                brain_relationships = []


            # ------------------------------------------------
            # PHASE 7 — STEP 2
            # RECALL INTELLIGENCE — BRAIN RANKING
            # ------------------------------------------------

            brain_entities, brain_relationships = rank_recall_brain_context(
                message=message,
                entities=brain_entities,
                relationships=brain_relationships,
                entity_limit=40,
                relationship_limit=40
            )


            recall_trace = build_recall_trace(
                message=message,
                memories=memories,
                brain_entities=brain_entities,
                brain_relationships=brain_relationships,
                recall_meta=recall_meta
            )


            if brain_entities:

                entity_text = "\n".join(
                    [
                        (
                            "- "
                            + item["name"]
                            + " | type: "
                            + item["entity_type"]
                            + " | description: "
                            + str(
                                item["description"] or ""
                            )
                        )
                        for item in brain_entities
                    ]
                )

            else:

                entity_text = (
                    "No structured entities available."
                )


            if brain_relationships:

                relationship_text = "\n".join(
                    [
                        (
                            "- "
                            + item["from"]
                            + " -> "
                            + item["relationship"]
                            + " -> "
                            + item["to"]
                        )
                        for item in brain_relationships
                    ]
                )

            else:

                relationship_text = (
                    "No structured relationships available."
                )


            # ------------------------------------------------
            # HISTORY TEXT
            # ------------------------------------------------

            if history:

                history_text = "\n".join(
                    [
                        item["role"]
                        + ": "
                        + item["message"]
                        for item in history
                    ]
                )

            else:

                history_text = (
                    "No previous conversation."
                )


            # ------------------------------------------------
            # PHASE 7 — STEP 1A
            # GROUNDED ANSWER + EVIDENCE TRACE
            # ------------------------------------------------

            grounded_result = generate_grounded_answer(
                message=message,
                session_id=session_id,
                title=title,
                memories=memories,
                brain_entities=brain_entities,
                brain_relationships=brain_relationships,
                history=history,
            )

            response = grounded_result.get(
                "answer",
                ""
            ).strip()

            evidence_trace = grounded_result.get(
                "evidence_trace",
                []
            )

            grounded = bool(
                grounded_result.get(
                    "grounded",
                    False
                )
            )


            # ------------------------------------------------
            # SAVE ASSISTANT MESSAGE
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

                analysis = analyze_memory(
                    message,
                    current_subject=title
                )

                if analysis.get(
                    "remember"
                ):

                    memory_value = str(
                        analysis.get(
                            "memory",
                            ""
                        )
                    ).strip()

                    category = str(
                        analysis.get(
                            "category",
                            "general"
                        )
                    ).strip()

                    importance = int(
                        analysis.get(
                            "importance",
                            5
                        )
                    )

                    subject = normalize_subject(
                        analysis.get(
                            "subject",
                            title
                        )
                    )

                    if memory_value:

                        save_memory(
                            user_id=user_id,

                            memory=
                                memory_value,

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


            # ------------------------------------------------
            # STRUCTURED BRAIN EXTRACTION
            # ------------------------------------------------

            try:

                save_brain_structure(
                    user_id=user_id,
                    user_message=message,
                    current_subject=title
                )

            except Exception:

                pass


            # ------------------------------------------------
            # RESPONSE
            # ------------------------------------------------

            send_json(
                self,
                {
                    "response":
                        response,

                    "evidence_trace":
                        evidence_trace,

                    "evidence_count":
                        len(
                            evidence_trace
                        ),

                    "grounded":
                        grounded,

                    "recall_trace":
                        recall_trace,

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

            content_length = int(
                self.headers.get(
                    "Content-Length",
                    0
                )
            )

            raw_body = self.rfile.read(
                content_length
            )

            body = json.loads(
                raw_body.decode(
                    "utf-8"
                )
            )

            memory_id = body.get(
                "id"
            )

            memory = str(
                body.get(
                    "memory",
                    ""
                )
            ).strip()

            category = str(
                body.get(
                    "category",
                    "general"
                )
            ).strip()

            importance = int(
                body.get(
                    "importance",
                    5
                )
            )

            subject = normalize_subject(
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


            memory_key = make_memory_key(
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

                    row = cur.fetchone()

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
                "id":
                    row[0],

                "memory":
                    row[1],

                "created_at":
                    row[2].isoformat()
                    if row[2]
                    else None,

                "category":
                    row[3],

                "importance":
                    row[4],

                "subject":
                    row[5],

                "memory_key":
                    row[6],

                "session_id":
                    row[7] or "default",
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

            parsed = urlparse(
                self.path
            )

            params = parse_qs(
                parsed.query
            )

            memory_id = params.get(
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

                    deleted = cur.fetchone()

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
                        deleted[0],
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
