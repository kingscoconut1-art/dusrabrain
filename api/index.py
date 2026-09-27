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
