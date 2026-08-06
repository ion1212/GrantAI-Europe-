import os
import json
from datetime import datetime, timezone
from typing import Any

import streamlit as st
from openai import OpenAI
from supabase import create_client

st.set_page_config(
    page_title="Etapa 19 — AI Resolution Agent",
    page_icon="🧩",
    layout="wide",
)

st.title("🧩 Etapa 19 — AI Resolution Agent")
st.caption(
    "Transformă problemele blocante din Submission Readiness în rezolvări propuse, "
    "cereri de input sau verificări oficiale, fără să inventeze date."
)


# ---------------------------------------------------------------------
# Secrets / clients
# ---------------------------------------------------------------------
def secret(name: str, default: str = "") -> str:
    try:
        return str(st.secrets.get(name, default))
    except Exception:
        return os.getenv(name, default)


@st.cache_resource
def get_supabase():
    return create_client(
        secret("SUPABASE_URL"),
        secret("SUPABASE_KEY") or secret("SUPABASE_ANON_KEY"),
    )


@st.cache_resource
def get_openai():
    return OpenAI(api_key=secret("OPENAI_API_KEY"))


def model_name() -> str:
    return secret("OPENAI_MODEL", "gpt-4.1-mini")


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def rows(resp):
    data = getattr(resp, "data", None)
    return data if isinstance(data, list) else []


def restore_auth_session(sb) -> None:
    """Restore the Supabase JWT session created on the main app page."""
    session = st.session_state.get("auth_session")
    if not session:
        return

    access_token = (
        session.get("access_token")
        if isinstance(session, dict)
        else getattr(session, "access_token", None)
    )
    refresh_token = (
        session.get("refresh_token")
        if isinstance(session, dict)
        else getattr(session, "refresh_token", None)
    )

    if access_token and refresh_token:
        try:
            sb.auth.set_session(access_token, refresh_token)
        except Exception:
            pass


def current_user_id(sb) -> str | None:
    # Main app stores the logged-in user under "auth_user".
    for key in ("auth_user", "user"):
        user = st.session_state.get(key)
        if isinstance(user, dict) and user.get("id"):
            return str(user["id"])
        if getattr(user, "id", None):
            return str(user.id)

    for key in ("user_id", "auth_user_id"):
        value = st.session_state.get(key)
        if value:
            return str(value)

    try:
        user = sb.auth.get_user().user
        if user and getattr(user, "id", None):
            return str(user.id)
    except Exception:
        pass

    return None


def safe_query(sb, table, *, filters=None, order=None, limit=None):
    try:
        q = sb.table(table).select("*")
        for key, value in (filters or {}).items():
            if value not in (None, ""):
                q = q.eq(key, value)
        if order:
            q = q.order(order[0], desc=order[1])
        if limit:
            q = q.limit(limit)
        return rows(q.execute())
    except Exception:
        return []


def latest(sb, table, *, project_id=None, opportunity_identity=None):
    filters = {}
    if project_id:
        filters["project_id"] = project_id

    data = safe_query(
        sb,
        table,
        filters=filters,
        order=("created_at", True),
        limit=100,
    )
    if not data:
        return None

    if opportunity_identity:
        exact = [
            row for row in data
            if str(row.get("opportunity_identity") or "")
            == str(opportunity_identity)
        ]
        if exact:
            return exact[0]

    return data[0]


def first_value(record: dict[str, Any] | None, keys, default=None):
    if not isinstance(record, dict):
        return default
    for key in keys:
        value = record.get(key)
        if value not in (None, "", [], {}):
            return value
    return default


def project_label(project):
    name = first_value(
        project,
        ["name", "project_name", "title"],
        "Project",
    )
    return f"{name} — {str(project.get('id', ''))[:8]}"


def opportunity_identity_from(row):
    if not isinstance(row, dict):
        return ""
    return str(
        first_value(
            row,
            [
                "opportunity_identity",
                "identity",
                "opportunity_id",
                "call_id",
                "identifier",
                "code",
                "id",
            ],
            "",
        )
        or ""
    )


def opportunity_label(row):
    return str(
        first_value(
            row,
            ["title", "name", "opportunity_name", "call_title"],
            "Funding opportunity",
        )
    )


def clean_json(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = (
            text.replace("```json", "", 1)
            .replace("```JSON", "", 1)
            .replace("```", "")
            .strip()
        )
    return text


def compact_json(obj: Any, limit: int = 14000) -> str:
    return json.dumps(
        obj,
        ensure_ascii=False,
        default=str,
    )[:limit]


def readiness_item_title(item):
    return str(
        first_value(
            item,
            ["title", "task", "description"],
            "Problemă de rezolvat",
        )
    )


def is_unresolved(item):
    status = str(item.get("status") or "").strip().lower()
    return status not in ("done", "completed", "resolved", "closed")


def is_blocking(item):
    status = str(item.get("status") or "").strip().lower()
    return bool(item.get("blocking")) or status == "blocked"


def fingerprint(item):
    return (
        str(item.get("readiness_item_id") or ""),
        str(item.get("issue_title") or "").strip().lower(),
    )


# ---------------------------------------------------------------------
# AI
# ---------------------------------------------------------------------
def classify_and_resolve(
    project,
    opportunity,
    readiness_items,
    reviewer,
    compliance,
    final_validation,
):
    prompt = f"""
You are an EU grant resolution agent.

Your job is to transform unresolved submission-readiness issues into safe,
actionable resolution tasks.

CRITICAL RULES:
- Never invent official eligibility rules, deadlines, budget ceilings, TRL,
  partners, KPI values, certifications, evidence, legal facts or applicant facts.
- If a fact is missing and only the user can know it, use resolution_type
  "request_input".
- If official call documents must be checked, use resolution_type "verify".
- If existing proposal text can be improved without inventing facts, use
  "rewrite" or "generate".
- For rewrite/generate, proposed_resolution must be usable draft text but must
  preserve placeholders such as [TO CONFIRM] for unverified facts.
- Keep one output item per input readiness issue.
- Return valid JSON only.

Return exactly:
{{
  "summary": "",
  "items": [
    {{
      "readiness_item_id": "",
      "source_stage": "stage16",
      "category": "",
      "issue_title": "",
      "issue_description": "",
      "resolution_type": "rewrite|generate|verify|request_input|manual",
      "proposed_resolution": "",
      "required_input": "",
      "target_section": "",
      "confidence": "High|Medium|Low",
      "status": "Proposed|Waiting input"
    }}
  ]
}}

PROJECT:
{compact_json(project)}

OPPORTUNITY:
{compact_json(opportunity)}

UNRESOLVED READINESS ITEMS:
{compact_json(readiness_items)}

LATEST REVIEWER:
{compact_json(reviewer or {})}

LATEST COMPLIANCE:
{compact_json(compliance or {})}

LATEST FINAL VALIDATION:
{compact_json(final_validation or {})}
"""

    response = get_openai().responses.create(
        model=model_name(),
        instructions=(
            "Return JSON only. Do not fabricate facts. "
            "Classify every issue into a safe resolution action."
        ),
        input=prompt,
    )

    result = json.loads(clean_json(response.output_text))
    if not isinstance(result, dict):
        raise ValueError("Răspunsul AI nu este un obiect JSON.")

    result.setdefault("summary", "")
    result.setdefault("items", [])
    return result


# ---------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------
ALLOWED_SOURCE_STAGES = {
    "stage13",
    "stage14",
    "stage15",
    "stage16",
    "stage18",
    "manual",
}
ALLOWED_TYPES = {
    "rewrite",
    "generate",
    "verify",
    "request_input",
    "manual",
}
ALLOWED_CONFIDENCE = {"High", "Medium", "Low"}
ALLOWED_STATUS = {
    "Open",
    "Proposed",
    "Waiting input",
    "Applied",
    "Rejected",
    "Done",
}


def save_resolution_run(
    sb,
    uid,
    project_id,
    identity,
    result,
):
    items = result.get("items", [])
    auto_resolvable = sum(
        1 for item in items
        if item.get("resolution_type") in ("rewrite", "generate")
    )
    requires_input = sum(
        1 for item in items
        if item.get("resolution_type") == "request_input"
    )
    requires_verification = sum(
        1 for item in items
        if item.get("resolution_type") == "verify"
    )

    payload = {
        "user_id": uid,
        "project_id": project_id,
        "opportunity_identity": identity,
        "total_issues": len(items),
        "auto_resolvable": auto_resolvable,
        "requires_user_input": requires_input,
        "requires_official_verification": requires_verification,
        "summary": {
            "text": result.get("summary", ""),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
    }

    return rows(
        sb.table("resolution_runs")
        .insert(payload)
        .execute()
    )


def save_resolution_tasks(
    sb,
    uid,
    project_id,
    identity,
    result,
):
    existing = safe_query(
        sb,
        "resolution_tasks",
        filters={
            "user_id": uid,
            "project_id": project_id,
            "opportunity_identity": identity,
        },
        limit=500,
    )

    existing_by_readiness = {
        str(item.get("readiness_item_id")): item
        for item in existing
        if item.get("readiness_item_id")
    }

    created = 0
    updated = 0

    for item in result.get("items", []):
        readiness_item_id = str(item.get("readiness_item_id") or "").strip()
        if not readiness_item_id:
            continue

        source_stage = item.get("source_stage") or "stage16"
        if source_stage not in ALLOWED_SOURCE_STAGES:
            source_stage = "stage16"

        resolution_type = item.get("resolution_type") or "manual"
        if resolution_type not in ALLOWED_TYPES:
            resolution_type = "manual"

        confidence = item.get("confidence") or "Medium"
        if confidence not in ALLOWED_CONFIDENCE:
            confidence = "Medium"

        status = item.get("status") or (
            "Waiting input"
            if resolution_type in ("verify", "request_input")
            else "Proposed"
        )
        if status not in ALLOWED_STATUS:
            status = "Proposed"

        payload = {
            "user_id": uid,
            "project_id": project_id,
            "opportunity_identity": identity,
            "readiness_item_id": readiness_item_id,
            "source_stage": source_stage,
            "category": str(item.get("category") or "Other"),
            "issue_title": str(
                item.get("issue_title")
                or "Problemă de rezolvat"
            )[:500],
            "issue_description": str(
                item.get("issue_description") or ""
            ),
            "resolution_type": resolution_type,
            "proposed_resolution": str(
                item.get("proposed_resolution") or ""
            ),
            "required_input": str(
                item.get("required_input") or ""
            ),
            "target_section": str(
                item.get("target_section") or ""
            ),
            "confidence": confidence,
            "status": status,
            "ai_result": item,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        old = existing_by_readiness.get(readiness_item_id)
        if old:
            sb.table("resolution_tasks").update(
                payload
            ).eq("id", old["id"]).eq("user_id", uid).execute()
            updated += 1
        else:
            sb.table("resolution_tasks").insert(payload).execute()
            created += 1

    return created, updated


def update_resolution_task(
    sb,
    uid,
    task_id,
    status,
    proposed_resolution,
    required_input,
):
    sb.table("resolution_tasks").update(
        {
            "status": status,
            "proposed_resolution": proposed_resolution,
            "required_input": required_input,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    ).eq("id", task_id).eq("user_id", uid).execute()


def update_readiness_status(
    sb,
    uid,
    readiness_item_id,
    status,
    notes="",
):
    payload = {
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if notes:
        payload["resolution_notes"] = notes

    sb.table("submission_readiness_items").update(
        payload
    ).eq("id", readiness_item_id).eq("user_id", uid).execute()


# ---------------------------------------------------------------------
# App context
# ---------------------------------------------------------------------
try:
    sb = get_supabase()
except Exception as exc:
    st.error(f"Supabase nu este configurat corect: {exc}")
    st.stop()

# Reuse the login session created by app.py so RLS-authenticated queries work.
restore_auth_session(sb)

uid = current_user_id(sb)
if not uid:
    st.error("Intră în cont din pagina principală și revino.")
    st.stop()

projects = safe_query(
    sb,
    "projects",
    filters={"user_id": uid},
    order=("created_at", True),
    limit=100,
)
if not projects:
    projects = safe_query(
        sb,
        "projects",
        order=("created_at", True),
        limit=100,
    )

if not projects:
    st.error("Nu există proiecte disponibile.")
    st.stop()

project = st.selectbox(
    "Project",
    projects,
    format_func=project_label,
)
project_id = str(project["id"])

# Same fallback strategy as Stage 18.
opportunities = []
for table in ("funding_opportunities", "opportunities"):
    opportunities = safe_query(
        sb,
        table,
        filters={"project_id": project_id},
        order=("created_at", True),
        limit=100,
    )
    if opportunities:
        break

pack_latest = latest(
    sb,
    "submission_packs",
    project_id=project_id,
)
pack_identity = opportunity_identity_from(pack_latest or {})

if opportunities:
    opportunity = st.selectbox(
        "Oportunitate",
        opportunities,
        format_func=opportunity_label,
    )
    identity = opportunity_identity_from(opportunity)
else:
    opportunity = {}
    identity = pack_identity or "Funding opportunity"
    st.text_input(
        "Oportunitate",
        value=identity,
        disabled=True,
    )

if not identity:
    identity = pack_identity or "Funding opportunity"

if pack_identity and identity != pack_identity:
    st.info(
        "Identificatorul oportunității diferă între module; "
        "se folosesc cele mai recente date disponibile pentru proiect."
    )

readiness_items = safe_query(
    sb,
    "submission_readiness_items",
    filters={"project_id": project_id},
    order=("created_at", True),
    limit=500,
)

# Prefer exact opportunity identity if it exists.
exact_items = [
    item for item in readiness_items
    if str(item.get("opportunity_identity") or "") == str(identity)
]
if exact_items:
    readiness_items = exact_items

unresolved = [
    item for item in readiness_items
    if is_unresolved(item)
]

blocking = [
    item for item in unresolved
    if is_blocking(item)
]

reviewer = latest(
    sb,
    "grant_evaluations",
    project_id=project_id,
    opportunity_identity=identity,
)
compliance = latest(
    sb,
    "grant_compliance_checks",
    project_id=project_id,
    opportunity_identity=identity,
)
final_validation = latest(
    sb,
    "final_submission_validations",
    project_id=project_id,
    opportunity_identity=identity,
)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Probleme nerezolvate", len(unresolved))
c2.metric("Blocante", len(blocking))
c3.metric(
    "Reviewer",
    f"{first_value(reviewer, ['overall_score'], 0)}/100"
    if reviewer else "—",
)
c4.metric(
    "Final validation",
    str(
        first_value(
            final_validation,
            ["validation_status"],
            "—",
        )
    ).upper(),
)

if not unresolved:
    st.success(
        "Nu există probleme nerezolvate în Submission Readiness."
    )
else:
    st.warning(
        f"Agentul are {len(unresolved)} probleme de analizat, "
        f"dintre care {len(blocking)} sunt blocante."
    )

# ---------------------------------------------------------------------
# Generate/update resolution plan
# ---------------------------------------------------------------------
if st.button(
    "🧠 Analizează și generează planul de rezolvare",
    type="primary",
    use_container_width=True,
    disabled=not unresolved,
):
    with st.spinner(
        "AI clasifică problemele și pregătește rezolvările..."
    ):
        try:
            result = classify_and_resolve(
                project,
                opportunity,
                unresolved,
                reviewer,
                compliance,
                final_validation,
            )
            created, updated = save_resolution_tasks(
                sb,
                uid,
                project_id,
                identity,
                result,
            )
            save_resolution_run(
                sb,
                uid,
                project_id,
                identity,
                result,
            )
            st.session_state["stage19_last_summary"] = result.get(
                "summary",
                "",
            )
            st.success(
                f"Plan generat: {created} task-uri noi, "
                f"{updated} task-uri actualizate."
            )
            st.rerun()
        except Exception as exc:
            st.error(f"Nu am putut genera planul: {exc}")

# ---------------------------------------------------------------------
# Load saved resolution tasks
# ---------------------------------------------------------------------
resolution_tasks = safe_query(
    sb,
    "resolution_tasks",
    filters={
        "user_id": uid,
        "project_id": project_id,
    },
    order=("created_at", True),
    limit=500,
)

exact_resolution_tasks = [
    item for item in resolution_tasks
    if str(item.get("opportunity_identity") or "") == str(identity)
]
if exact_resolution_tasks:
    resolution_tasks = exact_resolution_tasks

if resolution_tasks:
    auto_count = sum(
        1 for item in resolution_tasks
        if item.get("resolution_type") in ("rewrite", "generate")
        and item.get("status") not in ("Done", "Rejected")
    )
    input_count = sum(
        1 for item in resolution_tasks
        if item.get("resolution_type") == "request_input"
        and item.get("status") not in ("Done", "Rejected")
    )
    verify_count = sum(
        1 for item in resolution_tasks
        if item.get("resolution_type") == "verify"
        and item.get("status") not in ("Done", "Rejected")
    )

    st.subheader("Plan de rezolvare")
    a, b, c = st.columns(3)
    a.metric("Rezolvabile cu draft AI", auto_count)
    b.metric("Necesită input", input_count)
    c.metric("Verificare oficială", verify_count)

    tabs = st.tabs(
        [
            "Toate",
            "AI poate propune",
            "Necesită input",
            "Verificare oficială",
        ]
    )

    groups = [
        resolution_tasks,
        [
            x for x in resolution_tasks
            if x.get("resolution_type") in ("rewrite", "generate")
        ],
        [
            x for x in resolution_tasks
            if x.get("resolution_type") == "request_input"
        ],
        [
            x for x in resolution_tasks
            if x.get("resolution_type") == "verify"
        ],
    ]

    for tab, group in zip(tabs, groups):
        with tab:
            if not group:
                st.info("Nu există task-uri în această categorie.")
                continue

            for task in group:
                rtype = task.get("resolution_type") or "manual"
                icon = {
                    "rewrite": "✍️",
                    "generate": "✨",
                    "verify": "🔎",
                    "request_input": "🙋",
                    "manual": "🛠️",
                }.get(rtype, "🛠️")

                label = (
                    f"{icon} {task.get('category') or 'Other'} — "
                    f"{task.get('issue_title') or 'Task'} "
                    f"[{task.get('status')}]"
                )

                with st.expander(label):
                    st.write(
                        task.get("issue_description")
                        or "Fără descriere suplimentară."
                    )

                    st.write(
                        f"**Tip rezolvare:** `{rtype}`  "
                        f"**Încredere:** `{task.get('confidence') or 'Medium'}`"
                    )

                    if task.get("target_section"):
                        st.write(
                            f"**Secțiune țintă:** "
                            f"{task.get('target_section')}"
                        )

                    proposed = st.text_area(
                        "Rezolvare propusă",
                        value=task.get("proposed_resolution") or "",
                        height=180,
                        key=f"proposed_{task['id']}",
                    )

                    required_input = st.text_area(
                        "Input / verificare necesară",
                        value=task.get("required_input") or "",
                        height=100,
                        key=f"input_{task['id']}",
                    )

                    status_options = [
                        "Open",
                        "Proposed",
                        "Waiting input",
                        "Applied",
                        "Rejected",
                        "Done",
                    ]
                    current_status = task.get("status") or "Open"
                    status = st.selectbox(
                        "Status Resolution Task",
                        status_options,
                        index=(
                            status_options.index(current_status)
                            if current_status in status_options
                            else 0
                        ),
                        key=f"resolution_status_{task['id']}",
                    )

                    x1, x2, x3 = st.columns(3)

                    if x1.button(
                        "Salvează",
                        key=f"save_resolution_{task['id']}",
                        use_container_width=True,
                    ):
                        try:
                            update_resolution_task(
                                sb,
                                uid,
                                task["id"],
                                status,
                                proposed,
                                required_input,
                            )
                            st.success("Task-ul de rezolvare a fost salvat.")
                            st.rerun()
                        except Exception as exc:
                            st.error(str(exc))

                    readiness_item_id = task.get(
                        "readiness_item_id"
                    )

                    if x2.button(
                        "Marchează în lucru",
                        key=f"in_progress_{task['id']}",
                        use_container_width=True,
                        disabled=not readiness_item_id,
                    ):
                        try:
                            update_resolution_task(
                                sb,
                                uid,
                                task["id"],
                                "Applied",
                                proposed,
                                required_input,
                            )
                            update_readiness_status(
                                sb,
                                uid,
                                readiness_item_id,
                                "In progress",
                                "Rezolvare propusă în Etapa 19.",
                            )
                            st.success(
                                "Problema a fost marcată In progress "
                                "în Submission Readiness."
                            )
                            st.rerun()
                        except Exception as exc:
                            st.error(str(exc))

                    if x3.button(
                        "Confirm rezolvat",
                        key=f"done_{task['id']}",
                        use_container_width=True,
                        disabled=not readiness_item_id,
                    ):
                        if rtype in ("verify", "request_input") and not required_input.strip():
                            st.warning(
                                "Pentru problemele care necesită verificare/input, "
                                "notează mai întâi ce ai confirmat."
                            )
                        else:
                            try:
                                update_resolution_task(
                                    sb,
                                    uid,
                                    task["id"],
                                    "Done",
                                    proposed,
                                    required_input,
                                )
                                update_readiness_status(
                                    sb,
                                    uid,
                                    readiness_item_id,
                                    "Done",
                                    required_input.strip()
                                    or proposed.strip()
                                    or "Confirmat ca rezolvat în Etapa 19.",
                                )
                                st.success(
                                    "Problema a fost marcată Done. "
                                    "Recalculează apoi Etapa 16 și Etapa 18."
                                )
                                st.rerun()
                            except Exception as exc:
                                st.error(str(exc))

else:
    st.info(
        "Nu există încă un plan de rezolvare. "
        "Apasă «Analizează și generează planul de rezolvare»."
    )

# ---------------------------------------------------------------------
# History
# ---------------------------------------------------------------------
st.divider()
with st.expander("Istoric Resolution Runs"):
    runs = safe_query(
        sb,
        "resolution_runs",
        filters={
            "user_id": uid,
            "project_id": project_id,
        },
        order=("created_at", True),
        limit=30,
    )

    exact_runs = [
        run for run in runs
        if str(run.get("opportunity_identity") or "") == str(identity)
    ]
    if exact_runs:
        runs = exact_runs

    if not runs:
        st.info("Nu există încă rulări salvate.")
    else:
        for run in runs:
            st.write(
                f"**{run.get('created_at')}** — "
                f"{run.get('total_issues', 0)} probleme · "
                f"{run.get('auto_resolvable', 0)} AI · "
                f"{run.get('requires_user_input', 0)} input · "
                f"{run.get('requires_official_verification', 0)} verificări oficiale"
            )

st.caption(
    "Resolution Agent nu confirmă eligibilitatea și nu inventează date. "
    "Task-urile de tip «verify» și «request_input» trebuie validate înainte "
    "de marcarea lor ca Done."
)
