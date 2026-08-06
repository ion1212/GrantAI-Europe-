import os
import json
from datetime import datetime, timezone
from typing import Any

import streamlit as st
from openai import OpenAI
from supabase import create_client

st.set_page_config(
    page_title="AI Evidence Autofill",
    page_icon="🧠",
    layout="wide",
)

st.title("🧠 Etapa 22 — AI Evidence Autofill")
st.caption(
    "Propune completări pentru cerințele din Etapa 21 folosind numai date deja existente "
    "în proiect. Sugestiile AI nu devin dovezi confirmate fără aprobarea explicită a utilizatorului."
)


# ---------------------------------------------------------------------
# Secrets / clients / authentication
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


def restore_auth_session(sb) -> None:
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


def norm(v):
    return str(v or "").strip().lower()


def first_value(row, names, default=""):
    if not isinstance(row, dict):
        return default
    for name in names:
        if row.get(name) not in (None, "", [], {}):
            return row.get(name)
    return default


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


def compact_json(obj: Any, limit: int = 24000) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)[:limit]


def safe_rows(query):
    try:
        data = query.execute().data
        return data if isinstance(data, list) else []
    except Exception:
        return []


try:
    supabase = get_supabase()
except Exception as exc:
    st.error(f"Supabase nu este configurat corect: {exc}")
    st.stop()

restore_auth_session(supabase)

user_id = current_user_id(supabase)
if not user_id:
    st.error("Intră în cont din pagina principală și revino.")
    st.stop()


# ---------------------------------------------------------------------
# Project / opportunity
# ---------------------------------------------------------------------
try:
    projects = (
        supabase.table("projects")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .execute()
    ).data or []
except Exception as exc:
    st.error(f"Nu am putut încărca proiectele: {exc}")
    st.stop()

if not projects:
    st.warning("Nu există proiecte disponibile.")
    st.stop()

project_labels = {
    f"{p.get('name') or p.get('title') or 'Project'} — {str(p.get('id'))[:8]}": p
    for p in projects
}
selected_label = st.selectbox("Project", list(project_labels.keys()))
project = project_labels[selected_label]
project_id = str(project["id"])

try:
    all_tasks = (
        supabase.table("resolution_tasks")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .order("created_at", desc=True)
        .execute()
    ).data or []
except Exception as exc:
    st.error(f"Nu am putut încărca resolution_tasks: {exc}")
    st.stop()

if not all_tasks:
    st.warning("Etapa 19 nu are task-uri pentru acest proiect.")
    st.stop()

opportunity_identity = str(all_tasks[0].get("opportunity_identity") or "")
tasks = [
    x for x in all_tasks
    if str(x.get("opportunity_identity") or "") == opportunity_identity
]

st.text_input("Oportunitate", value=opportunity_identity or "—", disabled=True)


# ---------------------------------------------------------------------
# Stage 21 requirements
# ---------------------------------------------------------------------
def needs_real_input(task):
    action = norm(first_value(task, [
        "resolution_type", "action_type", "task_type", "resolution_mode",
        "recommended_action", "action"
    ]))
    status = norm(first_value(task, ["status", "resolution_status"]))
    proposed = str(first_value(task, [
        "proposed_resolution", "proposed_text", "proposed_content",
        "resolution", "draft_resolution"
    ], "") or "").strip()

    return (
        "input" in action
        or "verify" in action
        or "official" in action
        or "waiting input" in status
        or "input provided" in status
        or (not proposed and status != "done")
    )


blocked_tasks = [t for t in tasks if needs_real_input(t)]

try:
    evidence_rows = (
        supabase.table("project_evidence_inputs")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_identity", opportunity_identity)
        .order("created_at", desc=True)
        .execute()
    ).data or []
except Exception:
    evidence_rows = []

latest_evidence_by_task = {}
for row in evidence_rows:
    tid = str(row.get("resolution_task_id") or "")
    if tid and tid not in latest_evidence_by_task:
        latest_evidence_by_task[tid] = row


# ---------------------------------------------------------------------
# Existing project context — only real stored data is supplied to AI
# ---------------------------------------------------------------------
context_tables = [
    "proposal_drafts",
    "proposal_sections",
    "grant_evaluations",
    "grant_compliance_checks",
    "submission_packs",
    "final_submission_validations",
    "resolution_executions",
]

project_context = {"project": project}

for table in context_tables:
    try:
        q = (
            supabase.table(table)
            .select("*")
            .eq("project_id", project_id)
            .order("created_at", desc=True)
            .limit(30)
        )
        data = q.execute().data or []
        exact = [
            r for r in data
            if not r.get("opportunity_identity")
            or str(r.get("opportunity_identity")) == opportunity_identity
        ]
        project_context[table] = exact[:10]
    except Exception:
        project_context[table] = []


# ---------------------------------------------------------------------
# AI suggestion
# ---------------------------------------------------------------------
def generate_autofill_suggestions(requirements):
    prompt = f"""
You are an EU grant evidence autofill assistant.

Your task is to inspect REAL PROJECT DATA ALREADY STORED in the application and
suggest possible values for Stage 21 evidence/input requirements.

STRICT SAFETY RULES:
- Never invent facts.
- Never invent budget values, partners, consortium members, TRL, eligibility,
  KPI values, certifications, legal facts, official rules, references or evidence.
- A suggestion may only be made when the supplied project context explicitly
  supports it.
- If the information is absent, ambiguous, inferred, outdated or requires an
  official source, return can_suggest=false.
- Never mark anything "Officially verified".
- AI suggestions are drafts only and require explicit user approval.
- Quote/source-reference the internal source as precisely as possible.
- Keep one output item per supplied requirement.
- Return valid JSON only.

Return exactly:
{{
  "summary": "",
  "items": [
    {{
      "resolution_task_id": "",
      "category": "",
      "field_name": "",
      "field_label": "",
      "can_suggest": true,
      "suggested_value": "",
      "source_type": "project_data",
      "source_reference": "",
      "reason": "",
      "confidence": "High|Medium|Low"
    }}
  ]
}}

STAGE 21 REQUIREMENTS:
{compact_json(requirements)}

EXISTING CONFIRMED/SAVED EVIDENCE:
{compact_json(evidence_rows)}

EXISTING PROJECT CONTEXT:
{compact_json(project_context)}
"""

    response = get_openai().responses.create(
        model=model_name(),
        instructions=(
            "Return JSON only. Use only supplied project data. "
            "If a fact is not explicitly supported, do not suggest it."
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
# Existing Stage 22 suggestions
# ---------------------------------------------------------------------
try:
    saved_suggestions = (
        supabase.table("evidence_autofill_suggestions")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_identity", opportunity_identity)
        .order("created_at", desc=True)
        .execute()
    ).data or []
except Exception:
    saved_suggestions = []

latest_suggestion_by_task = {}
for row in saved_suggestions:
    tid = str(row.get("resolution_task_id") or "")
    if tid and tid not in latest_suggestion_by_task:
        latest_suggestion_by_task[tid] = row

suggestable = sum(
    1 for x in latest_suggestion_by_task.values()
    if str(x.get("suggested_value") or "").strip()
)
approved = sum(
    1 for x in latest_suggestion_by_task.values()
    if norm(x.get("suggestion_status")) in ("approved", "accepted", "applied")
)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Cerințe Etapa 21", len(blocked_tasks))
c2.metric("Sugestii AI disponibile", suggestable)
c3.metric("Aprobate", approved)
c4.metric("Fără sugestie sigură", max(len(blocked_tasks) - suggestable, 0))

st.info(
    "Etapa 22 caută informații numai în datele deja existente ale proiectului. "
    "Nu confirmă eligibilitatea și nu transformă automat o sugestie AI într-o dovadă."
)


# ---------------------------------------------------------------------
# Generate and persist suggestions
# ---------------------------------------------------------------------
if st.button(
    "🧠 Analizează proiectul și propune autofill",
    type="primary",
    use_container_width=True,
    disabled=not blocked_tasks,
):
    requirements = []
    for task in blocked_tasks:
        requirements.append({
            "resolution_task_id": str(task.get("id")),
            "category": str(first_value(task, ["category"], "Other")),
            "field_label": str(first_value(
                task, ["issue_title", "task", "title", "issue", "description"], "Cerință"
            )),
            "required_input": str(first_value(
                task,
                ["required_input", "input_request", "reason", "issue_description", "description"],
                ""
            )),
            "status": task.get("status"),
            "resolution_type": task.get("resolution_type"),
        })

    with st.spinner("AI verifică datele deja existente în proiect..."):
        try:
            result = generate_autofill_suggestions(requirements)
            created = 0

            for item in result.get("items", []):
                tid = str(item.get("resolution_task_id") or "").strip()
                if not tid:
                    continue

                can_suggest = bool(item.get("can_suggest"))
                suggested_value = (
                    str(item.get("suggested_value") or "").strip()
                    if can_suggest else ""
                )

                confidence = str(item.get("confidence") or "Low")
                if confidence not in ("High", "Medium", "Low"):
                    confidence = "Low"

                payload = {
                    "user_id": user_id,
                    "project_id": project_id,
                    "opportunity_identity": opportunity_identity,
                    "resolution_task_id": tid,
                    "category": str(item.get("category") or "Other"),
                    "field_name": str(item.get("field_name") or item.get("category") or "Other"),
                    "field_label": str(item.get("field_label") or "Cerință"),
                    "suggested_value": suggested_value,
                    "source_type": str(item.get("source_type") or "project_data"),
                    "source_reference": str(item.get("source_reference") or ""),
                    "confidence": confidence,
                    "reason": str(item.get("reason") or ""),
                    "suggestion_status": "Proposed" if suggested_value else "Rejected",
                    "ai_result": item,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }

                supabase.table("evidence_autofill_suggestions").insert(payload).execute()
                created += 1

            run_payload = {
                "user_id": user_id,
                "project_id": project_id,
                "opportunity_identity": opportunity_identity,
                "total_requirements": len(requirements),
                "suggested_requirements": sum(
                    1 for i in result.get("items", [])
                    if i.get("can_suggest") and str(i.get("suggested_value") or "").strip()
                ),
                "unsuggested_requirements": sum(
                    1 for i in result.get("items", [])
                    if not i.get("can_suggest") or not str(i.get("suggested_value") or "").strip()
                ),
                "summary": {
                    "stage": 22,
                    "text": result.get("summary", ""),
                },
            }
            supabase.table("evidence_autofill_runs").insert(run_payload).execute()

            st.success(f"Analiza a fost salvată. {created} rezultate procesate.")
            st.rerun()
        except Exception as exc:
            st.error(f"Autofill-ul nu a putut fi generat: {exc}")


# Reload after possible generation.
try:
    saved_suggestions = (
        supabase.table("evidence_autofill_suggestions")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_identity", opportunity_identity)
        .order("created_at", desc=True)
        .execute()
    ).data or []
except Exception:
    saved_suggestions = []

latest_suggestion_by_task = {}
for row in saved_suggestions:
    tid = str(row.get("resolution_task_id") or "")
    if tid and tid not in latest_suggestion_by_task:
        latest_suggestion_by_task[tid] = row


# ---------------------------------------------------------------------
# Review / explicit approval
# ---------------------------------------------------------------------
st.subheader("Sugestii pentru completare")

if not blocked_tasks:
    st.success("Nu există cerințe blocate în Etapa 21.")
else:
    for pos, task in enumerate(blocked_tasks):
        task_id = str(task.get("id"))
        category = str(first_value(task, ["category"], "Other"))
        title = str(first_value(
            task, ["issue_title", "task", "title", "issue", "description"], "Cerință"
        ))
        required = str(first_value(
            task,
            ["required_input", "input_request", "reason", "issue_description", "description"],
            "Este necesară informație reală."
        ))

        suggestion = latest_suggestion_by_task.get(task_id, {})
        value = str(suggestion.get("suggested_value") or "")
        status = str(suggestion.get("suggestion_status") or "Not analysed")

        with st.expander(
            f"{'✨' if value else '🔒'} {category} — {title} [{status}]",
            expanded=(pos == 0),
        ):
            st.write(required)

            existing = latest_evidence_by_task.get(task_id)
            if existing and str(existing.get("value_text") or "").strip():
                st.success(
                    "Etapa 21 are deja o valoare salvată pentru această cerință: "
                    f"{existing.get('verification_status') or 'Unverified'}."
                )

            if not suggestion:
                st.caption("Nu există încă o analiză Autofill pentru această cerință.")
                continue

            if not value:
                st.warning(
                    "AI nu a găsit în proiect o informație suficient de sigură pentru autofill."
                )
                if suggestion.get("reason"):
                    st.write(f"**Motiv:** {suggestion.get('reason')}")
                continue

            proposed_value = st.text_area(
                "Valoare sugerată",
                value=value,
                height=160,
                key=f"autofill_value_{pos}_{task_id}",
            )

            st.write(
                f"**Sursă internă:** {suggestion.get('source_reference') or 'Nespecificată'}  \n"
                f"**Încredere AI:** {suggestion.get('confidence') or 'Low'}"
            )
            if suggestion.get("reason"):
                st.caption(str(suggestion.get("reason")))

            approve = st.checkbox(
                "Am verificat această informație și aprob salvarea ei în Etapa 21",
                key=f"approve_autofill_{pos}_{task_id}",
            )

            if st.button(
                "✅ Aprobă și trimite în Etapa 21",
                key=f"apply_autofill_{pos}_{task_id}",
                use_container_width=True,
                disabled=not approve or not proposed_value.strip(),
            ):
                try:
                    evidence_payload = {
                        "user_id": user_id,
                        "project_id": project_id,
                        "opportunity_identity": opportunity_identity,
                        "resolution_task_id": task_id,
                        "category": category,
                        "field_name": str(suggestion.get("field_name") or category),
                        "field_label": title,
                        "value_text": proposed_value.strip(),
                        "value_json": {},
                        "source_type": "project_data",
                        "source_reference": str(suggestion.get("source_reference") or "").strip(),
                        # Explicit approval means user-confirmed, never officially verified.
                        "verification_status": "User confirmed",
                        "confidence": str(suggestion.get("confidence") or "Medium"),
                        "notes": "Precompletat în Etapa 22 și aprobat explicit de utilizator.",
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }

                    supabase.table("project_evidence_inputs").insert(evidence_payload).execute()

                    supabase.table("evidence_autofill_suggestions").update({
                        "suggested_value": proposed_value.strip(),
                        "suggestion_status": "Applied",
                        "approved_at": datetime.now(timezone.utc).isoformat(),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }).eq("id", suggestion["id"]).eq("user_id", user_id).execute()

                    supabase.table("resolution_tasks").update({
                        "status": "Input provided"
                    }).eq("id", task_id).eq("user_id", user_id).execute()

                    st.success(
                        "Sugestia aprobată a fost salvată în Etapa 21 ca «User confirmed»."
                    )
                    st.rerun()
                except Exception as exc:
                    st.error(f"Nu am putut aplica sugestia: {exc}")


# ---------------------------------------------------------------------
# History
# ---------------------------------------------------------------------
st.divider()
with st.expander("Istoric Evidence Autofill"):
    try:
        hist = (
            supabase.table("evidence_autofill_suggestions")
            .select(
                "id,category,field_label,source_type,confidence,"
                "suggestion_status,created_at,approved_at"
            )
            .eq("user_id", user_id)
            .eq("project_id", project_id)
            .eq("opportunity_identity", opportunity_identity)
            .order("created_at", desc=True)
            .limit(100)
            .execute()
        ).data or []

        if hist:
            st.dataframe(hist, use_container_width=True, hide_index=True)
        else:
            st.caption("Nu există încă sugestii Autofill salvate.")
    except Exception as exc:
        st.error(f"Nu am putut încărca istoricul: {exc}")

st.caption(
    "Etapa 22 nu inventează date și nu declară o informație «Officially verified». "
    "Autofill-ul este doar o sugestie bazată pe date existente și necesită aprobare explicită."
)
