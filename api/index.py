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
    temperature=0.2
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

def save_memory(
    user_id,
    memory,
    category="general",
    importance=5,
    subject="general",
    session_id="default"
):

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

                exact = cur.fetchone()

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

            row = cur.fetchone()

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
    }


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
            # SYSTEM PROMPT
            # ------------------------------------------------

            system_prompt = f"""
You are Dusra Brain, a personal AI brain and memory assistant.

Current session:
{session_id}

Current session title:
{title}

MEMORY RULES:

1. Stored memories are facts explicitly provided by the user.

2. Never invent personal facts.

3. Prefer memories from the current session when available.

4. If a question clearly refers to a known subject,
use ALL relevant memories for that subject, even if
those memories were created in another conversation.

5. Do not mix unrelated project or business memories.

6. Use the current conversation history.

7. Use structured entities and relationships when they
are relevant to the user's question.

8. Do not claim to remember something that is not available.

9. If information is missing, say you do not have enough
stored information.

10. Keep answers natural and useful.

STORED MEMORIES:

{memory_text}

STRUCTURED ENTITIES:

{entity_text}

STRUCTURED RELATIONSHIPS:

{relationship_text}

CURRENT CONVERSATION:

{history_text}
"""


            # ------------------------------------------------
            # GROQ RESPONSE
            # ------------------------------------------------

            response = groq_request(
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
