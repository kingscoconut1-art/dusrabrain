import json
import hashlib
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
# PHASE 7 — STEP 4B
# DECISION CONTEXT INTERPRETER
# ============================================================

def interpret_decision_context(decision_context):
    """
    Deterministically interpret the already-built Step 4A decision context.

    This layer does not call the model, query the database, write memory,
    create evidence, rank recall results, or recommend a decision. It only
    classifies the supplied context using explicit fields already present.
    """
    context = decision_context if isinstance(decision_context, dict) else {}

    intent = str(context.get("intent") or "general").strip().lower()
    decision = context.get("decision", [])
    options = context.get("options", [])
    goals = context.get("goals", [])
    constraints = context.get("constraints", [])
    risks = context.get("risks", [])
    uncertainties = context.get("uncertainties", [])
    tradeoffs = context.get("tradeoffs", [])
    missing = context.get("missing_information", [])

    decision_present = isinstance(decision, list) and bool(decision)
    option_count = len(options) if isinstance(options, list) else 0
    goal_count = len(goals) if isinstance(goals, list) else 0
    constraint_count = len(constraints) if isinstance(constraints, list) else 0
    risk_count = len(risks) if isinstance(risks, list) else 0
    uncertainty_count = len(uncertainties) if isinstance(uncertainties, list) else 0
    tradeoff_count = len(tradeoffs) if isinstance(tradeoffs, list) else 0
    missing_count = len(missing) if isinstance(missing, list) else 0

    decision_signal = decision_present or option_count > 0 or tradeoff_count > 0
    planning_signal = intent in {"planning", "plan", "strategy", "strategic"} or goal_count > 0 or constraint_count > 0

    if decision_signal and planning_signal:
        context_type = "mixed"
    elif decision_signal:
        context_type = "decision"
    elif planning_signal:
        context_type = "planning"
    else:
        context_type = "informational"

    return {
        "context_type": context_type,
        "decision_relevance": bool(decision_present or option_count > 0),
        "goal_relevance": bool(goal_count > 0),
        "option_relevance": bool(option_count > 0),
        "risk_relevance": bool(risk_count > 0),
        "constraint_relevance": bool(constraint_count > 0),
        "tradeoff_relevance": bool(tradeoff_count > 0),
        "uncertainty_relevance": bool(uncertainty_count > 0),
        "information_gap": bool(missing_count > 0),
        "source_intent": intent or "general",
        "counts": {
            "decisions": 1 if decision_present else 0,
            "options": option_count,
            "goals": goal_count,
            "constraints": constraint_count,
            "risks": risk_count,
            "uncertainties": uncertainty_count,
            "tradeoffs": tradeoff_count,
            "missing_information": missing_count,
        },
    }


def build_decision_context_interpretation_trace(
    decision_context,
    interpretation,
):
    """Compact public verification trace for Step 4B."""
    context = decision_context if isinstance(decision_context, dict) else {}
    result = interpretation if isinstance(interpretation, dict) else {}

    return {
        "built": bool(context),
        "interpreted": bool(result),
        "context_type": str(result.get("context_type") or "informational"),
        "decision_relevance": bool(result.get("decision_relevance", False)),
        "planning_relevance": str(result.get("context_type") or "") in {"planning", "mixed"},
        "option_relevance": bool(result.get("option_relevance", False)),
        "goal_relevance": bool(result.get("goal_relevance", False)),
        "risk_relevance": bool(result.get("risk_relevance", False)),
        "information_gap": bool(result.get("information_gap", False)),
        "source_intent": str(result.get("source_intent") or "general"),
        "counts": dict(result.get("counts", {})) if isinstance(result.get("counts", {}), dict) else {},
    }


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
            "plan", "plans", "planning", "strategy", "strategies",
            "launch", "launching", "roadmap", "phase", "goal", "goals", "next",
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
# PHASE 7 — STEP 3A
# REASONING CONTEXT BUILDER
# ============================================================

def build_reasoning_context(
    message,
    session_id,
    title,
    memories,
    brain_entities,
    brain_relationships,
    history,
    recall_trace=None,
    evidence_trace=None,
):
    """
    Build a deterministic, read-only context package for the future
    Reasoning layer.

    This function does NOT call the model.
    This function does NOT write to the database.
    It only organizes the already-ranked context produced by Recall
    Intelligence and the already-validated evidence produced by the
    Grounded Answer layer.

    The returned package is intentionally source-addressable so a later
    reasoning layer can reason across memories, entities, relationships,
    conversation history, recall, and evidence without re-querying or
    reconstructing context.
    """

    message = str(
        message or ""
    ).strip()

    session_id = str(
        session_id or "default"
    )

    title = str(
        title or "New Chat"
    )

    memories = (
        memories
        if isinstance(memories, list)
        else []
    )

    brain_entities = (
        brain_entities
        if isinstance(brain_entities, list)
        else []
    )

    brain_relationships = (
        brain_relationships
        if isinstance(brain_relationships, list)
        else []
    )

    history = (
        history
        if isinstance(history, list)
        else []
    )

    recall_trace = (
        recall_trace
        if isinstance(recall_trace, dict)
        else {}
    )

    evidence_trace = (
        evidence_trace
        if isinstance(evidence_trace, list)
        else []
    )

    # Keep the context deterministic and bounded.
    selected_memories = [
        dict(item)
        for item in memories[:30]
        if isinstance(item, dict)
    ]

    selected_entities = [
        dict(item)
        for item in brain_entities[:40]
        if isinstance(item, dict)
    ]

    selected_relationships = [
        dict(item)
        for item in brain_relationships[:40]
        if isinstance(item, dict)
    ]

    selected_history = [
        dict(item)
        for item in history[-20:]
        if isinstance(item, dict)
    ]

    selected_evidence = [
        dict(item)
        for item in evidence_trace[:10]
        if isinstance(item, dict)
    ]

    memory_ids = [
        int(item["id"])
        for item in selected_memories
        if item.get("id") is not None
    ]

    entity_ids = [
        int(item["id"])
        for item in selected_entities
        if item.get("id") is not None
    ]

    relationship_ids = [
        int(item["id"])
        for item in selected_relationships
        if item.get("id") is not None
    ]

    history_indexes = list(
        range(
            len(selected_history)
        )
    )

    return {
        "question": message,

        "session": {
            "id": session_id,
            "title": title,
        },

        "intent": str(
            recall_trace.get(
                "intent",
                "general"
            )
            or "general"
        ),

        "memories": selected_memories,

        "entities": selected_entities,

        "relationships": selected_relationships,

        "conversation": selected_history,

        "recall_trace": dict(
            recall_trace
        ),

        "evidence_trace": selected_evidence,

        "source_index": {
            "memory_ids": memory_ids,
            "entity_ids": entity_ids,
            "relationship_ids": relationship_ids,
            "conversation_indexes": history_indexes,
        },

        "source_counts": {
            "memories": len(
                selected_memories
            ),
            "entities": len(
                selected_entities
            ),
            "relationships": len(
                selected_relationships
            ),
            "conversation_messages": len(
                selected_history
            ),
            "evidence_sources": len(
                selected_evidence
            ),
        },
    }


def build_reasoning_context_trace(
    context
):
    """
    Return a compact, safe verification trace for the frontend/API.

    The full reasoning context remains an internal backend structure.
    The trace exposes only counts and source IDs needed to verify that
    Step 3A assembled the expected context.
    """

    if not isinstance(
        context,
        dict
    ):
        return {
            "built": False,
            "source_counts": {},
            "source_index": {},
        }

    source_counts = context.get(
        "source_counts",
        {}
    )

    source_index = context.get(
        "source_index",
        {}
    )

    return {
        "built": True,

        "intent": str(
            context.get(
                "intent",
                "general"
            )
            or "general"
        ),

        "source_counts": {
            "memories": int(
                source_counts.get(
                    "memories",
                    0
                )
                or 0
            ),
            "entities": int(
                source_counts.get(
                    "entities",
                    0
                )
                or 0
            ),
            "relationships": int(
                source_counts.get(
                    "relationships",
                    0
                )
                or 0
            ),
            "conversation_messages": int(
                source_counts.get(
                    "conversation_messages",
                    0
                )
                or 0
            ),
            "evidence_sources": int(
                source_counts.get(
                    "evidence_sources",
                    0
                )
                or 0
            ),
        },

        "source_index": {
            "memory_ids": [
                int(item)
                for item in source_index.get(
                    "memory_ids",
                    []
                )
                if item is not None
            ][:30],

            "entity_ids": [
                int(item)
                for item in source_index.get(
                    "entity_ids",
                    []
                )
                if item is not None
            ][:40],

            "relationship_ids": [
                int(item)
                for item in source_index.get(
                    "relationship_ids",
                    []
                )
                if item is not None
            ][:40],

            "conversation_indexes": [
                int(item)
                for item in source_index.get(
                    "conversation_indexes",
                    []
                )
                if item is not None
            ][:20],
        },
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


# ============================================================
# PHASE 7 — DECISION INPUT EXTRACTION
# ============================================================

def looks_like_decision_request(message):
    """Cheap gate so the extraction model is only called for decision-like requests."""
    text = str(message or "").strip().lower()
    if not text:
        return False

    phrases = [
        "should i", "should we", "which should", "which one", "help me decide",
        "help me choose", "need to decide", "decision", "decide between",
        "choose between", "compare", "or should", "whether i should",
        "whether we should", "option", "options", "alternative",
    ]
    return any(phrase in text for phrase in phrases)


def extract_decision_context_from_text(message, history=None):
    """
    Extract only decision information explicitly stated by the user.
    This is an interpretation layer; it does not choose, recommend, or
    invent facts. If the request is not decision-like, it returns empty data.
    """
    message = str(message or "").strip()
    history = history if isinstance(history, list) else []

    empty = {
        "decision": [],
        "options": [],
        "goals": [],
        "constraints": [],
        "risks": [],
        "uncertainties": [],
        "tradeoffs": [],
        "missing_information": [],
    }

    if not message or not looks_like_decision_request(message):
        return empty

    recent = []
    for item in history[-8:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        content = str(item.get("message") or "").strip()
        if content:
            recent.append({"role": role, "message": content})

    prompt = f"""
Extract decision-support structure from the user's own words.

STRICT RULES:
1. Use only information explicitly stated by the user in the current message
   or the recent conversation below.
2. Do not invent options, facts, risks, goals, constraints, numbers, dates,
   or recommendations.
3. If the user asks a comparison such as "A or B", the two explicitly named
   alternatives are options.
4. Convert the user's decision question into one concise decision statement.
5. Only populate a field when the content is explicitly present.
6. Missing optional fields are NOT errors.
7. Never choose an option and never recommend one.
8. Return JSON only.

CURRENT USER MESSAGE:
{message}

RECENT CONVERSATION:
{json.dumps(recent, ensure_ascii=False)}

JSON:
{{
  "decision": [],
  "options": [],
  "goals": [],
  "constraints": [],
  "risks": [],
  "uncertainties": [],
  "tradeoffs": [],
  "missing_information": []
}}
"""

    try:
        raw = groq_request(
            [
                {
                    "role": "system",
                    "content": (
                        "You extract explicit decision-support information. "
                        "You never make the decision."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0,
        )
        parsed = json.loads(clean_json_response(raw))
        if not isinstance(parsed, dict):
            return empty

        result = {}
        for key in empty:
            value = parsed.get(key, [])
            if isinstance(value, list):
                result[key] = [
                    item if isinstance(item, dict) else str(item).strip()
                    for item in value[:20]
                    if str(item).strip()
                ]
            else:
                result[key] = []

        return result
    except Exception:
        return empty


# PHASE 7 — STEP 4A
# DECISION CONTEXT BUILDER
# ============================================================

def build_decision_context(

    reasoning_context,
):
    """
    Build a deterministic, read-only decision context from the already
    validated Step 3A reasoning context.

    This layer does not call the model, query the database, write memory,
    create evidence, rank recall results, or make a decision. It only
    surfaces decision-relevant fields that are explicitly present in the
    supplied context and reports what is not explicitly available.
    """

    context = (
        reasoning_context
        if isinstance(reasoning_context, dict)
        else {}
    )

    extracted = (
        extracted_context
        if isinstance(extracted_context, dict)
        else {}
    )

    question = str(
        context.get("question") or ""
    ).strip()

    intent = str(
        context.get("intent") or "general"
    ).strip()

    source_collections = [
        ("memory", context.get("memories", [])),
        ("entity", context.get("entities", [])),
        ("relationship", context.get("relationships", [])),
        ("conversation", context.get("conversation", [])),
    ]

    field_aliases = {
        "decision": ["decision", "decision_question", "decision_context"],
        "options": ["options", "option", "alternatives", "alternative_options"],
        "goals": ["goals", "goal", "objectives", "objective"],
        "constraints": ["constraints", "constraint", "requirements", "requirement"],
        "risks": ["risks", "risk", "risk_factors"],
        "uncertainties": ["uncertainties", "uncertainty", "unknowns", "unknown"],
        "tradeoffs": ["tradeoffs", "trade_offs", "tradeoff", "trade_off"],
        "missing_information": ["missing_information", "missing", "gaps", "information_gaps"],
    }

    def normalize_values(value, limit=20):
        if value is None:
            return []

        if isinstance(value, (list, tuple)):
            values = list(value)
        else:
            values = [value]

        normalized = []

        for item in values[:limit]:
            if isinstance(item, dict):
                normalized.append(dict(item))
                continue

            text_value = str(item or "").strip()
            if text_value:
                normalized.append(text_value)

        return normalized

    def collect_explicit(field):
        values = []
        seen = set()

        for source_type, collection in source_collections:
            if not isinstance(collection, list):
                continue

            for item in collection:
                if not isinstance(item, dict):
                    continue

                for alias in field_aliases[field]:
                    if alias not in item:
                        continue

                    for value in normalize_values(item.get(alias)):
                        try:
                            key = json.dumps(
                                value,
                                ensure_ascii=False,
                                sort_keys=True,
                            )
                        except Exception:
                            key = str(value)

                        if key in seen:
                            continue

                        seen.add(key)
                        values.append(value)

                        if len(values) >= 20:
                            return values

        return values

    decision_values = collect_explicit("decision")
    options = collect_explicit("options")
    goals = collect_explicit("goals")
    constraints = collect_explicit("constraints")
    risks = collect_explicit("risks")
    uncertainties = collect_explicit("uncertainties")
    tradeoffs = collect_explicit("tradeoffs")
    missing_information = collect_explicit("missing_information")

    # Merge the explicit user-language extraction without allowing it to
    # overwrite stronger structured context already present in the system.
    for field_name, current in [
        ("decision", decision_values),
        ("options", options),
        ("goals", goals),
        ("constraints", constraints),
        ("risks", risks),
        ("uncertainties", uncertainties),
        ("tradeoffs", tradeoffs),
        ("missing_information", missing_information),
    ]:
        extracted_values = extracted.get(field_name, [])
        if not isinstance(extracted_values, list):
            continue
        for value in extracted_values:
            if value is None or str(value).strip() == "":
                continue
            if value not in current:
                current.append(value)

    # Do NOT manufacture missing requirements for optional decision fields.
    # Only explicitly identified gaps are blocking.

    source_counts = context.get(
        "source_counts",
        {}
    )

    return {
        "question": question,
        "intent": intent,
        "decision": decision_values[:1],
        "options": options,
        "goals": goals,
        "constraints": constraints,
        "risks": risks,
        "uncertainties": uncertainties,
        "tradeoffs": tradeoffs,
        "evidence_trace": [
            *[
                dict(item)
                for item in context.get("evidence_trace", [])[:10]
                if isinstance(item, dict)
            ],
            *([{
                "source_type": "conversation",
                "source_id": max(
                    0,
                    int(context.get("source_counts", {}).get("conversation_messages", 1) or 1) - 1,
                ),
                "label": "Current user message",
                "text": question,
            }] if question and (decision_values or options) else []),
        ],
        "source_index": dict(
            context.get("source_index", {})
        ),
        "source_counts": {
            "memories": int(source_counts.get("memories", 0) or 0),
            "entities": int(source_counts.get("entities", 0) or 0),
            "relationships": int(source_counts.get("relationships", 0) or 0),
            "conversation_messages": int(source_counts.get("conversation_messages", 0) or 0),
            "evidence_sources": int(source_counts.get("evidence_sources", 0) or 0),
        },
        "missing_information": missing_information[:20],
    }


def build_decision_context_trace(
    decision_context,
):
    """Return a compact public verification trace for Step 4A."""

    context = (
        decision_context
        if isinstance(decision_context, dict)
        else {}
    )

    source_counts = context.get(
        "source_counts",
        {}
    )

    return {
        "built": bool(context),
        "intent": str(
            context.get("intent") or "general"
        ),
        "decision_present": bool(
            context.get("decision")
        ),
        "option_count": len(
            context.get("options", [])
            if isinstance(context.get("options", []), list)
            else []
        ),
        "goal_count": len(
            context.get("goals", [])
            if isinstance(context.get("goals", []), list)
            else []
        ),
        "constraint_count": len(
            context.get("constraints", [])
            if isinstance(context.get("constraints", []), list)
            else []
        ),
        "risk_count": len(
            context.get("risks", [])
            if isinstance(context.get("risks", []), list)
            else []
        ),
        "uncertainty_count": len(
            context.get("uncertainties", [])
            if isinstance(context.get("uncertainties", []), list)
            else []
        ),
        "tradeoff_count": len(
            context.get("tradeoffs", [])
            if isinstance(context.get("tradeoffs", []), list)
            else []
        ),
        "missing_information_count": len(
            context.get("missing_information", [])
            if isinstance(context.get("missing_information", []), list)
            else []
        ),
        "evidence_count": len(
            context.get("evidence_trace", [])
            if isinstance(context.get("evidence_trace", []), list)
            else []
        ),
        "source_counts": {
            "memories": int(source_counts.get("memories", 0) or 0),
            "entities": int(source_counts.get("entities", 0) or 0),
            "relationships": int(source_counts.get("relationships", 0) or 0),
            "conversation_messages": int(source_counts.get("conversation_messages", 0) or 0),
            "evidence_sources": int(source_counts.get("evidence_sources", 0) or 0),
        },
    }


# ============================================================
# PHASE 7 — STEP 3B
# REASONING ENGINE
# ============================================================

def validate_reasoned_answer(result):
    """
    Deterministically validate the public result of the reasoning model.

    The reasoning model may synthesize across the already-built context,
    but it may not create new evidence records or source IDs here.
    """

    if not isinstance(result, dict):
        return {
            "answer": "",
            "reasoning_used": False,
        }

    answer = str(
        result.get("answer") or ""
    ).strip()

    if not answer:
        return {
            "answer": "",
            "reasoning_used": False,
        }

    return {
        "answer": answer,
        "reasoning_used": True,
    }


def generate_reasoned_answer(
    reasoning_context,
    fallback_answer="",
):
    """
    Reason over the deterministic Step 3A context package.

    This is the first reasoning layer of Dusra Brain. It does not query
    the database, mutate memory, alter Recall Intelligence, or create
    evidence references. Evidence remains the already-validated Step 1A
    trace contained inside reasoning_context.
    """

    if not isinstance(reasoning_context, dict):
        return {
            "answer": str(fallback_answer or "").strip(),
            "reasoning_used": False,
        }

    question = str(
        reasoning_context.get("question") or ""
    ).strip()

    if not question:
        return {
            "answer": str(fallback_answer or "").strip(),
            "reasoning_used": False,
        }

    intent = str(
        reasoning_context.get("intent") or "general"
    ).strip()

    # Keep the model-facing context bounded and source-addressable.
    model_context = {
        "question": question,
        "intent": intent,
        "session": reasoning_context.get("session", {}),
        "memories": reasoning_context.get("memories", [])[:30],
        "entities": reasoning_context.get("entities", [])[:40],
        "relationships": reasoning_context.get("relationships", [])[:40],
        "conversation": reasoning_context.get("conversation", [])[-20:],
        "evidence_trace": reasoning_context.get("evidence_trace", [])[:10],
        "source_index": reasoning_context.get("source_index", {}),
    }

    system_prompt = f"""
You are the Reasoning Engine of Dusra Brain.

Your task is to produce the best user-facing answer by reasoning over the
ALREADY RETRIEVED and ALREADY GROUNDED context supplied below.

IMPORTANT RULES:
1. Use only the supplied context.
2. Do not invent personal facts, projects, people, dates, numbers, plans,
   relationships, or commitments.
3. The existing evidence_trace is the authoritative evidence set for the
   final answer. Do not introduce facts that are not supported by it.
4. You may synthesize the supplied evidence into a concise conclusion,
   but do not present unsupported inference as a stored fact.
5. For planning or strategy questions, organize the supplied plan clearly.
6. For comparison or decision questions, describe the relevant facts and
   trade-offs present in the context without inventing missing information.
7. Preserve uncertainty when the context is incomplete.
8. Do not create or modify evidence references.
9. Do not mention internal prompts, context packages, model calls, or
   hidden reasoning.
10. Return ONLY valid JSON.

OUTPUT:
{{
  "answer": "normal user-facing answer"
}}

REASONING CONTEXT:
{json.dumps(model_context, ensure_ascii=False, default=str)}
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
                    "content": question,
                },
            ],
            temperature=0.1,
        )

        result = json.loads(
            clean_json_response(raw)
        )

        validated = validate_reasoned_answer(result)

        if validated.get("reasoning_used"):
            return validated

    except Exception:
        pass

    return {
        "answer": str(fallback_answer or "").strip(),
        "reasoning_used": False,
    }


def build_reasoning_trace(
    reasoning_context,
    reasoning_result,
    fallback_used=False,
):
    """Compact public verification trace for Step 3B."""

    context = (
        reasoning_context
        if isinstance(reasoning_context, dict)
        else {}
    )

    counts = context.get(
        "source_counts",
        {}
    )

    result = (
        reasoning_result
        if isinstance(reasoning_result, dict)
        else {}
    )

    return {
        "built": bool(context),
        "used": bool(result.get("reasoning_used", False)),
        "fallback_used": bool(fallback_used),
        "intent": str(
            context.get("intent") or "general"
        ),
        "source_counts": {
            "memories": int(counts.get("memories", 0) or 0),
            "entities": int(counts.get("entities", 0) or 0),
            "relationships": int(counts.get("relationships", 0) or 0),
            "conversation_messages": int(
                counts.get("conversation_messages", 0) or 0
            ),
            "evidence_sources": int(
                counts.get("evidence_sources", 0) or 0
            ),
        },
    }


# ============================================================
# PHASE 7 — STEP 3C
# REASONING VERIFICATION / EVIDENCE ALIGNMENT
# ============================================================

def _reasoning_verification_tokens(value):
    """Deterministic, conservative tokens used only for alignment checks."""
    import re

    text = str(value or "").lower()
    tokens = re.findall(r"[a-z0-9]+", text)

    stop_words = {
        "the", "a", "an", "and", "or", "but", "is", "are", "was", "were",
        "be", "been", "being", "to", "of", "in", "on", "for", "from", "with",
        "as", "at", "by", "it", "this", "that", "these", "those", "my", "your",
        "i", "we", "you", "they", "he", "she", "its", "their", "our", "has", "have",
        "had", "do", "does", "did", "will", "would", "can", "could", "should", "may",
        "might", "about", "into", "than", "then", "also", "be", "there", "here",
    }

    return {
        token for token in tokens
        if len(token) >= 4 and token not in stop_words
    }


def _reasoning_source_key(source_type, source_id):
    """Normalize a source reference into one deterministic key."""
    try:
        source_id = int(source_id)
    except Exception:
        return None

    allowed = {"memory", "entity", "relationship", "conversation"}
    source_type = str(source_type or "").strip().lower()

    if source_type not in allowed:
        return None

    return (source_type, source_id)


def _reasoning_context_source_keys(reasoning_context):
    """Build the authoritative source index from Step 3A context."""
    context = reasoning_context if isinstance(reasoning_context, dict) else {}
    index = context.get("source_index", {})

    keys = set()

    for value in index.get("memory_ids", []) or []:
        key = _reasoning_source_key("memory", value)
        if key:
            keys.add(key)

    for value in index.get("entity_ids", []) or []:
        key = _reasoning_source_key("entity", value)
        if key:
            keys.add(key)

    for value in index.get("relationship_ids", []) or []:
        key = _reasoning_source_key("relationship", value)
        if key:
            keys.add(key)

    for value in index.get("conversation_indexes", []) or []:
        key = _reasoning_source_key("conversation", value)
        if key:
            keys.add(key)

    return keys


def _reasoning_evidence_text(reasoning_context, evidence_trace):
    """Return text from the already-validated evidence sources only."""
    context = reasoning_context if isinstance(reasoning_context, dict) else {}
    evidence = evidence_trace if isinstance(evidence_trace, list) else []

    memory_by_id = {
        int(item.get("id")): item
        for item in context.get("memories", [])
        if isinstance(item, dict) and item.get("id") is not None
    }

    entity_by_id = {
        int(item.get("id")): item
        for item in context.get("entities", [])
        if isinstance(item, dict) and item.get("id") is not None
    }

    relationship_by_id = {
        int(item.get("id")): item
        for item in context.get("relationships", [])
        if isinstance(item, dict) and item.get("id") is not None
    }

    conversation = context.get("conversation", [])
    text_parts = []

    for item in evidence:
        if not isinstance(item, dict):
            continue

        source_type = str(item.get("source_type") or "").strip().lower()
        try:
            source_id = int(item.get("source_id"))
        except Exception:
            continue

        source = None
        if source_type == "memory":
            source = memory_by_id.get(source_id)
            if source:
                text_parts.append(str(source.get("memory") or ""))
        elif source_type == "entity":
            source = entity_by_id.get(source_id)
            if source:
                text_parts.append(" ".join([
                    str(source.get("name") or ""),
                    str(source.get("description") or ""),
                    str(source.get("entity_type") or ""),
                ]))
        elif source_type == "relationship":
            source = relationship_by_id.get(source_id)
            if source:
                text_parts.append(" ".join([
                    str(source.get("from") or ""),
                    str(source.get("relationship") or ""),
                    str(source.get("to") or ""),
                ]))
        elif source_type == "conversation":
            if 0 <= source_id < len(conversation):
                source = conversation[source_id]
                if isinstance(source, dict):
                    text_parts.append(" ".join([
                        str(source.get("role") or ""),
                        str(source.get("message") or ""),
                    ]))

    return " ".join(text_parts).strip()


def verify_reasoning_evidence_alignment(
    reasoning_context,
    evidence_trace,
    reasoned_answer,
    fallback_answer="",
):
    """
    Deterministically verify that a reasoned answer remains anchored to the
    authoritative Step 1A evidence set.

    This layer does not generate facts, create evidence, call the database,
    or call the model. If alignment cannot be established conservatively,
    the caller should use the already-grounded fallback answer.
    """
    context = reasoning_context if isinstance(reasoning_context, dict) else {}
    evidence = evidence_trace if isinstance(evidence_trace, list) else []
    answer = str(reasoned_answer or "").strip()
    fallback = str(fallback_answer or "").strip()

    authoritative_keys = _reasoning_context_source_keys(context)
    valid_evidence = []
    invalid_evidence = []
    seen = set()

    for item in evidence:
        if not isinstance(item, dict):
            invalid_evidence.append(item)
            continue

        key = _reasoning_source_key(
            item.get("source_type"),
            item.get("source_id")
        )

        if key is None or key not in authoritative_keys:
            invalid_evidence.append(item)
            continue

        if key in seen:
            continue

        seen.add(key)
        valid_evidence.append(item)

    evidence_text = _reasoning_evidence_text(
        context,
        valid_evidence
    )

    answer_tokens = _reasoning_verification_tokens(answer)
    evidence_tokens = _reasoning_verification_tokens(evidence_text)
    overlap = answer_tokens.intersection(evidence_tokens)

    # Conservative rule: a non-empty reasoned answer must have either
    # authoritative evidence references and meaningful lexical alignment,
    # or fall back to the already-grounded answer. Short answers are allowed
    # a smaller overlap because the evidence itself may be concise.
    if not answer:
        verified = False
        reason = "empty_reasoned_answer"
    elif not valid_evidence:
        verified = False
        reason = "no_valid_authoritative_evidence"
    else:
        required_overlap = 1 if len(answer_tokens) < 8 else 2
        verified = len(overlap) >= required_overlap
        reason = "aligned" if verified else "insufficient_evidence_alignment"

    selected_answer = answer if verified else fallback

    return {
        "verified": bool(verified),
        "reason": reason,
        "fallback_used": not bool(verified),
        "valid_evidence_count": len(valid_evidence),
        "invalid_evidence_count": len(invalid_evidence),
        "alignment_token_count": len(overlap),
        "answer_token_count": len(answer_tokens),
        "authoritative_evidence_tokens": len(evidence_tokens),
        "selected_answer": selected_answer,
    }


def build_reasoning_verification_trace(
    reasoning_context,
    verification_result,
):
    """Compact public verification trace for Step 3C."""
    context = reasoning_context if isinstance(reasoning_context, dict) else {}
    result = verification_result if isinstance(verification_result, dict) else {}
    counts = context.get("source_counts", {})

    return {
        "built": bool(context),
        "verified": bool(result.get("verified", False)),
        "fallback_used": bool(result.get("fallback_used", False)),
        "reason": str(result.get("reason") or "unknown"),
        "valid_evidence_count": int(result.get("valid_evidence_count", 0) or 0),
        "invalid_evidence_count": int(result.get("invalid_evidence_count", 0) or 0),
        "alignment_token_count": int(result.get("alignment_token_count", 0) or 0),
        "source_counts": {
            "memories": int(counts.get("memories", 0) or 0),
            "entities": int(counts.get("entities", 0) or 0),
            "relationships": int(counts.get("relationships", 0) or 0),
            "conversation_messages": int(counts.get("conversation_messages", 0) or 0),
            "evidence_sources": int(counts.get("evidence_sources", 0) or 0),
        },
    }


# ============================================================
# PHASE 7 — STEP 3D
# REASONING QUALITY GATE
# ============================================================

def evaluate_reasoning_quality_gate(
    reasoning_context,
    reasoning_trace,
    reasoning_verification_trace,
    evidence_trace,
    response,
):
    """
    Deterministically gate the final answer after reasoning verification.

    This layer does not call the model, query the database, create evidence,
    or change Recall Intelligence. It checks that the final response has a
    usable grounded basis and that the preceding reasoning/verification
    layers completed coherently.
    """
    context = reasoning_context if isinstance(reasoning_context, dict) else {}
    rtrace = reasoning_trace if isinstance(reasoning_trace, dict) else {}
    vtrace = (
        reasoning_verification_trace
        if isinstance(reasoning_verification_trace, dict)
        else {}
    )
    evidence = evidence_trace if isinstance(evidence_trace, list) else []
    answer = str(response or "").strip()

    counts = context.get("source_counts", {})
    evidence_sources = int(counts.get("evidence_sources", 0) or 0)
    valid_evidence = int(vtrace.get("valid_evidence_count", 0) or 0)
    invalid_evidence = int(vtrace.get("invalid_evidence_count", 0) or 0)
    verified = bool(vtrace.get("verified", False))
    verification_fallback = bool(vtrace.get("fallback_used", False))
    reasoning_built = bool(rtrace.get("built", False))
    reasoning_used = bool(rtrace.get("used", False))

    checks = {
        "answer_present": bool(answer),
        "reasoning_context_built": bool(context),
        "reasoning_trace_built": reasoning_built,
        "evidence_available": bool(evidence) and evidence_sources > 0,
        "evidence_authoritative": valid_evidence > 0 and invalid_evidence == 0,
        "reasoning_verified_or_grounded_fallback": verified or verification_fallback,
    }

    passed = all(checks.values())

    if not answer:
        status = "fail"
        reason = "empty_response"
    elif not checks["evidence_available"]:
        status = "fail"
        reason = "no_grounding_evidence"
    elif not checks["evidence_authoritative"]:
        status = "fail"
        reason = "invalid_or_missing_authoritative_evidence"
    elif verified and reasoning_used:
        status = "pass"
        reason = "reasoned_answer_verified"
    elif verification_fallback and valid_evidence > 0:
        status = "pass"
        reason = "grounded_fallback_verified"
    else:
        status = "fail"
        reason = "reasoning_quality_checks_failed"

    return {
        "passed": bool(passed),
        "status": status,
        "reason": reason,
        "checks": checks,
        "evidence_count": len(evidence),
        "valid_evidence_count": valid_evidence,
        "invalid_evidence_count": invalid_evidence,
        "reasoning_used": reasoning_used,
        "verification_fallback_used": verification_fallback,
    }


def build_reasoning_quality_trace(
    reasoning_context,
    quality_result,
):
    """Compact public verification trace for Step 3D."""
    context = reasoning_context if isinstance(reasoning_context, dict) else {}
    result = quality_result if isinstance(quality_result, dict) else {}

    return {
        "built": bool(context),
        "passed": bool(result.get("passed", False)),
        "status": str(result.get("status") or "fail"),
        "reason": str(result.get("reason") or "unknown"),
        "evidence_count": int(result.get("evidence_count", 0) or 0),
        "valid_evidence_count": int(result.get("valid_evidence_count", 0) or 0),
        "invalid_evidence_count": int(result.get("invalid_evidence_count", 0) or 0),
        "reasoning_used": bool(result.get("reasoning_used", False)),
        "verification_fallback_used": bool(
            result.get("verification_fallback_used", False)
        ),
    }




# ============================================================
# PHASE 7 — STEP 4C
# DECISION EVIDENCE MATRIX
# ============================================================

def build_decision_evidence_matrix(
    decision_context,
    interpretation,
):
    """Build a deterministic, read-only decision evidence matrix."""
    context = decision_context if isinstance(decision_context, dict) else {}
    interpreted = interpretation if isinstance(interpretation, dict) else {}

    evidence = context.get("evidence_trace", [])
    evidence = evidence if isinstance(evidence, list) else []

    evidence_items = []
    seen = set()
    for item in evidence[:10]:
        if not isinstance(item, dict):
            continue
        source_type = str(item.get("source_type") or "").strip()
        source_id = item.get("source_id")
        text_value = str(item.get("text") or "").strip()
        key = (source_type, str(source_id), text_value)
        if key in seen:
            continue
        seen.add(key)
        evidence_items.append({
            "source_type": source_type,
            "source_id": source_id,
            "label": str(item.get("label") or "").strip(),
            "text": text_value,
        })

    # Decision readiness is based on the fields that are genuinely required
    # to discuss a choice. Goals/constraints/risks/trade-offs improve the
    # analysis but are not mandatory unless the user explicitly says they are.
    fields = [
        "decision",
        "options",
        "goals",
        "constraints",
        "risks",
        "uncertainties",
        "tradeoffs",
    ]

    field_status = {}
    supported_field_count = 0
    for field in fields:
        value = context.get(field, [])
        values = value if isinstance(value, list) else []
        present = bool(values)
        field_status[field] = {
            "present": present,
            "count": len(values),
        }
        if present:
            supported_field_count += 1

    missing = context.get("missing_information", [])
    missing = missing if isinstance(missing, list) else []
    missing_items = [
        str(item).strip()
        for item in missing[:20]
        if str(item or "").strip()
    ]

    total_fields = len(fields)
    decision_present = bool(context.get("decision"))
    options_value = context.get("options", [])
    option_count = len(options_value) if isinstance(options_value, list) else 0
    evidence_source_count = len(evidence_items)

    # Coverage measures the required decision fields, not every optional
    # analytical dimension. This prevents a simple A-vs-B decision from being
    # permanently blocked because the user did not state seven separate fields.
    required_present = int(decision_present) + int(option_count > 0)
    required_total = 2
    evidence_coverage = round(required_present / required_total, 3)

    decision_ready = bool(
        decision_present
        and option_count > 0
        and evidence_source_count > 0
        and not missing_items
    )

    return {
        "question": str(context.get("question") or "").strip(),
        "context_type": str(interpreted.get("context_type") or "informational"),
        "supporting_evidence": evidence_items,
        "evidence_source_count": evidence_source_count,
        "field_status": field_status,
        "supported_field_count": supported_field_count,
        "total_decision_fields": total_fields,
        "evidence_coverage": evidence_coverage,
        "known_items": evidence_items,
        "unknown_items": missing_items,
        "missing_information": missing_items,
        "decision_readiness": decision_ready,
        "readiness_reason": (
            "sufficient_explicit_decision_evidence"
            if decision_ready
            else "decision_context_or_evidence_incomplete"
        ),
    }


def build_decision_evidence_matrix_trace(
    matrix,
):
    """Return a compact public verification trace for Step 4C."""
    value = matrix if isinstance(matrix, dict) else {}
    field_status = value.get("field_status", {})
    field_status = field_status if isinstance(field_status, dict) else {}
    known = value.get("known_items", [])
    unknown = value.get("unknown_items", [])

    return {
        "built": bool(value),
        "context_type": str(value.get("context_type") or "informational"),
        "evidence_source_count": int(value.get("evidence_source_count", 0) or 0),
        "supported_field_count": int(value.get("supported_field_count", 0) or 0),
        "total_decision_fields": int(value.get("total_decision_fields", 0) or 0),
        "evidence_coverage": float(value.get("evidence_coverage", 0.0) or 0.0),
        "known_item_count": len(known) if isinstance(known, list) else 0,
        "unknown_item_count": len(unknown) if isinstance(unknown, list) else 0,
        "decision_ready": bool(value.get("decision_readiness", False)),
        "readiness_reason": str(value.get("readiness_reason") or "unknown"),
        "field_presence": {
            key: bool(item.get("present", False))
            for key, item in field_status.items()
            if isinstance(item, dict)
        },
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

            if action == "get_decision_history":

                history = get_decision_history(
                    user_id=user_id,
                    limit=body.get("limit", 50),
                )

                send_json(
                    self,
                    {
                        "history": history,
                        "count": len(history),
                    },
                    200
                )

                return

            if action == "record_decision_outcome":

                outcome_result = persist_decision_outcome(
                    user_id=user_id,
                    payload=body,
                )

                send_json(
                    self,
                    {
                        "decision_outcome_trace":
                            build_decision_outcome_trace(outcome_result),
                        "outcome":
                            outcome_result,
                    },
                    200
                    if outcome_result.get("accepted")
                    and outcome_result.get("persisted")
                    else 400,
                )

                return


            if action == "get_decision_outcomes":

                decision_id = body.get("decision_id")

                try:
                    decision_id = (
                        int(decision_id)
                        if decision_id is not None
                        else None
                    )
                except Exception:
                    decision_id = None

                outcomes = get_decision_outcomes(
                    user_id=user_id,
                    decision_id=decision_id,
                    limit=body.get("limit", 100),
                )

                send_json(
                    self,
                    {
                        "outcomes": outcomes,
                        "count": len(outcomes),
                        "decision_outcome_history_trace":
                            build_decision_outcome_history_trace(outcomes),
                    },
                    200,
                )

                return


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
            # PHASE 7 — STEP 3A
            # REASONING CONTEXT BUILDER
            # ------------------------------------------------

            reasoning_context = build_reasoning_context(
                message=message,
                session_id=session_id,
                title=title,
                memories=memories,
                brain_entities=brain_entities,
                brain_relationships=brain_relationships,
                history=history,
                recall_trace=recall_trace,
                evidence_trace=evidence_trace,
            )

            reasoning_context_trace = (
                build_reasoning_context_trace(
                    reasoning_context
                )
            )


            # ------------------------------------------------
            # PHASE 7 — STEP 3B
            # REASONING ENGINE
            # ------------------------------------------------

            reasoning_result = generate_reasoned_answer(
                reasoning_context=reasoning_context,
                fallback_answer=response,
            )

            reasoned_response = str(
                reasoning_result.get(
                    "answer",
                    ""
                )
            ).strip()

            fallback_used = not bool(
                reasoning_result.get(
                    "reasoning_used",
                    False
                )
            )

            if reasoned_response:
                response = reasoned_response

            reasoning_trace = build_reasoning_trace(
                reasoning_context=reasoning_context,
                reasoning_result=reasoning_result,
                fallback_used=fallback_used,
            )


            # ------------------------------------------------
            # PHASE 7 — STEP 3C
            # REASONING VERIFICATION / EVIDENCE ALIGNMENT
            # ------------------------------------------------

            reasoning_verification = verify_reasoning_evidence_alignment(
                reasoning_context=reasoning_context,
                evidence_trace=evidence_trace,
                reasoned_answer=response,
                fallback_answer=grounded_result.get(
                    "answer",
                    ""
                ).strip(),
            )

            response = reasoning_verification.get(
                "selected_answer",
                response
            ).strip()

            reasoning_verification_trace = (
                build_reasoning_verification_trace(
                    reasoning_context=reasoning_context,
                    verification_result=reasoning_verification,
                )
            )


            # ------------------------------------------------
            # PHASE 7 — STEP 3D
            # REASONING QUALITY GATE
            # ------------------------------------------------

            reasoning_quality = evaluate_reasoning_quality_gate(
                reasoning_context=reasoning_context,
                reasoning_trace=reasoning_trace,
                reasoning_verification_trace=reasoning_verification_trace,
                evidence_trace=evidence_trace,
                response=response,
            )

            reasoning_quality_trace = build_reasoning_quality_trace(
                reasoning_context=reasoning_context,
                quality_result=reasoning_quality,
            )


            # ------------------------------------------------
            # PHASE 7 — DECISION INPUT EXTRACTION
            # ------------------------------------------------

            decision_extracted_context = extract_decision_context_from_text(
                message=message,
                history=history,
            )

            # ------------------------------------------------
            # PHASE 7 — STEP 4A
            # DECISION CONTEXT BUILDER
            # ------------------------------------------------

            decision_context = build_decision_context(
                reasoning_context=reasoning_context,
                extracted_context=decision_extracted_context,
            )

            decision_context_trace = build_decision_context_trace(
                decision_context=decision_context,
            )


            # ------------------------------------------------
            # PHASE 7 — STEP 4B
            # DECISION CONTEXT INTERPRETER
            # ------------------------------------------------

            decision_context_interpretation = interpret_decision_context(
                decision_context=decision_context,
            )

            decision_context_interpretation_trace = (
                build_decision_context_interpretation_trace(
                    decision_context=decision_context,
                    interpretation=decision_context_interpretation,
                )
            )


            # ------------------------------------------------
            # PHASE 7 — STEP 4C
            # DECISION EVIDENCE MATRIX
            # ------------------------------------------------

            decision_evidence_matrix = build_decision_evidence_matrix(
                decision_context=decision_context,
                interpretation=decision_context_interpretation,
            )

            decision_evidence_matrix_trace = build_decision_evidence_matrix_trace(
                matrix=decision_evidence_matrix,
            )


            # ------------------------------------------------
            # PHASE 7 — STEP 4D
            # DECISION READINESS GATE
            # ------------------------------------------------

            decision_readiness = evaluate_decision_readiness(
                decision_evidence_matrix=decision_evidence_matrix,
            )

            decision_readiness_trace = build_decision_readiness_trace(
                decision_evidence_matrix=decision_evidence_matrix,
                readiness_result=decision_readiness,
            )


            # ------------------------------------------------
            # PHASE 7 — STEP 4E
            # DECISION ANALYSIS ENGINE
            # ------------------------------------------------

            decision_analysis = generate_decision_analysis(
                decision_context=decision_context,
                decision_evidence_matrix=decision_evidence_matrix,
                decision_readiness=decision_readiness,
            )

            decision_analysis_trace = build_decision_analysis_trace(
                decision_readiness=decision_readiness,
                analysis_result=decision_analysis,
            )


            # ------------------------------------------------
            # PHASE 7 — STEP 4F
            # DECISION SYNTHESIS ENGINE
            # ------------------------------------------------

            decision_synthesis = generate_decision_synthesis(
                decision_context=decision_context,
                decision_readiness=decision_readiness,
                decision_analysis=decision_analysis,
            )

            decision_synthesis_trace = build_decision_synthesis_trace(
                decision_readiness=decision_readiness,
                analysis_result=decision_analysis,
                synthesis_result=decision_synthesis,
            )


            # ------------------------------------------------
            # PHASE 7 — STEP 4G
            # DECISION SYNTHESIS QUALITY GATE
            # ------------------------------------------------

            decision_synthesis_quality = evaluate_decision_synthesis_quality_gate(
                decision_readiness=decision_readiness,
                decision_analysis=decision_analysis,
                decision_synthesis=decision_synthesis,
            )

            decision_synthesis_quality_trace = build_decision_synthesis_quality_trace(
                decision_readiness=decision_readiness,
                decision_analysis=decision_analysis,
                decision_synthesis=decision_synthesis,
                quality_result=decision_synthesis_quality,
            )


            # ------------------------------------------------
            # PHASE 7 — STEP 4H
            # DECISION CAPTURE & ACTION GATE
            # ------------------------------------------------

            decision_capture = validate_decision_capture(
                decision_readiness=decision_readiness,
                decision_synthesis_quality=decision_synthesis_quality,
                decision_synthesis=decision_synthesis,
            )

            decision_capture_trace = build_decision_capture_trace(
                decision_readiness=decision_readiness,
                decision_synthesis_quality=decision_synthesis_quality,
                decision_synthesis=decision_synthesis,
                capture_result=decision_capture,
            )



            # ------------------------------------------------
            # PHASE 7 — STEP 4I
            # EXPLICIT USER DECISION INPUT INTERFACE
            # ------------------------------------------------
            #
            # Step 4I receives only an explicit decision payload supplied
            # by the user/client. It does not infer a decision from the
            # question, choose an option, create an action, or write memory.
            #
            # The 4H gate remains authoritative. If 4H is not capturable,
            # explicit input is safely blocked.
            # ------------------------------------------------

            decision_input = validate_explicit_decision_input(
                decision_capture=decision_capture,
                decision_payload=body.get(
                    "decision_input",
                    {}
                ),
            )

            decision_input_trace = build_explicit_decision_input_trace(
                decision_capture=decision_capture,
                input_result=decision_input,
            )

            # ------------------------------------------------
            # PHASE 7 — STEP 4J
            # DECISION PERSISTENCE & DECISION HISTORY
            # ------------------------------------------------

            decision_persistence = persist_explicit_decision(
                user_id=user_id,
                session_id=session_id,
                title=title,
                decision_input=decision_input,
                decision_payload=body.get(
                    "decision_input",
                    {}
                ),
                evidence_trace=evidence_trace,
                decision_capture=decision_capture,
                decision_synthesis_quality=decision_synthesis_quality,
            )

            decision_history_trace = build_decision_history_trace(
                persistence_result=decision_persistence,
            )


            # ------------------------------------------------
            # PHASE 7 — STEP 4K
            # DECISION HISTORY RECALL & RETRIEVAL
            # ------------------------------------------------

            decision_history_recall = recall_decision_history(
                user_id=user_id,
                query=message,
                limit=body.get("decision_history_limit", 10),
            )

            decision_history_recall_trace = build_decision_history_recall_trace(
                recall_result=decision_history_recall,
            )


            # ------------------------------------------------
            # PHASE 7 — STEP 4L
            # DECISION HISTORY GROUNDED ANSWER / RECALL
            # ------------------------------------------------

            decision_history_answer = build_decision_history_grounded_answer(
                recall_result=decision_history_recall,
            )

            decision_history_answer_verification = (
                validate_decision_history_grounded_answer(
                    answer_result=decision_history_answer,
                    recall_result=decision_history_recall,
                )
            )

            decision_history_answer_trace = (
                build_decision_history_answer_trace(
                    answer_result=decision_history_answer,
                    verification_result=decision_history_answer_verification,
                )
            )

            if decision_history_answer_verification.get("verified") and decision_history_answer.get("answered"):
                response = decision_history_answer_verification.get("answer", response).strip()


            # ------------------------------------------------
            # PHASE 7 — STEP 4M
            # DECISION HISTORY ↔ MEMORY / EVIDENCE INTEGRATION
            # ------------------------------------------------

            decision_history_memory_evidence = build_decision_history_memory_evidence_bridge(
                recall_result=decision_history_recall,
                reasoning_context=reasoning_context,
            )

            decision_history_memory_evidence_trace = (
                build_decision_history_memory_evidence_trace(
                    bridge_result=decision_history_memory_evidence,
                )
            )


            # ------------------------------------------------
            # PHASE 7 — STEP 4N
            # DECISION ↔ CURRENT PLAN CONFLICT DETECTION
            # ------------------------------------------------

            decision_current_plan_conflict = detect_decision_current_plan_conflicts(
                recall_result=decision_history_recall,
                reasoning_context=reasoning_context,
            )

            decision_current_plan_conflict_trace = (
                build_decision_current_plan_conflict_trace(
                    conflict_result=decision_current_plan_conflict,
                )
            )


            # ------------------------------------------------
            # PHASE 7 — STEP 4O
            # DECISION CHANGE DETECTION & EVOLUTION TRACKING
            # ------------------------------------------------

            decision_change_evolution = detect_decision_change_evolution(
                recall_result=decision_history_recall,
            )

            decision_change_evolution_trace = (
                build_decision_change_evolution_trace(
                    evolution_result=decision_change_evolution,
                )
            )


            # ------------------------------------------------
            # PHASE 7 — STEP 4P
            # DECISION EVOLUTION GROUNDED CHANGE EXPLANATION
            # ------------------------------------------------

            decision_evolution_answer = (
                build_decision_evolution_grounded_answer(
                    evolution_result=decision_change_evolution,
                )
            )

            decision_evolution_answer_verification = (
                validate_decision_evolution_grounded_answer(
                    answer_result=decision_evolution_answer,
                    evolution_result=decision_change_evolution,
                )
            )

            decision_evolution_answer_trace = (
                build_decision_evolution_answer_trace(
                    answer_result=decision_evolution_answer,
                    verification_result=decision_evolution_answer_verification,
                )
            )

            if (
                decision_evolution_answer_verification.get("verified")
                and decision_evolution_answer.get("answered")
            ):
                response = (
                    decision_evolution_answer_verification
                    .get("answer", response)
                    .strip()
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

                    "reasoning_context_trace":
                        reasoning_context_trace,

                    "reasoning_trace":
                        reasoning_trace,

                    "reasoning_verification_trace":
                        reasoning_verification_trace,

                    "reasoning_quality_trace":
                        reasoning_quality_trace,

                    "decision_context_trace":
                        decision_context_trace,

                    "decision_extracted_context":
                        decision_extracted_context,

                    "decision_context_interpretation_trace":
                        decision_context_interpretation_trace,

                    "decision_evidence_matrix_trace":
                        decision_evidence_matrix_trace,

                    "decision_readiness_trace":
                        decision_readiness_trace,

                    "decision_analysis_trace":
                        decision_analysis_trace,

                    "decision_synthesis_trace":
                        decision_synthesis_trace,

                    "decision_synthesis_quality_trace":
                        decision_synthesis_quality_trace,

                    "decision_capture_trace":
                        decision_capture_trace,

                    "decision_input_trace":
                        decision_input_trace,

                    "decision_history_trace":
                        decision_history_trace,

                    "decision_history_recall_trace":
                        decision_history_recall_trace,

                    "decision_history_answer_trace":
                        decision_history_answer_trace,

                    "decision_history_memory_evidence_trace":
                        decision_history_memory_evidence_trace,

                    "decision_current_plan_conflict_trace":
                        decision_current_plan_conflict_trace,

                    "decision_change_evolution_trace":
                        decision_change_evolution_trace,

                    "decision_evolution_answer_trace":
                        decision_evolution_answer_trace,

                    "decision_outcome_trace":
                        {
                            "built": True,
                            "accepted": False,
                            "persisted": False,
                            "status": "not_triggered",
                            "reason":
                                "outcome_requires_explicit_user_capture",
                            "decision_id": None,
                            "outcome_id": None,
                            "duplicate": False,
                            "outcome_recorded": False,
                            "recommendation_generated": False,
                            "decision_modified": False,
                            "action_created": False,
                            "read_only": True,
                        },

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



# ============================================================
# PHASE 7 — STEP 4D
# DECISION READINESS GATE
# ============================================================

def evaluate_decision_readiness(decision_evidence_matrix):
    """Deterministically gate whether the supplied decision context is ready.

    This layer does not call the model, query the database, write memory,
    create evidence, or recommend a decision. It evaluates only the
    already-built Step 4C matrix.
    """
    matrix = decision_evidence_matrix if isinstance(decision_evidence_matrix, dict) else {}

    field_status = matrix.get("field_status", {})
    decision_present = bool(field_status.get("decision", {}).get("present"))
    options_present = bool(field_status.get("options", {}).get("present"))
    evidence_source_count = int(matrix.get("evidence_source_count", 0) or 0)
    missing_information = matrix.get("missing_information", [])
    missing_information = missing_information if isinstance(missing_information, list) else []
    evidence_coverage = float(matrix.get("evidence_coverage", 0.0) or 0.0)

    checks = {
        "decision_present": decision_present,
        "options_present": options_present,
        "evidence_available": evidence_source_count > 0,
        "no_blocking_missing_information": len(missing_information) == 0,
        "required_decision_fields_complete": decision_present and options_present,
    }

    ready = all(checks.values())

    if ready:
        status = "ready"
        reason = "sufficient_explicit_decision_evidence"
    elif not checks["decision_present"]:
        status = "not_ready"
        reason = "decision_missing"
    elif not checks["options_present"]:
        status = "not_ready"
        reason = "options_missing"
    elif not checks["evidence_available"]:
        status = "not_ready"
        reason = "evidence_missing"
    elif not checks["no_blocking_missing_information"]:
        status = "not_ready"
        reason = "blocking_missing_information"
    elif not checks["required_decision_fields_complete"]:
        status = "not_ready"
        reason = "required_decision_fields_incomplete"
    else:
        status = "not_ready"
        reason = "decision_readiness_checks_failed"

    missing_requirements = [
        key for key, passed in checks.items()
        if not passed
    ]

    return {
        "ready": bool(ready),
        "status": status,
        "reason": reason,
        "checks": checks,
        "missing_requirements": missing_requirements,
        "evidence_coverage": evidence_coverage,
        "evidence_source_count": evidence_source_count,
    }


def build_decision_readiness_trace(decision_evidence_matrix, readiness_result):
    """Compact public verification trace for Step 4D."""
    matrix = decision_evidence_matrix if isinstance(decision_evidence_matrix, dict) else {}
    result = readiness_result if isinstance(readiness_result, dict) else {}

    return {
        "built": bool(matrix),
        "ready": bool(result.get("ready", False)),
        "status": str(result.get("status") or "not_ready"),
        "reason": str(result.get("reason") or "unknown"),
        "missing_requirements": list(result.get("missing_requirements", []) or []),
        "evidence_coverage": float(result.get("evidence_coverage", 0.0) or 0.0),
        "evidence_source_count": int(result.get("evidence_source_count", 0) or 0),
    }



# ============================================================
# PHASE 7 — STEP 4E
# DECISION ANALYSIS ENGINE
# ============================================================

def validate_decision_analysis_result(result, decision_context, decision_evidence_matrix):
    """Validate model-produced analysis against supplied decision context."""
    value = result if isinstance(result, dict) else {}
    context = decision_context if isinstance(decision_context, dict) else {}
    matrix = decision_evidence_matrix if isinstance(decision_evidence_matrix, dict) else {}

    allowed_options = context.get("options", [])
    allowed_options = allowed_options if isinstance(allowed_options, list) else []
    allowed_option_text = {str(item).strip() for item in allowed_options if str(item).strip()}

    raw_options = value.get("option_analysis", [])
    raw_options = raw_options if isinstance(raw_options, list) else []
    clean_options = []
    for item in raw_options[:20]:
        if not isinstance(item, dict):
            continue
        option = str(item.get("option") or "").strip()
        if not option:
            continue
        if allowed_option_text and option not in allowed_option_text:
            continue
        clean_options.append({
            "option": option,
            "supporting_evidence": item.get("supporting_evidence", []) if isinstance(item.get("supporting_evidence", []), list) else [],
            "benefits": item.get("benefits", []) if isinstance(item.get("benefits", []), list) else [],
            "risks": item.get("risks", []) if isinstance(item.get("risks", []), list) else [],
            "tradeoffs": item.get("tradeoffs", []) if isinstance(item.get("tradeoffs", []), list) else [],
            "unknowns": item.get("unknowns", []) if isinstance(item.get("unknowns", []), list) else [],
        })

    dimensions = value.get("comparison_dimensions", [])
    dimensions = dimensions if isinstance(dimensions, list) else []
    questions = value.get("unresolved_questions", [])
    questions = questions if isinstance(questions, list) else []

    return {
        "decision_summary": str(value.get("decision_summary") or "").strip(),
        "option_analysis": clean_options,
        "comparison_dimensions": [str(x).strip() for x in dimensions[:20] if str(x).strip()],
        "unresolved_questions": [str(x).strip() for x in questions[:20] if str(x).strip()],
        "evidence_source_count": int(matrix.get("evidence_source_count", 0) or 0),
        "grounded": bool(matrix.get("evidence_source_count", 0)),
    }


def generate_decision_analysis(decision_context, decision_evidence_matrix, decision_readiness):
    """Analyze a decision only when Step 4D says the context is ready."""
    context = decision_context if isinstance(decision_context, dict) else {}
    matrix = decision_evidence_matrix if isinstance(decision_evidence_matrix, dict) else {}
    readiness = decision_readiness if isinstance(decision_readiness, dict) else {}

    if not bool(readiness.get("ready", False)):
        return {
            "built": True,
            "analyzed": False,
            "status": "not_ready",
            "reason": str(readiness.get("reason") or "decision_not_ready"),
            "analysis": {},
        }

    evidence = matrix.get("supporting_evidence", [])
    evidence = evidence if isinstance(evidence, list) else []
    supplied = {
        "decision": context.get("decision", []),
        "options": context.get("options", []),
        "goals": context.get("goals", []),
        "constraints": context.get("constraints", []),
        "risks": context.get("risks", []),
        "uncertainties": context.get("uncertainties", []),
        "tradeoffs": context.get("tradeoffs", []),
        "evidence": evidence[:10],
    }

    system_prompt = f"""You are the Decision Analysis Engine for Dusra Brain.
Analyze ONLY the supplied decision context and evidence.
Do not invent facts, options, risks, benefits, numbers, dates, or relationships.
Do not make the decision and do not recommend an option.
Preserve uncertainty and explicitly surface unresolved questions.

SUPPLIED DECISION CONTEXT:
{supplied}

Return ONLY valid JSON:
{{
  "decision_summary": "",
  "option_analysis": [
    {{
      "option": "",
      "supporting_evidence": [],
      "benefits": [],
      "risks": [],
      "tradeoffs": [],
      "unknowns": []
    }}
  ],
  "comparison_dimensions": [],
  "unresolved_questions": []
}}
"""

    try:
        raw = groq_request(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "Analyze the supplied decision context."},
            ],
            temperature=0,
        )
        parsed = json.loads(clean_json_response(raw))
        analysis = validate_decision_analysis_result(parsed, context, matrix)
        return {
            "built": True,
            "analyzed": True,
            "status": "analyzed",
            "reason": "decision_context_ready",
            "analysis": analysis,
        }
    except Exception:
        return {
            "built": True,
            "analyzed": False,
            "status": "failed",
            "reason": "decision_analysis_failed",
            "analysis": {},
        }


def build_decision_analysis_trace(decision_readiness, analysis_result):
    """Compact public verification trace for Step 4E."""
    readiness = decision_readiness if isinstance(decision_readiness, dict) else {}
    result = analysis_result if isinstance(analysis_result, dict) else {}
    analysis = result.get("analysis", {})
    analysis = analysis if isinstance(analysis, dict) else {}
    option_analysis = analysis.get("option_analysis", [])
    option_analysis = option_analysis if isinstance(option_analysis, list) else []
    unresolved = analysis.get("unresolved_questions", [])
    unresolved = unresolved if isinstance(unresolved, list) else []

    return {
        "built": bool(result.get("built", False)),
        "analyzed": bool(result.get("analyzed", False)),
        "status": str(result.get("status") or "failed"),
        "reason": str(result.get("reason") or "unknown"),
        "readiness_status": str(readiness.get("status") or "not_ready"),
        "option_analysis_count": len(option_analysis),
        "unresolved_question_count": len(unresolved),
        "evidence_source_count": int(analysis.get("evidence_source_count", 0) or 0),
    }

# ============================================================
# PHASE 7 — STEP 4G
# DECISION SYNTHESIS QUALITY GATE
# ============================================================

def evaluate_decision_synthesis_quality_gate(
    decision_readiness,
    decision_analysis,
    decision_synthesis,
):
    """
    Deterministically verify the Step 4F synthesis before it is treated as
    a valid decision-support artifact.

    This layer does not call the model, query the database, write memory,
    create evidence, choose an option, or generate a recommendation.
    It validates only the already-built 4D/4E/4F outputs.
    """
    readiness = decision_readiness if isinstance(decision_readiness, dict) else {}
    analysis_result = decision_analysis if isinstance(decision_analysis, dict) else {}
    synthesis_result = decision_synthesis if isinstance(decision_synthesis, dict) else {}

    ready = bool(readiness.get("ready", False))
    analyzed = bool(analysis_result.get("analyzed", False))
    synthesized = bool(synthesis_result.get("synthesized", False))

    synthesis = synthesis_result.get("synthesis", {})
    synthesis = synthesis if isinstance(synthesis, dict) else {}

    summary = str(synthesis.get("synthesis_summary") or "").strip()
    comparison = synthesis.get("option_comparison", [])
    comparison = comparison if isinstance(comparison, list) else []
    unresolved = synthesis.get("unresolved_questions", [])
    unresolved = unresolved if isinstance(unresolved, list) else []
    recommendation = str(synthesis.get("recommendation") or "").strip()

    analysis = analysis_result.get("analysis", {})
    analysis = analysis if isinstance(analysis, dict) else {}
    analyzed_options = analysis.get("option_analysis", [])
    analyzed_options = analyzed_options if isinstance(analyzed_options, list) else []
    allowed_options = {
        str(item.get("option") or "").strip()
        for item in analyzed_options
        if isinstance(item, dict) and str(item.get("option") or "").strip()
    }
    compared_options = {
        str(item.get("option") or "").strip()
        for item in comparison
        if isinstance(item, dict) and str(item.get("option") or "").strip()
    }

    invalid_option_count = 0
    for item in comparison:
        if not isinstance(item, dict):
            invalid_option_count += 1
            continue
        option = str(item.get("option") or "").strip()
        if not option or (allowed_options and option not in allowed_options):
            invalid_option_count += 1

    checks = {
        "readiness_ready": ready,
        "analysis_available": analyzed,
        "synthesis_built": bool(synthesis_result.get("built", False)),
        "synthesis_completed": synthesized,
        "summary_present": bool(summary),
        "option_references_valid": invalid_option_count == 0,
        "recommendation_absent": not bool(recommendation),
    }

    passed = all(checks.values())

    if not ready:
        status = "not_ready"
        reason = str(readiness.get("reason") or "decision_not_ready")
    elif not analyzed:
        status = "not_ready"
        reason = "decision_analysis_unavailable"
    elif not synthesized:
        status = "fail"
        reason = str(synthesis_result.get("reason") or "decision_synthesis_failed")
    elif invalid_option_count > 0:
        status = "fail"
        reason = "invalid_option_reference"
    elif recommendation:
        status = "fail"
        reason = "recommendation_present"
    elif not summary:
        status = "fail"
        reason = "synthesis_summary_missing"
    else:
        status = "pass"
        reason = "decision_synthesis_verified"

    return {
        "passed": bool(passed),
        "status": status,
        "reason": reason,
        "checks": checks,
        "option_comparison_count": len(comparison),
        "unresolved_question_count": len(unresolved),
        "analyzed_option_count": len(allowed_options),
        "invalid_option_count": invalid_option_count,
        "recommendation_generated": bool(recommendation),
        "compared_option_count": len(compared_options),
    }


def build_decision_synthesis_quality_trace(
    decision_readiness,
    decision_analysis,
    decision_synthesis,
    quality_result,
):
    """Compact public verification trace for Step 4G."""
    readiness = decision_readiness if isinstance(decision_readiness, dict) else {}
    analysis = decision_analysis if isinstance(decision_analysis, dict) else {}
    synthesis = decision_synthesis if isinstance(decision_synthesis, dict) else {}
    result = quality_result if isinstance(quality_result, dict) else {}

    return {
        "built": True,
        "passed": bool(result.get("passed", False)),
        "status": str(result.get("status") or "fail"),
        "reason": str(result.get("reason") or "unknown"),
        "readiness_status": str(readiness.get("status") or "not_ready"),
        "analysis_status": str(analysis.get("status") or "unknown"),
        "synthesis_status": str(synthesis.get("status") or "unknown"),
        "option_comparison_count": int(result.get("option_comparison_count", 0) or 0),
        "unresolved_question_count": int(result.get("unresolved_question_count", 0) or 0),
        "invalid_option_count": int(result.get("invalid_option_count", 0) or 0),
        "recommendation_generated": bool(result.get("recommendation_generated", False)),
    }


# ============================================================
# PHASE 7 — STEP 4F
# DECISION SYNTHESIS ENGINE
# ============================================================

def validate_decision_synthesis_result(result, decision_analysis):
    """Deterministically validate synthesis against the already-built analysis."""
    if not isinstance(result, dict):
        return {"synthesis": {}}

    analysis = decision_analysis if isinstance(decision_analysis, dict) else {}
    source = analysis.get("analysis", {})
    source = source if isinstance(source, dict) else {}

    allowed_options = []
    raw_options = source.get("option_analysis", [])
    if isinstance(raw_options, list):
        for item in raw_options[:20]:
            if isinstance(item, dict):
                name = str(item.get("option") or "").strip()
                if name:
                    allowed_options.append(name)

    raw_comparison = result.get("option_comparison", [])
    raw_comparison = raw_comparison if isinstance(raw_comparison, list) else []
    clean_comparison = []
    seen = set()
    for item in raw_comparison[:20]:
        if not isinstance(item, dict):
            continue
        option = str(item.get("option") or "").strip()
        if not option or (allowed_options and option not in allowed_options):
            continue
        if option in seen:
            continue
        seen.add(option)
        clean_comparison.append({
            "option": option,
            "supported_factors": [
                str(x).strip() for x in (item.get("supported_factors", []) if isinstance(item.get("supported_factors", []), list) else [])[:20]
                if str(x).strip()
            ],
            "risks": [
                str(x).strip() for x in (item.get("risks", []) if isinstance(item.get("risks", []), list) else [])[:20]
                if str(x).strip()
            ],
            "tradeoffs": [
                str(x).strip() for x in (item.get("tradeoffs", []) if isinstance(item.get("tradeoffs", []), list) else [])[:20]
                if str(x).strip()
            ],
            "unknowns": [
                str(x).strip() for x in (item.get("unknowns", []) if isinstance(item.get("unknowns", []), list) else [])[:20]
                if str(x).strip()
            ],
        })

    return {
        "synthesis_summary": str(result.get("synthesis_summary") or "").strip(),
        "option_comparison": clean_comparison,
        "key_tradeoffs": [
            str(x).strip() for x in (result.get("key_tradeoffs", []) if isinstance(result.get("key_tradeoffs", []), list) else [])[:20]
            if str(x).strip()
        ],
        "key_risks": [
            str(x).strip() for x in (result.get("key_risks", []) if isinstance(result.get("key_risks", []), list) else [])[:20]
            if str(x).strip()
        ],
        "uncertainties": [
            str(x).strip() for x in (result.get("uncertainties", []) if isinstance(result.get("uncertainties", []), list) else [])[:20]
            if str(x).strip()
        ],
        "unresolved_questions": [
            str(x).strip() for x in (result.get("unresolved_questions", []) if isinstance(result.get("unresolved_questions", []), list) else [])[:20]
            if str(x).strip()
        ],
        "recommendation": "",
    }


def generate_decision_synthesis(decision_context, decision_readiness, decision_analysis):
    """Synthesize only a successfully analyzed, evidence-grounded decision context.

    This layer does not make a decision or recommend an option. It only
    organizes the validated analysis into a compact decision-support synthesis.
    """
    context = decision_context if isinstance(decision_context, dict) else {}
    readiness = decision_readiness if isinstance(decision_readiness, dict) else {}
    analysis_result = decision_analysis if isinstance(decision_analysis, dict) else {}

    if not bool(readiness.get("ready", False)):
        return {
            "built": True,
            "synthesized": False,
            "status": "not_ready",
            "reason": str(readiness.get("reason") or "decision_not_ready"),
            "synthesis": {},
        }

    if not bool(analysis_result.get("analyzed", False)):
        return {
            "built": True,
            "synthesized": False,
            "status": "analysis_unavailable",
            "reason": "decision_analysis_unavailable",
            "synthesis": {},
        }

    analysis = analysis_result.get("analysis", {})
    analysis = analysis if isinstance(analysis, dict) else {}

    supplied = {
        "decision": context.get("decision", []),
        "goals": context.get("goals", []),
        "constraints": context.get("constraints", []),
        "analysis": analysis,
    }

    system_prompt = f"""You are the Decision Synthesis Engine for Dusra Brain.

Synthesize ONLY the supplied decision context and validated analysis.
Do not invent facts, options, risks, benefits, numbers, dates, or relationships.
Do not choose an option and do not recommend an option.
Preserve uncertainty and unresolved questions.

SUPPLIED DATA:
{supplied}

Return ONLY valid JSON:
{{
  "synthesis_summary": "",
  "option_comparison": [
    {{
      "option": "",
      "supported_factors": [],
      "risks": [],
      "tradeoffs": [],
      "unknowns": []
    }}
  ],
  "key_tradeoffs": [],
  "key_risks": [],
  "uncertainties": [],
  "unresolved_questions": []
}}
"""

    try:
        raw = groq_request(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "Synthesize the validated decision analysis."},
            ],
            temperature=0,
        )
        parsed = json.loads(clean_json_response(raw))
        synthesis = validate_decision_synthesis_result(parsed, analysis_result)
        return {
            "built": True,
            "synthesized": True,
            "status": "synthesized",
            "reason": "decision_analysis_synthesized",
            "synthesis": synthesis,
        }
    except Exception:
        return {
            "built": True,
            "synthesized": False,
            "status": "failed",
            "reason": "decision_synthesis_failed",
            "synthesis": {},
        }


def build_decision_synthesis_trace(decision_readiness, analysis_result, synthesis_result):
    """Compact public verification trace for Step 4F."""
    readiness = decision_readiness if isinstance(decision_readiness, dict) else {}
    analysis = analysis_result if isinstance(analysis_result, dict) else {}
    result = synthesis_result if isinstance(synthesis_result, dict) else {}
    synthesis = result.get("synthesis", {})
    synthesis = synthesis if isinstance(synthesis, dict) else {}
    comparison = synthesis.get("option_comparison", [])
    comparison = comparison if isinstance(comparison, list) else []
    unresolved = synthesis.get("unresolved_questions", [])
    unresolved = unresolved if isinstance(unresolved, list) else []

    return {
        "built": bool(result.get("built", False)),
        "synthesized": bool(result.get("synthesized", False)),
        "status": str(result.get("status") or "failed"),
        "reason": str(result.get("reason") or "unknown"),
        "readiness_status": str(readiness.get("status") or "not_ready"),
        "analysis_status": str(analysis.get("status") or "unknown"),
        "option_comparison_count": len(comparison),
        "unresolved_question_count": len(unresolved),
        "recommendation_generated": False,
    }


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
# ============================================================
# PHASE 7 — STEP 4H
# DECISION CAPTURE AND ACTION GATE
# ============================================================

def validate_decision_capture(decision_readiness, decision_synthesis_quality, decision_synthesis):
    """Deterministically gate whether a user decision may be captured.

    This layer never chooses an option, never creates an action, and never
    writes to memory. It only validates an explicitly supplied user decision
    against the already validated 4D-4G decision-support chain.
    """
    readiness = decision_readiness if isinstance(decision_readiness, dict) else {}
    quality = decision_synthesis_quality if isinstance(decision_synthesis_quality, dict) else {}
    synthesis = decision_synthesis if isinstance(decision_synthesis, dict) else {}

    ready = bool(readiness.get("ready", False))
    quality_passed = bool(quality.get("passed", False))
    synthesized = bool(synthesis.get("synthesized", False))

    return {
        "built": True,
        "capturable": bool(ready and quality_passed and synthesized),
        "status": "capturable" if (ready and quality_passed and synthesized) else "not_ready",
        "reason": (
            "decision_support_validated"
            if (ready and quality_passed and synthesized)
            else "decision_support_not_validated"
        ),
        "decision_recorded": False,
        "action_created": False,
        "recommendation_generated": False,
    }


def build_decision_capture_trace(decision_readiness, decision_synthesis_quality, decision_synthesis, capture_result):
    """Compact public verification trace for Step 4H."""
    readiness = decision_readiness if isinstance(decision_readiness, dict) else {}
    quality = decision_synthesis_quality if isinstance(decision_synthesis_quality, dict) else {}
    synthesis = decision_synthesis if isinstance(decision_synthesis, dict) else {}
    result = capture_result if isinstance(capture_result, dict) else {}

    return {
        "built": bool(result.get("built", False)),
        "capturable": bool(result.get("capturable", False)),
        "status": str(result.get("status") or "not_ready"),
        "reason": str(result.get("reason") or "unknown"),
        "readiness_status": str(readiness.get("status") or "not_ready"),
        "quality_status": str(quality.get("status") or "unknown"),
        "synthesis_status": str(synthesis.get("status") or "unknown"),
        "decision_recorded": False,
        "action_created": False,
        "recommendation_generated": False,
    }


# ============================================================
# PHASE 7 — STEP 4I
# EXPLICIT USER DECISION INPUT INTERFACE
# ============================================================

def validate_explicit_decision_input(
    decision_capture,
    decision_payload,
):
    """
    Deterministically validate an explicit user decision input.

    Step 4I is an input boundary only:
    - it never infers a decision;
    - it never selects an option;
    - it never creates an action;
    - it never writes memory;
    - it requires the upstream Step 4H gate to be capturable;
    - it requires an explicit confirmation from the caller.

    The decision text and selected option are treated as user-supplied
    input, not as facts generated by Dusra Brain.
    """

    gate = (
        decision_capture
        if isinstance(decision_capture, dict)
        else {}
    )

    payload = (
        decision_payload
        if isinstance(decision_payload, dict)
        else {}
    )

    gate_capturable = bool(
        gate.get("capturable", False)
    )

    decision_text = str(
        payload.get("decision")
        or ""
    ).strip()

    selected_option = str(
        payload.get("selected_option")
        or ""
    ).strip()

    rationale = str(
        payload.get("rationale")
        or ""
    ).strip()

    explicit_confirmation = bool(
        payload.get("confirmed", False)
    )

    if not gate_capturable:
        # A confirmed user decision is still a valid user record even when
        # the optional decision-support chain is not fully ready. The system
        # must never choose for the user, but it should not block the user
        # from explicitly recording what they decided.
        if decision_text and explicit_confirmation:
            return {
                "built": True,
                "accepted": True,
                "status": "accepted",
                "reason": "explicit_user_decision_accepted_without_decision_support",
                "gate_capturable": False,
                "explicit_decision_present": True,
                "selected_option_present": bool(selected_option),
                "rationale_present": bool(rationale),
                "explicit_confirmation": True,
                "decision_recorded": False,
                "action_created": False,
                "recommendation_generated": False,
            }

        return {
            "built": True,
            "accepted": False,
            "status": "not_ready",
            "reason": "decision_support_not_validated",
            "gate_capturable": False,
            "explicit_decision_present": bool(decision_text),
            "selected_option_present": bool(selected_option),
            "rationale_present": bool(rationale),
            "explicit_confirmation": explicit_confirmation,
            "decision_recorded": False,
            "action_created": False,
            "recommendation_generated": False,
        }

    if not decision_text:
        return {
            "built": True,
            "accepted": False,
            "status": "not_ready",
            "reason": "explicit_decision_missing",
            "gate_capturable": True,
            "explicit_decision_present": False,
            "selected_option_present": bool(selected_option),
            "rationale_present": bool(rationale),
            "explicit_confirmation": explicit_confirmation,
            "decision_recorded": False,
            "action_created": False,
            "recommendation_generated": False,
        }

    if not explicit_confirmation:
        return {
            "built": True,
            "accepted": False,
            "status": "not_ready",
            "reason": "explicit_confirmation_required",
            "gate_capturable": True,
            "explicit_decision_present": True,
            "selected_option_present": bool(selected_option),
            "rationale_present": bool(rationale),
            "explicit_confirmation": False,
            "decision_recorded": False,
            "action_created": False,
            "recommendation_generated": False,
        }

    return {
        "built": True,
        "accepted": True,
        "status": "accepted",
        "reason": "explicit_user_decision_received",
        "gate_capturable": True,
        "explicit_decision_present": True,
        "selected_option_present": bool(selected_option),
        "rationale_present": bool(rationale),
        "explicit_confirmation": True,
        "decision_recorded": False,
        "action_created": False,
        "recommendation_generated": False,
    }


def build_explicit_decision_input_trace(
    decision_capture,
    input_result,
):
    """
    Build the public Step 4I verification trace.

    The actual decision text is deliberately not echoed into the trace.
    This keeps the trace a structural verification artifact rather than
    duplicating user-provided decision content.
    """

    gate = (
        decision_capture
        if isinstance(decision_capture, dict)
        else {}
    )

    result = (
        input_result
        if isinstance(input_result, dict)
        else {}
    )

    return {
        "built": bool(result.get("built", False)),
        "accepted": bool(result.get("accepted", False)),
        "status": str(
            result.get("status")
            or "not_ready"
        ),
        "reason": str(
            result.get("reason")
            or "unknown"
        ),
        "gate_capturable": bool(
            gate.get("capturable", False)
        ),
        "explicit_decision_present": bool(
            result.get(
                "explicit_decision_present",
                False
            )
        ),
        "selected_option_present": bool(
            result.get(
                "selected_option_present",
                False
            )
        ),
        "rationale_present": bool(
            result.get(
                "rationale_present",
                False
            )
        ),
        "explicit_confirmation": bool(
            result.get(
                "explicit_confirmation",
                False
            )
        ),
        "decision_recorded": False,
        "action_created": False,
        "recommendation_generated": False,
    }



# ============================================================
# PHASE 7 — STEP 4K
# DECISION HISTORY RECALL & RETRIEVAL
# ============================================================

def detect_decision_history_recall(message):
    """Deterministically detect requests to recall persisted decisions."""
    text = str(message or "").strip().lower()
    if not text:
        return False

    normalized = re.sub(r"[^a-z0-9]+", " ", text)
    tokens = set(normalized.split())

    decision_terms = {
        "decision", "decisions", "decided", "decide",
        "chose", "chosen", "selected", "selection",
    }
    history_terms = {
        "history", "previous", "earlier", "past", "made",
        "recorded", "records", "remember",
    }

    explicit_phrases = {
        "what did i decide",
        "what decisions have i made",
        "what have i decided",
        "show my decisions",
        "show decision history",
        "decision history",
        "my decision history",
        "decisions i made",
        "decisions about",
        "what was my decision",
    }

    if any(phrase in normalized for phrase in explicit_phrases):
        return True

    return bool(
        tokens.intersection(decision_terms)
        and tokens.intersection(history_terms)
    )


def _decision_recall_tokens(value):
    text = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())
    stop_words = {
        "the", "and", "for", "with", "about", "what", "when",
        "where", "which", "who", "did", "have", "has", "are",
        "was", "were", "you", "your", "my", "our", "this", "that",
        "show", "tell", "remember", "history", "previous", "earlier",
        "past", "made", "decision", "decisions", "decide", "decided",
        "i", "me", "of", "to", "on", "in", "from", "any", "all",
    }
    return {
        token for token in text.split()
        if len(token) >= 3 and token not in stop_words
    }


def rank_decision_history(query, history, limit=10):
    """Rank persisted decisions deterministically; never invents records."""
    rows = history if isinstance(history, list) else []
    query_tokens = _decision_recall_tokens(query)

    ranked = []
    for row in rows:
        if not isinstance(row, dict):
            continue

        searchable = " ".join([
            str(row.get("title") or ""),
            str(row.get("decision") or ""),
            str(row.get("selected_option") or ""),
            str(row.get("rationale") or ""),
        ])
        row_tokens = _decision_recall_tokens(searchable)
        overlap = query_tokens.intersection(row_tokens)

        score = len(overlap)
        if query_tokens and not overlap:
            continue

        ranked.append({
            **row,
            "recall_score": score,
            "matched_tokens": sorted(overlap)[:20],
        })

    ranked.sort(
        key=lambda item: (
            int(item.get("recall_score", 0) or 0),
            str(item.get("created_at") or ""),
            int(item.get("id", 0) or 0),
        ),
        reverse=True,
    )

    return ranked[:max(1, min(int(limit or 10), 20))]


def recall_decision_history(user_id, query, limit=10):
    """Retrieve only persisted decisions relevant to the user's query."""
    if not detect_decision_history_recall(query):
        return {
            "built": True,
            "triggered": False,
            "status": "not_triggered",
            "reason": "not_a_decision_history_query",
            "query": str(query or "").strip(),
            "candidate_count": 0,
            "selected_count": 0,
            "decisions": [],
        }

    history = get_decision_history(
        user_id=user_id,
        limit=100,
    )
    selected = rank_decision_history(
        query=query,
        history=history,
        limit=limit,
    )

    return {
        "built": True,
        "triggered": True,
        "status": "found" if selected else "empty",
        "reason": "persisted_decisions_retrieved" if selected else "no_matching_persisted_decisions",
        "query": str(query or "").strip(),
        "candidate_count": len(history),
        "selected_count": len(selected),
        "decisions": selected,
    }


def build_decision_history_recall_trace(recall_result):
    """Compact public Step 4K verification trace."""
    result = recall_result if isinstance(recall_result, dict) else {}
    return {
        "built": bool(result.get("built", False)),
        "triggered": bool(result.get("triggered", False)),
        "status": str(result.get("status") or "not_triggered"),
        "reason": str(result.get("reason") or "unknown"),
        "candidate_count": int(result.get("candidate_count", 0) or 0),
        "selected_count": int(result.get("selected_count", 0) or 0),
        "decision_ids": [
            item.get("id")
            for item in result.get("decisions", [])
            if isinstance(item, dict) and item.get("id") is not None
        ][:20],
    }

# ============================================================
# PHASE 7 — STEP 4L
# DECISION HISTORY GROUNDED ANSWER / RECALL
# ============================================================

def build_decision_history_grounded_answer(recall_result):
    """
    Build a deterministic user-facing answer from ONLY persisted decision
    records returned by Step 4K. No model call, no new records, and no
    inference beyond the stored decision fields.
    """
    result = recall_result if isinstance(recall_result, dict) else {}
    triggered = bool(result.get("triggered", False))
    decisions = result.get("decisions", [])
    decisions = [item for item in decisions if isinstance(item, dict)]

    if not triggered:
        return {
            "built": True,
            "answered": False,
            "status": "not_triggered",
            "reason": "not_a_decision_history_query",
            "answer": "",
            "decision_count": 0,
            "decision_ids": [],
            "decision_evidence": [],
        }

    if not decisions:
        return {
            "built": True,
            "answered": True,
            "status": "empty",
            "reason": "no_matching_persisted_decisions",
            "answer": "I don't have any matching persisted decisions for that request.",
            "decision_count": 0,
            "decision_ids": [],
            "decision_evidence": [],
        }

    lines = [
        "Here are the persisted decisions matching your request:"
    ]
    evidence = []
    ids = []

    for index, item in enumerate(decisions[:20], start=1):
        decision_id = item.get("id")
        if decision_id is not None:
            ids.append(decision_id)

        decision = str(item.get("decision") or "").strip()
        selected = str(item.get("selected_option") or "").strip()
        rationale = str(item.get("rationale") or "").strip()
        created_at = str(item.get("created_at") or "").strip()
        session_id = str(item.get("session_id") or "").strip()

        lines.append("\n" + str(index) + ". Decision #" + str(decision_id))
        if decision:
            lines.append("Decision: " + decision)
        if selected:
            lines.append("Selected option: " + selected)
        if rationale:
            lines.append("Rationale: " + rationale)
        if created_at:
            lines.append("Recorded: " + created_at)

        evidence.append({
            "source_type": "decision_history",
            "source_id": decision_id,
            "label": "Decision #" + str(decision_id),
            "decision": decision,
            "selected_option": selected,
            "rationale": rationale,
            "created_at": created_at,
            "session_id": session_id,
        })

    return {
        "built": True,
        "answered": True,
        "status": "answered",
        "reason": "persisted_decisions_grounded_answer",
        "answer": "\n".join(lines).strip(),
        "decision_count": len(evidence),
        "decision_ids": ids[:20],
        "decision_evidence": evidence,
    }


def validate_decision_history_grounded_answer(answer_result, recall_result):
    """Deterministically verify that every cited decision exists in Step 4K."""
    result = answer_result if isinstance(answer_result, dict) else {}
    recall = recall_result if isinstance(recall_result, dict) else {}
    recalled = recall.get("decisions", [])
    recalled_ids = {
        item.get("id")
        for item in recalled
        if isinstance(item, dict) and item.get("id") is not None
    }
    evidence = result.get("decision_evidence", [])
    evidence = evidence if isinstance(evidence, list) else []

    valid = []
    invalid = []
    seen = set()
    for item in evidence:
        if not isinstance(item, dict):
            continue
        source_id = item.get("source_id")
        if source_id in seen:
            continue
        seen.add(source_id)
        if source_id in recalled_ids:
            valid.append(item)
        else:
            invalid.append(item)

    answered = bool(result.get("answered", False))
    verified = (
        not answered
        or (bool(result.get("answer")) and bool(valid) and not invalid)
    )

    return {
        "built": True,
        "verified": bool(verified),
        "status": "pass" if verified else "fail",
        "reason": (
            "decision_history_answer_verified"
            if verified and answered
            else "not_triggered"
            if not answered
            else "invalid_decision_history_evidence"
        ),
        "valid_evidence_count": len(valid),
        "invalid_evidence_count": len(invalid),
        "decision_ids": [item.get("source_id") for item in valid][:20],
        "answer": str(result.get("answer") or "").strip(),
        "fallback_used": False,
    }


def build_decision_history_answer_trace(answer_result, verification_result):
    """Compact public Step 4L verification trace."""
    result = answer_result if isinstance(answer_result, dict) else {}
    verified = verification_result if isinstance(verification_result, dict) else {}
    return {
        "built": bool(result.get("built", False)),
        "answered": bool(result.get("answered", False)),
        "verified": bool(verified.get("verified", False)),
        "status": str(verified.get("status") or result.get("status") or "not_triggered"),
        "reason": str(verified.get("reason") or result.get("reason") or "unknown"),
        "decision_count": int(result.get("decision_count", 0) or 0),
        "valid_evidence_count": int(verified.get("valid_evidence_count", 0) or 0),
        "invalid_evidence_count": int(verified.get("invalid_evidence_count", 0) or 0),
        "decision_ids": [
            item for item in verified.get("decision_ids", [])
            if item is not None
        ][:20],
        "fallback_used": bool(verified.get("fallback_used", False)),
    }


# ============================================================
# PHASE 7 — STEP 4J
# DECISION PERSISTENCE & DECISION HISTORY
# ============================================================

def ensure_decision_history_table():
    """Create the append-only decision history store if it does not exist."""

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS decision_history
                (
                    id SERIAL PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL DEFAULT 'default',
                    title TEXT DEFAULT 'New Chat',
                    decision TEXT NOT NULL,
                    selected_option TEXT DEFAULT '',
                    rationale TEXT DEFAULT '',
                    evidence_trace JSONB DEFAULT '[]'::jsonb,
                    decision_input_trace JSONB DEFAULT '{}'::jsonb,
                    decision_capture_trace JSONB DEFAULT '{}'::jsonb,
                    decision_synthesis_quality_trace JSONB DEFAULT '{}'::jsonb,
                    fingerprint TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, fingerprint)
                )
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_decision_history_user_created
                ON decision_history(user_id, created_at DESC)
                """
            )

        conn.commit()


def _decision_history_fingerprint(
    user_id,
    session_id,
    decision,
    selected_option,
    rationale,
):
    """Stable idempotency key for one explicit decision submission."""

    raw = "|".join([
        str(user_id or "").strip(),
        str(session_id or "default").strip(),
        str(decision or "").strip(),
        str(selected_option or "").strip(),
        str(rationale or "").strip(),
    ])

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


def persist_explicit_decision(
    user_id,
    session_id,
    title,
    decision_input,
    decision_payload,
    evidence_trace,
    decision_capture,
    decision_synthesis_quality,
):
    """
    Persist ONLY an accepted Step 4I decision.

    Step 4J never infers, rewrites, selects, or recommends a decision.
    Identical submissions are idempotent and evidence provenance is retained.
    """

    result = decision_input if isinstance(decision_input, dict) else {}
    payload = decision_payload if isinstance(decision_payload, dict) else {}
    evidence = evidence_trace if isinstance(evidence_trace, list) else []
    capture = decision_capture if isinstance(decision_capture, dict) else {}
    quality = decision_synthesis_quality if isinstance(decision_synthesis_quality, dict) else {}

    if not bool(result.get("accepted", False)):
        return {
            "built": True,
            "persisted": False,
            "status": "not_persisted",
            "reason": str(
                result.get("reason") or "explicit_decision_not_accepted"
            ),
            "decision_id": None,
            "duplicate": False,
            "decision_recorded": False,
            "history_available": False,
        }

    decision = str(payload.get("decision") or "").strip()
    selected_option = str(payload.get("selected_option") or "").strip()
    rationale = str(payload.get("rationale") or "").strip()

    if not decision or not bool(payload.get("confirmed", False)):
        return {
            "built": True,
            "persisted": False,
            "status": "not_persisted",
            "reason": "invalid_accepted_input",
            "decision_id": None,
            "duplicate": False,
            "decision_recorded": False,
            "history_available": False,
        }

    ensure_decision_history_table()

    fingerprint = _decision_history_fingerprint(
        user_id,
        session_id,
        decision,
        selected_option,
        rationale,
    )

    decision_input_trace = {
        key: result.get(key)
        for key in [
            "built",
            "accepted",
            "status",
            "reason",
            "gate_capturable",
            "explicit_decision_present",
            "selected_option_present",
            "rationale_present",
            "explicit_confirmation",
        ]
    }

    capture_trace = {
        key: capture.get(key)
        for key in [
            "built",
            "capturable",
            "status",
            "reason",
            "readiness_status",
            "quality_status",
            "synthesis_status",
        ]
    }

    quality_trace = {
        key: quality.get(key)
        for key in [
            "built",
            "passed",
            "status",
            "reason",
            "readiness_status",
            "analysis_status",
            "synthesis_status",
        ]
    }

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                INSERT INTO decision_history
                (
                    user_id,
                    session_id,
                    title,
                    decision,
                    selected_option,
                    rationale,
                    evidence_trace,
                    decision_input_trace,
                    decision_capture_trace,
                    decision_synthesis_quality_trace,
                    fingerprint
                )
                VALUES
                (
                    %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb,
                    %s::jsonb, %s::jsonb, %s
                )
                ON CONFLICT (user_id, fingerprint) DO NOTHING
                RETURNING id
                """,
                (
                    str(user_id),
                    str(session_id or "default"),
                    str(title or "New Chat"),
                    decision,
                    selected_option,
                    rationale,
                    json.dumps(evidence, ensure_ascii=False, default=str),
                    json.dumps(decision_input_trace, ensure_ascii=False, default=str),
                    json.dumps(capture_trace, ensure_ascii=False, default=str),
                    json.dumps(quality_trace, ensure_ascii=False, default=str),
                    fingerprint,
                )
            )

            inserted = cur.fetchone()

            if inserted:
                decision_id = int(inserted[0])
                duplicate = False
            else:
                cur.execute(
                    """
                    SELECT id
                    FROM decision_history
                    WHERE user_id = %s
                      AND fingerprint = %s
                    LIMIT 1
                    """,
                    (str(user_id), fingerprint)
                )
                existing = cur.fetchone()
                decision_id = int(existing[0]) if existing else None
                duplicate = True

        conn.commit()

    return {
        "built": True,
        "persisted": decision_id is not None,
        "status": "duplicate" if duplicate else "persisted",
        "reason": (
            "decision_already_persisted"
            if duplicate
            else "explicit_user_decision_persisted"
        ),
        "decision_id": decision_id,
        "duplicate": duplicate,
        "decision_recorded": decision_id is not None,
        "history_available": decision_id is not None,
    }


def get_decision_history(user_id, limit=50):
    """Return the user's persisted decision history, newest first."""

    ensure_decision_history_table()

    try:
        limit = int(limit)
    except Exception:
        limit = 50

    limit = max(1, min(100, limit))

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    id, session_id, title, decision, selected_option,
                    rationale, evidence_trace, created_at
                FROM decision_history
                WHERE user_id = %s
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                (str(user_id), limit)
            )

            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "session_id": row[1],
            "title": row[2],
            "decision": row[3],
            "selected_option": row[4],
            "rationale": row[5],
            "evidence_trace": row[6] or [],
            "created_at": row[7].isoformat() if row[7] else None,
        }
        for row in rows
    ]


def build_decision_history_trace(persistence_result):
    """Compact public Step 4J verification trace."""

    result = (
        persistence_result
        if isinstance(persistence_result, dict)
        else {}
    )

    return {
        "built": bool(result.get("built", False)),
        "persisted": bool(result.get("persisted", False)),
        "status": str(result.get("status") or "not_persisted"),
        "reason": str(result.get("reason") or "unknown"),
        "decision_id": result.get("decision_id"),
        "duplicate": bool(result.get("duplicate", False)),
        "decision_recorded": bool(result.get("decision_recorded", False)),
        "history_available": bool(result.get("history_available", False)),
    }


# ============================================================
# PHASE 7 — STEP 4M
# DECISION HISTORY ↔ MEMORY / EVIDENCE INTEGRATION
# ============================================================

def _decision_history_source_key(source_type, source_id):
    """Return a normalized source key for evidence provenance checks."""
    normalized_type = str(source_type or "").strip().lower()
    try:
        normalized_id = int(source_id)
    except Exception:
        return None

    if normalized_type not in {
        "memory",
        "entity",
        "relationship",
        "conversation",
    }:
        return None

    return (normalized_type, normalized_id)


def build_decision_history_memory_evidence_bridge(
    recall_result,
    reasoning_context,
):
    """
    Deterministically bridge persisted decision provenance to the currently
    retrieved memory/Brain/conversation evidence.

    This is read-only. It does not modify decision history, memories,
    relationships, or evidence. A historical decision remains a distinct
    decision-history record; this helper only validates and exposes the
    evidence that was stored with that decision and is still addressable in
    the current retrieved context.
    """
    recall = recall_result if isinstance(recall_result, dict) else {}
    context = reasoning_context if isinstance(reasoning_context, dict) else {}

    triggered = bool(recall.get("triggered", False))
    decisions = recall.get("decisions", [])
    decisions = [item for item in decisions if isinstance(item, dict)]

    source_index = context.get("source_index", {})
    source_index = source_index if isinstance(source_index, dict) else {}

    available = set()
    for source_type, index_key in (
        ("memory", "memory_ids"),
        ("entity", "entity_ids"),
        ("relationship", "relationship_ids"),
        ("conversation", "conversation_indexes"),
    ):
        values = source_index.get(index_key, [])
        values = values if isinstance(values, list) else []
        for value in values:
            key = _decision_history_source_key(source_type, value)
            if key is not None:
                available.add(key)

    if not triggered:
        return {
            "built": True,
            "integrated": False,
            "status": "not_triggered",
            "reason": "not_a_decision_history_query",
            "decision_count": 0,
            "linked_decision_count": 0,
            "linked_evidence_count": 0,
            "orphaned_evidence_count": 0,
            "decision_ids": [],
            "bridges": [],
        }

    bridges = []
    linked_decision_count = 0
    linked_evidence_count = 0
    orphaned_evidence_count = 0

    for decision in decisions[:20]:
        decision_id = decision.get("id")
        raw_evidence = decision.get("evidence_trace", [])
        raw_evidence = raw_evidence if isinstance(raw_evidence, list) else []

        linked = []
        orphaned = []
        seen = set()

        for evidence in raw_evidence[:20]:
            if not isinstance(evidence, dict):
                continue

            source_type = str(evidence.get("source_type") or "").strip().lower()
            source_id = evidence.get("source_id")
            key = _decision_history_source_key(source_type, source_id)

            if key is None or key in seen:
                continue

            seen.add(key)
            normalized = {
                "source_type": key[0],
                "source_id": key[1],
                "label": str(evidence.get("label") or "").strip(),
            }

            if key in available:
                linked.append(normalized)
            else:
                orphaned.append(normalized)

        if linked:
            linked_decision_count += 1
            linked_evidence_count += len(linked)
        orphaned_evidence_count += len(orphaned)

        bridges.append({
            "decision_id": decision_id,
            "linked_evidence": linked,
            "orphaned_evidence": orphaned,
            "linked": bool(linked),
            "provenance_complete": bool(linked) and not orphaned,
        })

    integrated = bool(bridges and linked_evidence_count > 0)

    return {
        "built": True,
        "integrated": integrated,
        "status": "integrated" if integrated else "no_linked_evidence",
        "reason": (
            "decision_history_evidence_linked"
            if integrated
            else "no_current_context_evidence_matches"
        ),
        "decision_count": len(decisions[:20]),
        "linked_decision_count": linked_decision_count,
        "linked_evidence_count": linked_evidence_count,
        "orphaned_evidence_count": orphaned_evidence_count,
        "decision_ids": [
            item.get("id")
            for item in decisions[:20]
            if item.get("id") is not None
        ],
        "bridges": bridges,
    }


def build_decision_history_memory_evidence_trace(bridge_result):
    """Compact public Step 4M provenance/integration trace."""
    result = bridge_result if isinstance(bridge_result, dict) else {}

    return {
        "built": bool(result.get("built", False)),
        "integrated": bool(result.get("integrated", False)),
        "status": str(result.get("status") or "not_triggered"),
        "reason": str(result.get("reason") or "unknown"),
        "decision_count": int(result.get("decision_count", 0) or 0),
        "linked_decision_count": int(result.get("linked_decision_count", 0) or 0),
        "linked_evidence_count": int(result.get("linked_evidence_count", 0) or 0),
        "orphaned_evidence_count": int(result.get("orphaned_evidence_count", 0) or 0),
        "decision_ids": [
            item for item in result.get("decision_ids", [])
            if item is not None
        ][:20],
        "read_only": True,
        "decision_history_modified": False,
        "memory_modified": False,
        "evidence_created": False,
    }


# ============================================================
# PHASE 7 — STEP 4N
# DECISION ↔ CURRENT PLAN CONFLICT DETECTION
# ============================================================

def _decision_current_plan_tokens(value):
    """Conservative lexical tokens for deterministic plan comparison."""
    text = str(value or "").lower()
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9_'-]+", text)
        if len(token) >= 4
    }


def _decision_current_plan_subject(decision):
    """Return the best available historical decision subject label."""
    item = decision if isinstance(decision, dict) else {}
    for key in ("subject", "title", "session_title"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def _decision_current_plan_text(decision):
    """Build comparison text only from persisted decision fields."""
    item = decision if isinstance(decision, dict) else {}
    values = []
    for key in ("decision", "selected_option", "rationale"):
        value = item.get(key)
        if isinstance(value, list):
            values.extend(str(v).strip() for v in value if str(v or "").strip())
        elif str(value or "").strip():
            values.append(str(value).strip())
    return " ".join(values).strip()


def _current_plan_memory_items(reasoning_context):
    """Extract only currently retrieved memory records for comparison."""
    context = reasoning_context if isinstance(reasoning_context, dict) else {}
    memories = context.get("memories", [])
    if not isinstance(memories, list):
        return []
    return [item for item in memories[:30] if isinstance(item, dict)]


def _plan_conflict_signals(decision_text, memory_text):
    """
    Conservative deterministic comparison.

    We only report a potential conflict when the current memory has strong
    change/reversal language AND shares meaningful terms with the historical
    decision. Otherwise the result is consistency/insufficient evidence.
    """
    decision_tokens = _decision_current_plan_tokens(decision_text)
    memory_tokens = _decision_current_plan_tokens(memory_text)
    shared = decision_tokens.intersection(memory_tokens)

    change_terms = {
        "changed", "change", "changedto", "instead", "replaced",
        "replace", "switch", "switched", "different", "revised",
        "revisedto", "cancelled", "canceled", "stopped", "stop",
        "dropped", "drop", "abandoned", "abandon", "reversed",
        "reverse", "no", "not", "instead_of",
    }
    memory_lower = str(memory_text or "").lower()
    has_change_signal = any(term in memory_lower for term in change_terms)

    meaningful_shared = {token for token in shared if len(token) >= 5}

    if not decision_tokens or not memory_tokens:
        return {
            "classification": "insufficient_evidence",
            "shared_terms": [],
            "change_signal": False,
            "reason": "decision_or_current_plan_text_missing",
        }

    if has_change_signal and len(meaningful_shared) >= 2:
        return {
            "classification": "potential_conflict",
            "shared_terms": sorted(meaningful_shared)[:20],
            "change_signal": True,
            "reason": "current_plan_contains_change_signal_with_shared_terms",
        }

    if len(meaningful_shared) >= 2:
        return {
            "classification": "consistent",
            "shared_terms": sorted(meaningful_shared)[:20],
            "change_signal": False,
            "reason": "current_plan_shares_meaningful_terms_with_decision",
        }

    return {
        "classification": "insufficient_evidence",
        "shared_terms": sorted(shared)[:20],
        "change_signal": has_change_signal,
        "reason": "insufficient_overlap_for_deterministic_comparison",
    }


def detect_decision_current_plan_conflicts(
    recall_result,
    reasoning_context,
):
    """
    Deterministically compare persisted decisions with currently retrieved
    memories/plans. This is detection only: it never changes a decision,
    memory, recommendation, or action.
    """
    recall = recall_result if isinstance(recall_result, dict) else {}
    triggered = bool(recall.get("triggered", False))
    decisions = recall.get("decisions", [])
    decisions = [item for item in decisions if isinstance(item, dict)]

    memories = _current_plan_memory_items(reasoning_context)

    if not triggered:
        return {
            "built": True,
            "detected": False,
            "status": "not_triggered",
            "reason": "not_a_decision_history_query",
            "decision_count": 0,
            "current_plan_count": 0,
            "consistent_count": 0,
            "potential_conflict_count": 0,
            "insufficient_evidence_count": 0,
            "decision_ids": [],
            "comparisons": [],
        }

    if not decisions:
        return {
            "built": True,
            "detected": False,
            "status": "no_decisions",
            "reason": "no_persisted_decisions_available",
            "decision_count": 0,
            "current_plan_count": len(memories),
            "consistent_count": 0,
            "potential_conflict_count": 0,
            "insufficient_evidence_count": 0,
            "decision_ids": [],
            "comparisons": [],
        }

    comparisons = []
    consistent_count = 0
    potential_conflict_count = 0
    insufficient_count = 0

    for decision in decisions[:20]:
        decision_id = decision.get("id")
        decision_text = _decision_current_plan_text(decision)
        decision_subject = _decision_current_plan_subject(decision)
        matched = []

        for memory in memories[:30]:
            memory_text = str(memory.get("memory") or "").strip()
            if not memory_text:
                continue

            signal = _plan_conflict_signals(decision_text, memory_text)
            if signal["classification"] == "insufficient_evidence":
                continue

            matched.append({
                "memory_id": memory.get("id"),
                "subject": str(memory.get("subject") or "").strip(),
                "classification": signal["classification"],
                "reason": signal["reason"],
                "shared_terms": signal["shared_terms"],
                "change_signal": signal["change_signal"],
            })

        # Only surface comparisons that have deterministic lexical support.
        if matched:
            for item in matched:
                if item["classification"] == "potential_conflict":
                    potential_conflict_count += 1
                elif item["classification"] == "consistent":
                    consistent_count += 1
            comparisons.append({
                "decision_id": decision_id,
                "decision_subject": decision_subject,
                "comparisons": matched[:20],
            })
        else:
            insufficient_count += 1
            comparisons.append({
                "decision_id": decision_id,
                "decision_subject": decision_subject,
                "comparisons": [],
                "classification": "insufficient_evidence",
            })

    detected = bool(comparisons)
    if potential_conflict_count:
        status = "potential_conflict_detected"
        reason = "current_plan_contains_potential_conflict_signal"
    elif consistent_count:
        status = "consistent"
        reason = "current_plan_is_consistent_with_available_decision_evidence"
    else:
        status = "insufficient_evidence"
        reason = "current_plan_evidence_is_insufficient_for_comparison"

    return {
        "built": True,
        "detected": detected,
        "status": status,
        "reason": reason,
        "decision_count": len(decisions[:20]),
        "current_plan_count": len(memories[:30]),
        "consistent_count": consistent_count,
        "potential_conflict_count": potential_conflict_count,
        "insufficient_evidence_count": insufficient_count,
        "decision_ids": [
            item.get("id") for item in decisions[:20]
            if item.get("id") is not None
        ],
        "comparisons": comparisons,
        "recommendation_generated": False,
        "decision_modified": False,
        "memory_modified": False,
        "action_created": False,
    }


def build_decision_current_plan_conflict_trace(conflict_result):
    """Compact public Step 4N verification trace."""
    result = conflict_result if isinstance(conflict_result, dict) else {}
    return {
        "built": bool(result.get("built", False)),
        "detected": bool(result.get("detected", False)),
        "status": str(result.get("status") or "not_triggered"),
        "reason": str(result.get("reason") or "unknown"),
        "decision_count": int(result.get("decision_count", 0) or 0),
        "current_plan_count": int(result.get("current_plan_count", 0) or 0),
        "consistent_count": int(result.get("consistent_count", 0) or 0),
        "potential_conflict_count": int(result.get("potential_conflict_count", 0) or 0),
        "insufficient_evidence_count": int(result.get("insufficient_evidence_count", 0) or 0),
        "decision_ids": [
            item for item in result.get("decision_ids", [])
            if item is not None
        ][:20],
        "recommendation_generated": False,
        "decision_modified": False,
        "memory_modified": False,
        "action_created": False,
        "read_only": True,
    }


# ============================================================
# PHASE 7 — STEP 4O
# DECISION CHANGE DETECTION & EVOLUTION TRACKING
# ============================================================

def _decision_evolution_normalize_text(value):
    """Normalize decision text for deterministic evolution comparison."""
    text = str(value or "").lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _decision_evolution_tokens(value):
    """Return conservative content tokens used only for overlap checks."""
    normalized = _decision_evolution_normalize_text(value)
    return {
        token
        for token in normalized.split()
        if len(token) >= 4
    }


def _decision_evolution_text(decision):
    """Build comparison text strictly from persisted decision fields."""
    item = decision if isinstance(decision, dict) else {}
    parts = []

    for key in (
        "decision",
        "selected_option",
        "rationale",
    ):
        value = item.get(key)

        if isinstance(value, list):
            parts.extend(
                str(part).strip()
                for part in value
                if str(part or "").strip()
            )
        elif str(value or "").strip():
            parts.append(str(value).strip())

    return " ".join(parts).strip()


def _decision_evolution_subject(decision):
    """Return the strongest stored grouping label for a decision."""
    item = decision if isinstance(decision, dict) else {}

    for key in (
        "subject",
        "title",
        "session_title",
    ):
        value = str(item.get(key) or "").strip()

        if value:
            return _decision_evolution_normalize_text(value)

    return ""


def _decision_evolution_timestamp(decision):
    """Return a sortable stored timestamp without inventing one."""
    item = decision if isinstance(decision, dict) else {}

    for key in (
        "created_at",
        "recorded_at",
        "timestamp",
    ):
        value = item.get(key)

        if value is None:
            continue

        text = str(value).strip()

        if text:
            return text

    return ""


def _decision_evolution_id(decision):
    """Return a stable persisted decision ID when available."""
    item = decision if isinstance(decision, dict) else {}

    value = item.get("id")

    if value is None:
        value = item.get("decision_id")

    try:
        return int(value)
    except Exception:
        return None


def _classify_decision_evolution(previous, current):
    """
    Deterministically classify two persisted decisions.

    This function describes observable stored-data changes only.
    It never decides which version is correct.
    """
    previous_text = _decision_evolution_text(previous)
    current_text = _decision_evolution_text(current)

    previous_normalized = _decision_evolution_normalize_text(previous_text)
    current_normalized = _decision_evolution_normalize_text(current_text)

    if not previous_normalized or not current_normalized:
        return {
            "classification": "insufficient_evidence",
            "reason": "decision_text_missing",
            "shared_terms": [],
        }

    if previous_normalized == current_normalized:
        return {
            "classification": "reaffirmed",
            "reason": "decision_content_unchanged",
            "shared_terms": sorted(
                _decision_evolution_tokens(current_text)
            )[:20],
        }

    previous_tokens = _decision_evolution_tokens(previous_text)
    current_tokens = _decision_evolution_tokens(current_text)
    shared = previous_tokens.intersection(current_tokens)

    # A meaningful overlap indicates that the two records concern related
    # decision content; a changed normalized text indicates the later record
    # is not identical to the earlier one.
    if len(shared) >= 2:
        return {
            "classification": "modified",
            "reason": "related_decision_content_changed",
            "shared_terms": sorted(shared)[:20],
        }

    return {
        "classification": "insufficient_evidence",
        "reason": "insufficient_overlap_for_evolution_link",
        "shared_terms": sorted(shared)[:20],
    }


def detect_decision_change_evolution(recall_result):
    """
    Detect observable evolution among persisted decisions returned by Step 4K.

    Rules:
    - Only persisted decisions supplied by Step 4K are considered.
    - No decision is inferred from ordinary memories or plans.
    - No decision is changed, superseded, deleted, or recommended.
    - No database write occurs.
    """
    recall = recall_result if isinstance(recall_result, dict) else {}
    triggered = bool(recall.get("triggered", False))

    decisions = recall.get("decisions", [])
    decisions = [
        item for item in decisions
        if isinstance(item, dict)
    ][:50]

    if not triggered:
        return {
            "built": True,
            "detected": False,
            "status": "not_triggered",
            "reason": "not_a_decision_history_query",
            "decision_count": 0,
            "evolution_chain_count": 0,
            "reaffirmed_count": 0,
            "modified_count": 0,
            "superseded_count": 0,
            "insufficient_evidence_count": 0,
            "decision_ids": [],
            "chains": [],
        }

    if not decisions:
        return {
            "built": True,
            "detected": False,
            "status": "no_decisions",
            "reason": "no_persisted_decisions_available",
            "decision_count": 0,
            "evolution_chain_count": 0,
            "reaffirmed_count": 0,
            "modified_count": 0,
            "superseded_count": 0,
            "insufficient_evidence_count": 0,
            "decision_ids": [],
            "chains": [],
        }

    # Group only by an explicit stored subject/title. Decisions without a
    # grouping label are kept isolated rather than being guessed together.
    groups = {}

    for decision in decisions:
        subject = _decision_evolution_subject(decision)

        if not subject:
            continue

        groups.setdefault(subject, []).append(decision)

    chains = []
    reaffirmed_count = 0
    modified_count = 0
    superseded_count = 0
    insufficient_count = 0

    for subject, group in groups.items():
        if len(group) < 2:
            continue

        ordered = sorted(
            group,
            key=lambda item: (
                _decision_evolution_timestamp(item),
                _decision_evolution_id(item) or 0,
            ),
        )

        transitions = []

        for index in range(1, len(ordered)):
            previous = ordered[index - 1]
            current = ordered[index]

            classification = _classify_decision_evolution(
                previous,
                current,
            )

            transition = {
                "from_decision_id": _decision_evolution_id(previous),
                "to_decision_id": _decision_evolution_id(current),
                "classification": classification["classification"],
                "reason": classification["reason"],
                "shared_terms": classification["shared_terms"],
            }

            transitions.append(transition)

            if transition["classification"] == "reaffirmed":
                reaffirmed_count += 1
            elif transition["classification"] == "modified":
                modified_count += 1
            else:
                insufficient_count += 1

        # "Superseded" is deliberately not inferred from mere modification.
        # It requires an explicit stored status marker on the later decision.
        for index, transition in enumerate(transitions):
            current = ordered[index + 1]
            explicit_status = str(
                current.get("status")
                or current.get("decision_status")
                or ""
            ).strip().lower()

            if (
                transition["classification"] == "modified"
                and explicit_status in {
                    "superseded",
                    "replaced",
                }
            ):
                transition["classification"] = "superseded"
                transition["reason"] = (
                    "later_decision_explicitly_marked_superseded_or_replaced"
                )
                modified_count = max(0, modified_count - 1)
                superseded_count += 1

        if transitions:
            chains.append({
                "subject": subject,
                "decision_ids": [
                    _decision_evolution_id(item)
                    for item in ordered
                    if _decision_evolution_id(item) is not None
                ],
                "transitions": transitions,
            })

    detected = bool(chains)

    return {
        "built": True,
        "detected": detected,
        "status": "detected" if detected else "no_evolution_detected",
        "reason": (
            "decision_evolution_detected"
            if detected
            else "no_comparable_decision_versions_found"
        ),
        "decision_count": len(decisions),
        "evolution_chain_count": len(chains),
        "reaffirmed_count": reaffirmed_count,
        "modified_count": modified_count,
        "superseded_count": superseded_count,
        "insufficient_evidence_count": insufficient_count,
        "decision_ids": [
            _decision_evolution_id(item)
            for item in decisions
            if _decision_evolution_id(item) is not None
        ][:50],
        "chains": chains,
    }


def build_decision_change_evolution_trace(evolution_result):
    """Compact public Step 4O verification trace."""
    result = (
        evolution_result
        if isinstance(evolution_result, dict)
        else {}
    )

    return {
        "built": bool(result.get("built", False)),
        "detected": bool(result.get("detected", False)),
        "status": str(
            result.get("status")
            or "not_triggered"
        ),
        "reason": str(
            result.get("reason")
            or "unknown"
        ),
        "decision_count": int(
            result.get("decision_count", 0)
            or 0
        ),
        "evolution_chain_count": int(
            result.get("evolution_chain_count", 0)
            or 0
        ),
        "reaffirmed_count": int(
            result.get("reaffirmed_count", 0)
            or 0
        ),
        "modified_count": int(
            result.get("modified_count", 0)
            or 0
        ),
        "superseded_count": int(
            result.get("superseded_count", 0)
            or 0
        ),
        "insufficient_evidence_count": int(
            result.get("insufficient_evidence_count", 0)
            or 0
        ),
        "decision_ids": [
            item
            for item in result.get("decision_ids", [])
            if item is not None
        ][:50],
        "recommendation_generated": False,
        "decision_modified": False,
        "decision_deleted": False,
        "memory_modified": False,
        "action_created": False,
        "read_only": True,
    }


# ============================================================
# PHASE 7 — STEP 4P
# DECISION EVOLUTION GROUNDED CHANGE EXPLANATION
# ============================================================

def build_decision_evolution_grounded_answer(evolution_result):
    """Build a grounded explanation strictly from validated Step 4O output."""
    result = evolution_result if isinstance(evolution_result, dict) else {}
    detected = bool(result.get("detected", False))
    chains = [item for item in result.get("chains", []) if isinstance(item, dict)][:20]

    if not detected:
        status = str(result.get("status") or "not_triggered")
        reason = str(result.get("reason") or "no_evolution_detected")
        if status == "not_triggered":
            return {"built": True, "answered": False, "status": "not_triggered", "reason": reason, "answer": "", "decision_count": 0, "evolution_chain_count": 0, "decision_ids": [], "evidence": []}
        return {
            "built": True, "answered": True, "status": "no_change", "reason": reason,
            "answer": "No supported decision evolution was detected in the persisted decision records available for this request.",
            "decision_count": int(result.get("decision_count", 0) or 0),
            "evolution_chain_count": 0,
            "decision_ids": [item for item in result.get("decision_ids", []) if item is not None][:50],
            "evidence": [],
        }

    lines=["Here is the grounded decision evolution found in your persisted history:"]
    evidence=[]; decision_ids=[]
    for chain_index, chain in enumerate(chains, start=1):
        subject=str(chain.get("subject") or "").strip()
        decision_ids.extend([x for x in chain.get("decision_ids", []) if x is not None])
        lines.append("\\n"+str(chain_index)+". "+("Subject: "+subject if subject else "Decision evolution"))
        for tr in [x for x in chain.get("transitions", []) if isinstance(x,dict)][:20]:
            a=tr.get("from_decision_id"); b=tr.get("to_decision_id")
            cls=str(tr.get("classification") or "insufficient_evidence").strip()
            reason=str(tr.get("reason") or "").strip()
            lines.append("Decision #"+str(a)+" → Decision #"+str(b)+": "+cls+".")
            if reason: lines.append("Basis: "+reason+".")
            evidence.append({"source_type":"decision_history","from_decision_id":a,"to_decision_id":b,"classification":cls,"reason":reason,"subject":subject})
    unique=[]; seen=set()
    for x in decision_ids:
        if str(x) not in seen: seen.add(str(x)); unique.append(x)
    return {"built":True,"answered":bool(evidence),"status":"answered" if evidence else "empty","reason":"grounded_decision_evolution_explained" if evidence else "no_evolution_transitions_available","answer":"\\n".join(lines) if evidence else "","decision_count":int(result.get("decision_count",0) or 0),"evolution_chain_count":len(chains),"decision_ids":unique[:50],"evidence":evidence[:50]}


def validate_decision_evolution_grounded_answer(answer_result, evolution_result):
    """Reject any explanation containing a transition absent from Step 4O."""
    answer=answer_result if isinstance(answer_result,dict) else {}
    evolution=evolution_result if isinstance(evolution_result,dict) else {}
    allowed=set()
    for chain in evolution.get("chains",[]) or []:
        if not isinstance(chain,dict): continue
        for tr in chain.get("transitions",[]) or []:
            if not isinstance(tr,dict): continue
            allowed.add((str(tr.get("from_decision_id")),str(tr.get("to_decision_id")),str(tr.get("classification") or "insufficient_evidence")))
    valid=[]; invalid=[]
    for item in [x for x in answer.get("evidence",[]) if isinstance(x,dict)][:50]:
        marker=(str(item.get("from_decision_id")),str(item.get("to_decision_id")),str(item.get("classification") or "insufficient_evidence"))
        (valid if marker in allowed else invalid).append(item)
    answered=bool(answer.get("answered",False)); answer_text=str(answer.get("answer") or "").strip()
    verified=bool(answer.get("built",False) and (not answered or (bool(answer_text) and bool(valid) and not invalid)))
    return {"verified":verified,"answer":answer_text,"valid_evidence_count":len(valid),"invalid_evidence_count":len(invalid),"reason":"aligned" if verified else "invalid_or_missing_evolution_evidence"}


def build_decision_evolution_answer_trace(answer_result, verification_result):
    """Compact public Step 4P verification trace."""
    answer=answer_result if isinstance(answer_result,dict) else {}
    verification=verification_result if isinstance(verification_result,dict) else {}
    return {"built":bool(answer.get("built",False)),"answered":bool(answer.get("answered",False)),"verified":bool(verification.get("verified",False)),"status":str(answer.get("status") or "not_triggered"),"reason":str(verification.get("reason") or answer.get("reason") or "unknown"),"decision_count":int(answer.get("decision_count",0) or 0),"evolution_chain_count":int(answer.get("evolution_chain_count",0) or 0),"valid_evidence_count":int(verification.get("valid_evidence_count",0) or 0),"invalid_evidence_count":int(verification.get("invalid_evidence_count",0) or 0),"decision_ids":[x for x in answer.get("decision_ids",[]) if x is not None][:50],"fallback_used":False,"recommendation_generated":False,"decision_modified":False,"memory_modified":False,"action_created":False,"read_only":True}


# PHASE 7 — STEP 4Q
# DECISION OUTCOME CAPTURE & LEARNING LOOP
# ============================================================
#
# Purpose:
#   Close the decision loop without changing the historical decision.
#   A user may explicitly record what happened after a persisted decision,
#   what the observed outcome was, and what they learned.
#
# Safety / integrity rules:
#   - outcome is NEVER inferred from memories, plans, or conversation
#   - outcome is NEVER generated by the API
#   - only a persisted decision can receive an outcome
#   - explicit confirmation is required
#   - existing decision_history is immutable from Step 4Q
#   - no recommendation is generated
#   - no action is executed
#   - no existing 4A–4P trace is changed
# ============================================================


def ensure_decision_outcomes_table():
    """Create the append-only explicit decision outcome store."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS decision_outcomes
                (
                    id SERIAL PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    decision_id INTEGER NOT NULL,
                    outcome_status TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    expected_outcome TEXT DEFAULT '',
                    learning TEXT DEFAULT '',
                    confirmed BOOLEAN NOT NULL DEFAULT FALSE,
                    fingerprint TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, fingerprint)
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_decision_outcomes_user_decision_created
                ON decision_outcomes(user_id, decision_id, created_at DESC)
                """
            )
        conn.commit()


def _decision_outcome_fingerprint(
    user_id,
    decision_id,
    outcome_status,
    outcome,
    expected_outcome,
    learning,
):
    raw = "|".join([
        str(user_id or "").strip(),
        str(decision_id or "").strip(),
        str(outcome_status or "").strip().lower(),
        str(outcome or "").strip(),
        str(expected_outcome or "").strip(),
        str(learning or "").strip(),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def validate_decision_outcome_input(payload):
    """Validate only explicit user-supplied outcome information."""
    data = payload if isinstance(payload, dict) else {}

    try:
        decision_id = int(data.get("decision_id"))
    except Exception:
        decision_id = None

    status = str(data.get("outcome_status") or "").strip().lower()
    allowed_statuses = {
        "positive",
        "negative",
        "mixed",
        "neutral",
        "unknown",
    }

    outcome = str(data.get("outcome") or "").strip()
    expected = str(data.get("expected_outcome") or "").strip()
    learning = str(data.get("learning") or "").strip()
    confirmed = bool(data.get("confirmed", False))

    errors = []
    if decision_id is None or decision_id <= 0:
        errors.append("decision_id_required")
    if status not in allowed_statuses:
        errors.append("valid_outcome_status_required")
    if not outcome:
        errors.append("outcome_required")
    if not confirmed:
        errors.append("explicit_confirmation_required")

    return {
        "built": True,
        "accepted": not errors,
        "status": "accepted" if not errors else "not_ready",
        "reason": "explicit_outcome_validated" if not errors else errors[0],
        "decision_id": decision_id,
        "outcome_status": status,
        "outcome_present": bool(outcome),
        "expected_outcome_present": bool(expected),
        "learning_present": bool(learning),
        "explicit_confirmation": confirmed,
        "errors": errors,
        "outcome_recorded": False,
        "recommendation_generated": False,
        "decision_modified": False,
        "action_created": False,
        "read_only": True,
    }


def persist_decision_outcome(user_id, payload):
    """Persist an explicitly confirmed outcome for an existing decision."""
    validation = validate_decision_outcome_input(payload)
    if not validation.get("accepted"):
        return {
            **validation,
            "persisted": False,
            "duplicate": False,
            "outcome_id": None,
            "reason": validation.get("reason") or "outcome_not_validated",
        }

    decision_id = validation["decision_id"]
    status = validation["outcome_status"]
    outcome = str(payload.get("outcome") or "").strip()
    expected = str(payload.get("expected_outcome") or "").strip()
    learning = str(payload.get("learning") or "").strip()

    ensure_decision_history_table()
    ensure_decision_outcomes_table()

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id
                FROM decision_history
                WHERE user_id = %s AND id = %s
                LIMIT 1
                """,
                (str(user_id), decision_id),
            )
            decision_row = cur.fetchone()

            if not decision_row:
                return {
                    **validation,
                    "persisted": False,
                    "duplicate": False,
                    "outcome_id": None,
                    "reason": "decision_not_found",
                }

            fingerprint = _decision_outcome_fingerprint(
                user_id,
                decision_id,
                status,
                outcome,
                expected,
                learning,
            )

            cur.execute(
                """
                INSERT INTO decision_outcomes
                (
                    user_id,
                    decision_id,
                    outcome_status,
                    outcome,
                    expected_outcome,
                    learning,
                    confirmed,
                    fingerprint
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id, fingerprint) DO NOTHING
                RETURNING id
                """,
                (
                    str(user_id),
                    decision_id,
                    status,
                    outcome,
                    expected,
                    learning,
                    True,
                    fingerprint,
                ),
            )
            inserted = cur.fetchone()

            if inserted:
                outcome_id = int(inserted[0])
                duplicate = False
            else:
                cur.execute(
                    """
                    SELECT id
                    FROM decision_outcomes
                    WHERE user_id = %s AND fingerprint = %s
                    LIMIT 1
                    """,
                    (str(user_id), fingerprint),
                )
                existing = cur.fetchone()
                outcome_id = int(existing[0]) if existing else None
                duplicate = True

        conn.commit()

    return {
        **validation,
        "persisted": outcome_id is not None,
        "duplicate": duplicate,
        "outcome_id": outcome_id,
        "outcome_recorded": outcome_id is not None,
        "reason": (
            "decision_outcome_already_persisted"
            if duplicate
            else "explicit_decision_outcome_persisted"
        ),
        "read_only": False,
    }


def get_decision_outcomes(user_id, decision_id=None, limit=100):
    """Return explicitly recorded outcomes, newest first."""
    ensure_decision_outcomes_table()
    try:
        limit = int(limit)
    except Exception:
        limit = 100
    limit = max(1, min(200, limit))

    params = [str(user_id)]
    where = "WHERE o.user_id = %s"
    if decision_id is not None:
        try:
            decision_id = int(decision_id)
        except Exception:
            decision_id = None
        if decision_id is not None:
            where += " AND o.decision_id = %s"
            params.append(decision_id)

    params.append(limit)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    o.id,
                    o.decision_id,
                    d.title,
                    d.decision,
                    d.selected_option,
                    o.outcome_status,
                    o.outcome,
                    o.expected_outcome,
                    o.learning,
                    o.confirmed,
                    o.created_at
                FROM decision_outcomes o
                LEFT JOIN decision_history d
                  ON d.id = o.decision_id
                 AND d.user_id = o.user_id
                {where}
                ORDER BY o.created_at DESC, o.id DESC
                LIMIT %s
                """,
                tuple(params),
            )
            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "decision_id": row[1],
            "title": row[2],
            "decision": row[3],
            "selected_option": row[4],
            "outcome_status": row[5],
            "outcome": row[6],
            "expected_outcome": row[7],
            "learning": row[8],
            "confirmed": bool(row[9]),
            "created_at": row[10].isoformat() if row[10] else None,
        }
        for row in rows
    ]


def build_decision_outcome_trace(result):
    """Compact public Step 4Q trace."""
    data = result if isinstance(result, dict) else {}
    return {
        "built": bool(data.get("built", False)),
        "accepted": bool(data.get("accepted", False)),
        "persisted": bool(data.get("persisted", False)),
        "status": str(data.get("status") or "not_ready"),
        "reason": str(data.get("reason") or "unknown"),
        "decision_id": data.get("decision_id"),
        "outcome_id": data.get("outcome_id"),
        "duplicate": bool(data.get("duplicate", False)),
        "outcome_recorded": bool(data.get("outcome_recorded", False)),
        "outcome_status": str(data.get("outcome_status") or ""),
        "outcome_present": bool(data.get("outcome_present", False)),
        "expected_outcome_present": bool(data.get("expected_outcome_present", False)),
        "learning_present": bool(data.get("learning_present", False)),
        "explicit_confirmation": bool(data.get("explicit_confirmation", False)),
        "recommendation_generated": False,
        "decision_modified": False,
        "action_created": False,
        "read_only": bool(data.get("read_only", True)),
    }


def build_decision_outcome_history_trace(outcomes):
    """Deterministic UI-facing summary for saved outcome history."""
    items = outcomes if isinstance(outcomes, list) else []
    return {
        "built": True,
        "count": len(items),
        "outcome_ids": [x.get("id") for x in items if isinstance(x, dict) and x.get("id") is not None][:100],
        "decision_ids": [x.get("decision_id") for x in items if isinstance(x, dict) and x.get("decision_id") is not None][:100],
        "explicit_only": True,
        "inferred": False,
        "recommendation_generated": False,
        "decision_modified": False,
        "action_created": False,
        "read_only": True,
    }


# ============================================================
