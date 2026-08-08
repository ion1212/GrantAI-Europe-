import os
import json
from datetime import datetime, timezone
from typing import Any

import streamlit as st
from openai import OpenAI
from supabase import create_client

st.set_page_config(
    page_title="AI Official Call Verification",
    page_icon="🔎",
    layout="wide",
)

st.title("🔎 Etapa 31 — AI Official Call Verification")
st.caption(
    "Verifică cerințele marcate OFFICIAL_VERIFICATION în Etapa 30 folosind numai "
    "surse oficiale furnizate sau documente oficiale deja stocate. "
    "Nu transformă automat o afirmație AI într-o cerință oficială."
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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def norm(v: Any) -> str:
    return str(v or "").strip()


def lower(v: Any) -> str:
    return norm(v).lower()


def safe_json(v: Any):
    if isinstance(v, (dict, list)):
        return v
    if not v:
        return {}
    try:
        return json.loads(v)
    except Exception:
        return {}


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


def rows(table: str, filters=None, order="created_at", limit=100):
    q = supabase.table(table).select("*")
    for key, value in (filters or {}).items():
        if value not in (None, ""):
            q = q.eq(key, value)
    if order:
        q = q.order(order, desc=True)
    if limit:
        q = q.limit(limit)
    try:
        return q.execute().data or []
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
# Project
# ---------------------------------------------------------------------
projects = rows("projects", {"user_id": user_id}, "created_at", 100)

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


# ---------------------------------------------------------------------
# Latest Stage 30 run + OFFICIAL_VERIFICATION items
# ---------------------------------------------------------------------
stage30_runs = rows(
    "evidence_requirement_resolution_runs",
    {"user_id": user_id, "project_id": project_id},
    "created_at",
    50,
)

if not stage30_runs:
    st.warning("Nu există un run Etapa 30 pentru acest proiect.")
    st.stop()

stage30_run = stage30_runs[0]
stage30_run_id = str(stage30_run["id"])
opportunity_identity = str(stage30_run.get("opportunity_identity") or "")

st.text_input("Oportunitate", value=opportunity_identity or "—", disabled=True)

stage30_items = rows(
    "evidence_requirement_resolution_items",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_identity": opportunity_identity,
        "resolution_run_id": stage30_run_id,
    },
    "created_at",
    200,
)

official_items = [
    item for item in stage30_items
    if str(item.get("classification") or "") == "OFFICIAL_VERIFICATION"
    and str(item.get("resolution_status") or "") not in ("Dismissed",)
]

if not official_items:
    st.success("Etapa 30 nu are cerințe OFFICIAL_VERIFICATION de procesat.")
    st.stop()


# ---------------------------------------------------------------------
# Attempt to locate official documents already stored in documents table
# ---------------------------------------------------------------------
document_rows = rows(
    "documents",
    {"user_id": user_id},
    "created_at",
    200,
)

project_documents = []
for doc in document_rows:
    doc_project = str(doc.get("project_id") or "")
    if doc_project and doc_project != project_id:
        continue

    text_blob = " ".join([
        str(doc.get("title") or ""),
        str(doc.get("name") or ""),
        str(doc.get("filename") or ""),
        str(doc.get("content") or ""),
    ]).lower()

    # Only surface documents plausibly connected to the call.
    if (
        opportunity_identity.lower() in text_blob
        or "guidelines" in text_blob
        or "applicant" in text_blob
        or "call" in text_blob
        or "programme" in text_blob
        or "eligibility" in text_blob
    ):
        project_documents.append(doc)


# ---------------------------------------------------------------------
# Existing Stage 31 results
# ---------------------------------------------------------------------
existing_runs = rows(
    "official_call_verification_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_identity": opportunity_identity,
    },
    "created_at",
    50,
)

latest_run = None
for run in existing_runs:
    if str(run.get("evidence_resolution_run_id") or "") == stage30_run_id:
        latest_run = run
        break

existing_verifications = []
if latest_run:
    existing_verifications = rows(
        "official_call_verification_items",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_identity": opportunity_identity,
            "verification_run_id": latest_run["id"],
        },
        "created_at",
        200,
    )

latest_verification_by_stage30_item = {}
for row in existing_verifications:
    key = str(row.get("evidence_resolution_item_id") or "")
    if key and key not in latest_verification_by_stage30_item:
        latest_verification_by_stage30_item[key] = row


# ---------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------
verified_count = sum(
    1 for row in latest_verification_by_stage30_item.values()
    if str(row.get("verification_status") or "") == "Verified"
)

needs_manual_count = sum(
    1 for row in latest_verification_by_stage30_item.values()
    if str(row.get("verification_status") or "") == "Needs manual verification"
)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Official gaps", len(official_items))
c2.metric("Official docs găsite", len(project_documents))
c3.metric("Verified", verified_count)
c4.metric("Needs manual verification", needs_manual_count)

st.info(
    "Un verdict 'Required' este permis numai dacă textul furnizat ca sursă oficială susține explicit cerința. "
    "Dacă sursa este insuficientă, rezultatul trebuie să rămână 'Not found' sau 'Unclear'."
)


# ---------------------------------------------------------------------
# Source selection
# ---------------------------------------------------------------------
st.subheader("Surse oficiale disponibile")

source_options = {"— Selectează / introdu manual —": None}

for doc in project_documents:
    label = (
        str(doc.get("title") or doc.get("name") or doc.get("filename") or "Document")
        + f" — {str(doc.get('id') or '')[:8]}"
    )
    source_options[label] = doc

selected_source_label = st.selectbox(
    "Document oficial stocat",
    list(source_options.keys()),
)

selected_doc = source_options[selected_source_label]

manual_source_title = st.text_input(
    "Titlu sursă oficială",
    value=str(
        selected_doc.get("title")
        or selected_doc.get("name")
        or selected_doc.get("filename")
        or ""
    ) if selected_doc else "",
    placeholder="Ex.: Guidelines for Applicants",
)

manual_source_url = st.text_input(
    "URL / referință sursă oficială",
    value=str(
        selected_doc.get("source_url")
        or selected_doc.get("url")
        or ""
    ) if selected_doc else "",
    placeholder="Ex.: portalul oficial / pagina apelului / documentul oficial",
)

source_text_default = str(selected_doc.get("content") or "") if selected_doc else ""

official_source_text = st.text_area(
    "Text / fragment din sursa oficială",
    value=source_text_default,
    height=260,
    placeholder=(
        "Lipește aici textul relevant din Guidelines for Applicants / call document. "
        "Etapa 31 nu va presupune că o cerință este oficială dacă textul nu o confirmă."
    ),
)

manual_reference = st.text_input(
    "Referință exactă",
    placeholder="Ex.: Section 2.1.1, Eligibility of applicants, page 12",
)


# ---------------------------------------------------------------------
# AI verifier
# ---------------------------------------------------------------------
SYSTEM = """You are a strict official-call requirement verifier for an EU grant workflow.

You receive:
1. A suspected requirement/gap from a prior workflow.
2. Text claimed to come from an official call source.
3. Source title / URL / section reference.

Your job is to determine whether the supplied official text actually establishes the suspected requirement.

STRICT RULES:
- Use only the supplied official-source text.
- Never rely on prior AI reviewer statements as proof.
- Never invent eligibility rules, thematic requirements, consortium rules, budget rules or legal requirements.
- "Required" only if the supplied official text clearly establishes the requirement.
- "Not required" only if the supplied official text clearly shows it is not required or is optional/not applicable.
- "Not found" if the supplied text does not address the requirement.
- "Unclear" if the text is ambiguous or incomplete.
- Supporting excerpt must be a short exact excerpt from the supplied source text, maximum 25 words.
- Do not fabricate an excerpt.
- Return JSON only.

Schema:
{
  "verdict": "Required|Not required|Not found|Unclear",
  "confidence": "Low|Medium|High",
  "supporting_excerpt": "",
  "verification_reason": "",
  "affects_eligibility": false,
  "affects_submission": false,
  "action_required": ""
}"""


def verify_requirement(item: dict, source_text: str):
    client = get_openai()

    payload = {
        "opportunity_identity": opportunity_identity,
        "requirement_title": item.get("title"),
        "verification_query": item.get("official_verification_query"),
        "requirement_or_gap": item.get("requirement_or_gap"),
        "current_evidence": item.get("current_evidence"),
        "official_source": {
            "title": manual_source_title,
            "url_or_reference": manual_source_url,
            "exact_reference": manual_reference,
            "text": source_text,
        },
    }

    response = client.chat.completions.create(
        model=model_name(),
        temperature=0.0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    )

    result = json.loads(clean_json(response.choices[0].message.content))
    if not isinstance(result, dict):
        raise ValueError("Răspunsul AI nu este JSON valid.")

    return result


# ---------------------------------------------------------------------
# Create/reuse Stage 31 run
# ---------------------------------------------------------------------
def ensure_run():
    global latest_run

    if latest_run:
        return str(latest_run["id"])

    fit_runs = rows(
        "opportunity_fit_gate_runs",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_identity": opportunity_identity,
        },
        "created_at",
        1,
    )
    gate_run_id = fit_runs[0].get("id") if fit_runs else None

    inserted = (
        supabase.table("official_call_verification_runs")
        .insert({
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_identity": opportunity_identity,
            "evidence_resolution_run_id": stage30_run_id,
            "opportunity_fit_gate_run_id": gate_run_id,
            "total_requirements": len(official_items),
            "verified_requirements": 0,
            "required_requirements": 0,
            "not_required_requirements": 0,
            "unclear_requirements": 0,
            "not_found_requirements": 0,
            "overall_status": "Running",
            "summary": {
                "stage": 31,
                "source_mode": "official_document_text",
            },
            "started_at": now_iso(),
            "updated_at": now_iso(),
        })
        .execute()
    ).data or []

    if not inserted:
        raise RuntimeError("Nu am putut crea run-ul Etapei 31.")

    latest_run = inserted[0]
    return str(latest_run["id"])


# ---------------------------------------------------------------------
# Verification workspace
# ---------------------------------------------------------------------
st.subheader("Official Verification Workspace")

for pos, item in enumerate(official_items):
    item_id = str(item["id"])
    previous = latest_verification_by_stage30_item.get(item_id)

    previous_verdict = str(previous.get("verdict") or "") if previous else ""
    previous_status = str(previous.get("verification_status") or "") if previous else "Not verified"

    with st.expander(
        f"🔎 {item.get('title') or 'Requirement'} [{previous_verdict or previous_status}]",
        expanded=(pos == 0),
    ):
        st.write("**Ce trebuie verificat:**")
        st.write(item.get("official_verification_query") or "—")

        if item.get("requirement_or_gap"):
            st.write("**Gap original:**")
            st.write(item.get("requirement_or_gap"))

        if previous:
            st.write(
                f"**Ultimul verdict:** {previous.get('verdict')}  \n"
                f"**Confidence:** {previous.get('confidence')}  \n"
                f"**Verification status:** {previous.get('verification_status')}"
            )

            if previous.get("supporting_excerpt"):
                st.write("**Supporting excerpt:**")
                st.write(previous.get("supporting_excerpt"))

            if previous.get("verification_reason"):
                st.write("**Reason:**")
                st.write(previous.get("verification_reason"))

        if st.button(
            "🔎 Verifică această cerință în sursa furnizată",
            key=f"verify_{item_id}",
            use_container_width=True,
            disabled=not bool(official_source_text.strip()),
        ):
            with st.spinner("Verific sursa oficială..."):
                try:
                    result = verify_requirement(item, official_source_text)

                    verdict = str(result.get("verdict") or "Unclear")
                    if verdict not in ("Required", "Not required", "Not found", "Unclear"):
                        verdict = "Unclear"

                    confidence = str(result.get("confidence") or "Low")
                    if confidence not in ("Low", "Medium", "High"):
                        confidence = "Low"

                    verification_status = (
                        "Verified"
                        if verdict in ("Required", "Not required")
                        and confidence in ("Medium", "High")
                        else "Needs manual verification"
                    )

                    run_id = ensure_run()

                    payload = {
                        "user_id": user_id,
                        "project_id": project_id,
                        "opportunity_identity": opportunity_identity,
                        "verification_run_id": run_id,
                        "evidence_resolution_item_id": item_id,
                        "requirement_title": str(item.get("title") or "Requirement"),
                        "verification_query": str(item.get("official_verification_query") or ""),
                        "verdict": verdict,
                        "confidence": confidence,
                        "official_source_title": manual_source_title.strip(),
                        "official_source_url": manual_source_url.strip(),
                        "official_source_reference": manual_reference.strip(),
                        "supporting_excerpt": str(result.get("supporting_excerpt") or ""),
                        "verification_reason": str(result.get("verification_reason") or ""),
                        "affects_eligibility": bool(result.get("affects_eligibility")),
                        "affects_submission": bool(result.get("affects_submission")),
                        "action_required": str(result.get("action_required") or ""),
                        "verification_status": verification_status,
                        "metadata": {
                            "stage": 31,
                            "source_document_id": selected_doc.get("id") if selected_doc else None,
                        },
                        "updated_at": now_iso(),
                    }

                    supabase.table("official_call_verification_items").insert(payload).execute()

                    # Feed verified result back to Stage 30 only when safe.
                    if verification_status == "Verified":
                        if verdict == "Required":
                            stage30_status = "Resolved"
                            can_continue = True
                            official_result = (
                                "Official source confirms this requirement. "
                                + str(result.get("verification_reason") or "")
                            )
                        else:
                            stage30_status = "Resolved"
                            can_continue = True
                            official_result = (
                                "Official source does not establish this as a required condition. "
                                + str(result.get("verification_reason") or "")
                            )

                        supabase.table("evidence_requirement_resolution_items").update({
                            "official_source_reference": " | ".join(
                                x for x in [
                                    manual_source_title.strip(),
                                    manual_source_url.strip(),
                                    manual_reference.strip(),
                                ] if x
                            ),
                            "official_verification_result": official_result,
                            "resolution_status": stage30_status,
                            "verification_status": "Officially verified",
                            "can_continue": can_continue,
                            "updated_at": now_iso(),
                        }).eq("id", item_id).eq("user_id", user_id).execute()

                    st.success(f"Verdict: {verdict} ({confidence})")
                    st.rerun()

                except Exception as exc:
                    st.error(f"Verificarea nu a putut fi finalizată: {exc}")


# ---------------------------------------------------------------------
# Recalculate run counters/status
# ---------------------------------------------------------------------
if latest_run:
    fresh = rows(
        "official_call_verification_items",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_identity": opportunity_identity,
            "verification_run_id": latest_run["id"],
        },
        "created_at",
        200,
    )

    # Keep latest result per Stage 30 item.
    latest_by_item = {}
    for row in fresh:
        key = str(row.get("evidence_resolution_item_id") or "")
        if key and key not in latest_by_item:
            latest_by_item[key] = row

    current = list(latest_by_item.values())

    verified = sum(
        1 for row in current
        if str(row.get("verification_status") or "") == "Verified"
    )
    required = sum(1 for row in current if str(row.get("verdict") or "") == "Required")
    not_required = sum(1 for row in current if str(row.get("verdict") or "") == "Not required")
    unclear = sum(1 for row in current if str(row.get("verdict") or "") == "Unclear")
    not_found = sum(1 for row in current if str(row.get("verdict") or "") == "Not found")

    if verified == len(official_items):
        overall = "Completed"
        completed_at = now_iso()
    elif unclear or not_found or len(current) < len(official_items):
        overall = "Needs verification"
        completed_at = None
    else:
        overall = "Running"
        completed_at = None

    update_payload = {
        "verified_requirements": verified,
        "required_requirements": required,
        "not_required_requirements": not_required,
        "unclear_requirements": unclear,
        "not_found_requirements": not_found,
        "overall_status": overall,
        "summary": {
            "stage": 31,
            "total": len(official_items),
            "verified": verified,
            "required": required,
            "not_required": not_required,
            "unclear": unclear,
            "not_found": not_found,
        },
        "updated_at": now_iso(),
    }

    if completed_at:
        update_payload["completed_at"] = completed_at

    try:
        supabase.table("official_call_verification_runs").update(
            update_payload
        ).eq("id", latest_run["id"]).eq("user_id", user_id).execute()
    except Exception:
        pass


# ---------------------------------------------------------------------
# Status / history
# ---------------------------------------------------------------------
st.divider()

st.subheader("Status Etapa 31")

if latest_run:
    refreshed_runs = rows(
        "official_call_verification_runs",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_identity": opportunity_identity,
        },
        "created_at",
        20,
    )

    current_run = refreshed_runs[0] if refreshed_runs else latest_run

    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Overall", str(current_run.get("overall_status") or "Pending"))
    s2.metric("Verified", int(current_run.get("verified_requirements") or 0))
    s3.metric("Required", int(current_run.get("required_requirements") or 0))
    s4.metric("Not required", int(current_run.get("not_required_requirements") or 0))

    with st.expander("Istoric Official Call Verification"):
        history = rows(
            "official_call_verification_items",
            {
                "user_id": user_id,
                "project_id": project_id,
                "opportunity_identity": opportunity_identity,
            },
            "created_at",
            100,
        )

        if history:
            st.dataframe(
                [
                    {
                        "created_at": row.get("created_at"),
                        "requirement": row.get("requirement_title"),
                        "verdict": row.get("verdict"),
                        "confidence": row.get("confidence"),
                        "status": row.get("verification_status"),
                        "source": row.get("official_source_title"),
                    }
                    for row in history
                ],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.caption("Nu există încă verificări salvate.")

else:
    st.caption("Nicio cerință nu a fost verificată încă.")


st.caption(
    "Etapa 31 nu caută sau inventează singură reguli oficiale. "
    "Ea validează numai textul din sursele oficiale furnizate/stocate și propagă în Etapa 30 "
    "doar verdicte susținute de sursă."
)
