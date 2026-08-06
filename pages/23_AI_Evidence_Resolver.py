import os
import json
from datetime import datetime, timezone
from typing import Any

import streamlit as st
from openai import OpenAI
from supabase import create_client

st.set_page_config(
    page_title="AI Evidence Resolver",
    page_icon="🧩",
    layout="wide",
)

st.title("🧩 Etapa 23 — AI Evidence Resolver")
st.caption(
    "Transformă dovezile reale salvate în Etapa 21 și sugestiile aprobate din Etapa 22 "
    "în propuneri de rezolvare pentru task-urile blocate. Nu inventează date și nu marchează "
    "automat un task ca rezolvat."
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


def compact_json(obj: Any, limit: int = 28000) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)[:limit]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    st.warning("Nu există resolution_tasks pentru acest proiect.")
    st.stop()

opportunity_identity = str(all_tasks[0].get("opportunity_identity") or "")
tasks = [
    x for x in all_tasks
    if str(x.get("opportunity_identity") or "") == opportunity_identity
]

st.text_input("Oportunitate", value=opportunity_identity or "—", disabled=True)


# ---------------------------------------------------------------------
# Inputs from Stage 21 + approved/applied Stage 22 suggestions
# ---------------------------------------------------------------------
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

try:
    autofill_rows = (
        supabase.table("evidence_autofill_suggestions")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_identity", opportunity_identity)
        .order("created_at", desc=True)
        .execute()
    ).data or []
except Exception:
    autofill_rows = []

latest_autofill_by_task = {}
for row in autofill_rows:
    tid = str(row.get("resolution_task_id") or "")
    if tid and tid not in latest_autofill_by_task:
        latest_autofill_by_task[tid] = row


def task_needs_resolution(task):
    status = norm(first_value(task, ["status", "resolution_status"]))
    proposed = str(first_value(task, [
        "proposed_resolution", "proposed_text", "proposed_content",
        "resolution", "draft_resolution"
    ], "") or "").strip()

    return status not in ("done", "completed", "resolved", "executed") or not proposed


candidate_tasks = [t for t in tasks if task_needs_resolution(t)]

resolvable_tasks = []
for task in candidate_tasks:
    tid = str(task.get("id") or "")
    evidence = latest_evidence_by_task.get(tid, {})
    value = str(first_value(evidence, ["value_text", "value", "text"], "") or "").strip()

    if value:
        resolvable_tasks.append(task)
        continue

    suggestion = latest_autofill_by_task.get(tid, {})
    suggestion_status = norm(suggestion.get("suggestion_status"))
    suggested_value = str(suggestion.get("suggested_value") or "").strip()

    if suggestion_status in ("approved", "accepted", "applied") and suggested_value:
        resolvable_tasks.append(task)


# ---------------------------------------------------------------------
# Existing Stage 23 inputs/runs
# ---------------------------------------------------------------------
try:
    saved_resolutions = (
        supabase.table("evidence_resolution_inputs")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_identity", opportunity_identity)
        .order("created_at", desc=True)
        .execute()
    ).data or []
except Exception:
    saved_resolutions = []

latest_resolution_by_task = {}
for row in saved_resolutions:
    tid = str(row.get("resolution_task_id") or "")
    if tid and tid not in latest_resolution_by_task:
        latest_resolution_by_task[tid] = row


# ---------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------
generated_count = sum(
    1 for row in latest_resolution_by_task.values()
    if str(first_value(row, ["proposed_resolution", "resolution_text", "value_text"], "") or "").strip()
)
approved_count = sum(
    1 for row in latest_resolution_by_task.values()
    if norm(first_value(row, ["resolution_status", "status"], "")) in
    ("approved", "accepted", "applied", "ready")
)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Task-uri oportunitate", len(tasks))
c2.metric("Cu dovezi utilizabile", len(resolvable_tasks))
c3.metric("Rezolvări generate", generated_count)
c4.metric("Aprobate", approved_count)

st.info(
    "Etapa 23 folosește numai dovezi salvate/confirmate. AI poate redacta o propunere de "
    "rezolvare, dar utilizatorul trebuie să o aprobe explicit înainte de a fi transmisă "
    "către resolution_tasks."
)


# ---------------------------------------------------------------------
# AI resolver
# ---------------------------------------------------------------------
def build_requirements():
    reqs = []

    for task in resolvable_tasks:
        tid = str(task.get("id") or "")
        evidence = latest_evidence_by_task.get(tid, {})
        suggestion = latest_autofill_by_task.get(tid, {})

        evidence_value = str(
            first_value(evidence, ["value_text", "value", "text"], "") or ""
        ).strip()

        if evidence_value:
            source_value = evidence_value
            verification_status = str(evidence.get("verification_status") or "Unverified")
            source_reference = str(evidence.get("source_reference") or "")
            source_type = str(evidence.get("source_type") or "user_input")
        else:
            source_value = str(suggestion.get("suggested_value") or "").strip()
            verification_status = "User confirmed"
            source_reference = str(suggestion.get("source_reference") or "")
            source_type = str(suggestion.get("source_type") or "project_data")

        reqs.append({
            "resolution_task_id": tid,
            "category": str(first_value(task, ["category"], "Other")),
            "title": str(first_value(
                task,
                ["issue_title", "task", "title", "issue", "description"],
                "Cerință"
            )),
            "issue_description": str(first_value(
                task,
                ["issue_description", "description", "reason"],
                ""
            )),
            "required_input": str(first_value(
                task,
                ["required_input", "input_request", "reason", "issue_description", "description"],
                ""
            )),
            "current_status": str(task.get("status") or ""),
            "evidence_value": source_value,
            "verification_status": verification_status,
            "source_type": source_type,
            "source_reference": source_reference,
        })

    return reqs


def generate_resolutions(requirements):
    prompt = f"""
You are an EU grant evidence resolution assistant.

Your task is to draft a safe resolution for each supplied resolution task using ONLY
the evidence explicitly supplied with that task.

STRICT RULES:
- Never invent facts.
- Never invent budgets, partners, consortium members, eligibility, TRL, KPIs,
  certifications, legal facts, official rules, references or evidence.
- Do not use unsupported assumptions.
- Do not declare anything officially verified unless the supplied evidence itself
  explicitly has that status.
- "User confirmed" is not the same as "Officially verified".
- If the supplied evidence is insufficient to resolve the task safely,
  return can_resolve=false.
- Preserve uncertainty and qualification present in the evidence.
- Do not add new numeric values.
- Do not silently change the meaning of user-provided evidence.
- The proposed resolution must be suitable for later human review.
- Return exactly one item for each supplied task.
- Return valid JSON only.

Return exactly:
{{
  "summary": "",
  "items": [
    {{
      "resolution_task_id": "",
      "category": "",
      "can_resolve": true,
      "proposed_resolution": "",
      "evidence_used": "",
      "source_reference": "",
      "verification_status": "",
      "reason": "",
      "confidence": "High|Medium|Low"
    }}
  ]
}}

TASKS AND CONFIRMED EVIDENCE:
{compact_json(requirements)}
"""

    response = get_openai().responses.create(
        model=model_name(),
        instructions=(
            "Return JSON only. Resolve tasks only from supplied evidence. "
            "Never invent or upgrade verification status."
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
# Flexible persistence helpers
# These keep Stage 23 compatible if the SQL schema contains either the
# preferred field names or the common aliases used in earlier stages.
# ---------------------------------------------------------------------
def insert_resolution(payload):
    """
    Preferred Stage 23 schema:
      user_id, project_id, opportunity_identity, resolution_task_id,
      category, proposed_resolution, evidence_used, source_reference,
      verification_status, confidence, reason, resolution_status,
      ai_result, updated_at
    """
    return supabase.table("evidence_resolution_inputs").insert(payload).execute()


def insert_run(payload):
    return supabase.table("evidence_resolution_runs").insert(payload).execute()


# ---------------------------------------------------------------------
# Generate
# ---------------------------------------------------------------------
if st.button(
    "🧩 Generează propuneri de rezolvare din dovezile confirmate",
    type="primary",
    use_container_width=True,
    disabled=not resolvable_tasks,
):
    requirements = build_requirements()

    with st.spinner("AI construiește rezolvările fără să inventeze informații..."):
        try:
            result = generate_resolutions(requirements)
            created = 0
            resolvable_count = 0

            for item in result.get("items", []):
                tid = str(item.get("resolution_task_id") or "").strip()
                if not tid:
                    continue

                can_resolve = bool(item.get("can_resolve"))
                proposed = (
                    str(item.get("proposed_resolution") or "").strip()
                    if can_resolve else ""
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
                    "proposed_resolution": proposed,
                    "evidence_used": str(item.get("evidence_used") or ""),
                    "source_reference": str(item.get("source_reference") or ""),
                    "verification_status": str(
                        item.get("verification_status") or "User confirmed"
                    ),
                    "confidence": confidence,
                    "reason": str(item.get("reason") or ""),
                    "resolution_status": "Proposed" if proposed else "Rejected",
                    "ai_result": item,
                    "updated_at": now_iso(),
                }

                insert_resolution(payload)
                created += 1
                if proposed:
                    resolvable_count += 1

            run_payload = {
                "user_id": user_id,
                "project_id": project_id,
                "opportunity_identity": opportunity_identity,
                "total_requirements": len(requirements),
                "resolved_requirements": resolvable_count,
                "unresolved_requirements": max(len(requirements) - resolvable_count, 0),
                "run_status": "Completed",
                "summary": {
                    "stage": 23,
                    "text": result.get("summary", ""),
                },
            }
            insert_run(run_payload)

            st.success(f"Etapa 23 a procesat {created} task-uri.")
            st.rerun()

        except Exception as exc:
            st.error(f"Resolver-ul nu a putut fi executat: {exc}")


# ---------------------------------------------------------------------
# Reload Stage 23 rows
# ---------------------------------------------------------------------
try:
    saved_resolutions = (
        supabase.table("evidence_resolution_inputs")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_identity", opportunity_identity)
        .order("created_at", desc=True)
        .execute()
    ).data or []
except Exception:
    saved_resolutions = []

latest_resolution_by_task = {}
for row in saved_resolutions:
    tid = str(row.get("resolution_task_id") or "")
    if tid and tid not in latest_resolution_by_task:
        latest_resolution_by_task[tid] = row


# ---------------------------------------------------------------------
# Review + explicit approval
# ---------------------------------------------------------------------
st.subheader("Rezolvări propuse")

if not resolvable_tasks:
    st.warning(
        "Nu există încă task-uri cu dovezi suficiente pentru Etapa 23. "
        "Completează Etapa 21 sau aprobă o sugestie validă în Etapa 22."
    )
else:
    for pos, task in enumerate(resolvable_tasks):
        task_id = str(task.get("id") or "")
        category = str(first_value(task, ["category"], "Other"))
        title = str(first_value(
            task,
            ["issue_title", "task", "title", "issue", "description"],
            "Cerință"
        ))

        resolution = latest_resolution_by_task.get(task_id, {})
        proposed = str(first_value(
            resolution,
            ["proposed_resolution", "resolution_text", "value_text"],
            ""
        ) or "")
        status = str(first_value(
            resolution,
            ["resolution_status", "status"],
            "Not analysed"
        ))

        with st.expander(
            f"{'✨' if proposed else '🔒'} {category} — {title} [{status}]",
            expanded=(pos == 0),
        ):
            evidence = latest_evidence_by_task.get(task_id, {})
            suggestion = latest_autofill_by_task.get(task_id, {})

            evidence_value = str(
                first_value(evidence, ["value_text", "value", "text"], "") or ""
            ).strip()

            if evidence_value:
                st.write("**Dovadă disponibilă:**")
                st.write(evidence_value)
                st.caption(
                    f"Verificare: {evidence.get('verification_status') or 'Unverified'} | "
                    f"Sursă: {evidence.get('source_reference') or 'Nespecificată'}"
                )
            elif norm(suggestion.get("suggestion_status")) in (
                "approved", "accepted", "applied"
            ):
                st.write("**Dovadă provenită din Etapa 22 și aprobată:**")
                st.write(str(suggestion.get("suggested_value") or ""))
                st.caption(
                    f"Sursă: {suggestion.get('source_reference') or 'Nespecificată'}"
                )

            if not resolution:
                st.caption("Nu există încă o analiză Resolver pentru acest task.")
                continue

            if not proposed:
                st.warning(
                    "AI a considerat că dovada disponibilă nu este suficientă "
                    "pentru o rezolvare sigură."
                )
                if resolution.get("reason"):
                    st.write(f"**Motiv:** {resolution.get('reason')}")
                continue

            edited_resolution = st.text_area(
                "Rezolvare propusă",
                value=proposed,
                height=190,
                key=f"resolver_value_{pos}_{task_id}",
            )

            st.write(
                f"**Sursă:** {resolution.get('source_reference') or 'Nespecificată'}  \n"
                f"**Verificare:** {resolution.get('verification_status') or 'User confirmed'}  \n"
                f"**Încredere AI:** {resolution.get('confidence') or 'Low'}"
            )

            if resolution.get("reason"):
                st.caption(str(resolution.get("reason")))

            approve = st.checkbox(
                "Am verificat rezolvarea și aprob trimiterea ei către task",
                key=f"approve_resolution_{pos}_{task_id}",
            )

            if st.button(
                "✅ Aprobă rezolvarea",
                key=f"apply_resolution_{pos}_{task_id}",
                use_container_width=True,
                disabled=not approve or not edited_resolution.strip(),
            ):
                try:
                    # Keep Stage 23 audit trail.
                    supabase.table("evidence_resolution_inputs").update({
                        "proposed_resolution": edited_resolution.strip(),
                        "resolution_status": "Approved",
                        "updated_at": now_iso(),
                    }).eq("id", resolution["id"]).eq("user_id", user_id).execute()

                    # Pass only the approved draft to the existing resolution task.
                    # Stage 23 does NOT execute the final submission itself.
                    task_update = {
                        "proposed_resolution": edited_resolution.strip(),
                        "status": "Ready",
                    }

                    supabase.table("resolution_tasks").update(
                        task_update
                    ).eq("id", task_id).eq("user_id", user_id).execute()

                    st.success(
                        "Rezolvarea a fost aprobată și trimisă către resolution_tasks."
                    )
                    st.rerun()

                except Exception as exc:
                    st.error(f"Nu am putut aproba rezolvarea: {exc}")


# ---------------------------------------------------------------------
# History
# ---------------------------------------------------------------------
st.divider()

with st.expander("Istoric Evidence Resolver"):
    try:
        hist = (
            supabase.table("evidence_resolution_inputs")
            .select("*")
            .eq("user_id", user_id)
            .eq("project_id", project_id)
            .eq("opportunity_identity", opportunity_identity)
            .order("created_at", desc=True)
            .limit(100)
            .execute()
        ).data or []

        if hist:
            display_cols = [
                "id",
                "category",
                "resolution_task_id",
                "verification_status",
                "confidence",
                "resolution_status",
                "created_at",
                "updated_at",
            ]
            available = [
                col for col in display_cols
                if any(col in row for row in hist)
            ]
            st.dataframe(
                [{k: row.get(k) for k in available} for row in hist],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.caption("Nu există încă rezolvări salvate.")
    except Exception as exc:
        st.error(f"Nu am putut încărca istoricul: {exc}")

st.caption(
    "Etapa 23 redactează rezolvări numai din dovezi existente. "
    "Nu inventează informații, nu ridică nivelul de verificare și nu execută "
    "automat o depunere finală."
)
