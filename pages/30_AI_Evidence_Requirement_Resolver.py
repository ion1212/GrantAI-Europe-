import os
import json
from datetime import datetime, timezone
from typing import Any

import streamlit as st
from openai import OpenAI
from supabase import create_client

st.set_page_config(
    page_title="AI Evidence & Requirement Resolver",
    page_icon="🧾",
    layout="wide",
)

st.title("🧾 Etapa 30 — AI Evidence & Official Requirement Resolver")
st.caption(
    "Transformă gap-urile din Etapa 29 în acțiuni concrete: ce poate redacta AI-ul, "
    "ce informație trebuie furnizată de utilizator, ce trebuie verificat oficial și ce blochează continuarea."
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


def compact_json(obj: Any, limit: int = 30000) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)[:limit]


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
# Latest Stage 29 run + gap items
# ---------------------------------------------------------------------
gate_runs = rows(
    "opportunity_fit_gate_runs",
    {"user_id": user_id, "project_id": project_id},
    "created_at",
    20,
)

if not gate_runs:
    st.warning("Nu există încă un rezultat Etapa 29 pentru acest proiect.")
    st.stop()

gate_run = gate_runs[0]
gate_run_id = str(gate_run["id"])
opportunity_identity = str(gate_run.get("opportunity_identity") or "")

st.text_input("Oportunitate", value=opportunity_identity or "—", disabled=True)

gate_result = safe_json(gate_run.get("result"))

# Etapa 29 may explicitly flag claims that require official call verification.
# These claims must override an earlier USER_EVIDENCE classification.
official_verification_claims = [
    str(x).strip()
    for x in (gate_result.get("claims_requiring_official_call_verification") or [])
    if str(x).strip()
]

gate_items = rows(
    "opportunity_fit_gate_items",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_identity": opportunity_identity,
        "gate_run_id": gate_run_id,
    },
    "created_at",
    100,
)

open_gate_items = [
    item for item in gate_items
    if lower(item.get("status")) in ("open", "blocked")
]

if not open_gate_items:
    st.success("Etapa 29 nu are gap-uri deschise pentru acest proiect.")
    st.stop()


# ---------------------------------------------------------------------
# Existing Stage 30 run/items
# ---------------------------------------------------------------------
existing_runs = rows(
    "evidence_requirement_resolution_runs",
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
    if str(run.get("opportunity_fit_gate_run_id") or "") == gate_run_id:
        latest_run = run
        break

existing_items = []
if latest_run:
    existing_items = rows(
        "evidence_requirement_resolution_items",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_identity": opportunity_identity,
            "resolution_run_id": latest_run["id"],
        },
        "created_at",
        200,
    )

latest_item_by_gate_item = {}
for item in existing_items:
    gid = str(item.get("opportunity_fit_gate_item_id") or "")
    if gid and gid not in latest_item_by_gate_item:
        latest_item_by_gate_item[gid] = item


# ---------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------
def _tokens(text: str) -> set[str]:
    stop = {
        "the", "and", "for", "with", "from", "that", "this", "are", "was",
        "call", "requirements", "requirement", "official", "verification",
        "proposal", "project", "missing", "explicit", "address", "themes",
        "thematic", "priorities", "criteria", "mandatory"
    }
    return {
        token for token in
        "".join(ch.lower() if ch.isalnum() else " " for ch in str(text or "")).split()
        if len(token) >= 4 and token not in stop
    }


def requires_official_verification(item: dict) -> bool:
    if str(item.get("classification") or "") == "OFFICIAL_VERIFICATION":
        return True

    gap_text = " ".join([
        str(item.get("title") or ""),
        str(item.get("reason") or ""),
        str(item.get("required_next_action") or ""),
        str(item.get("evidence") or ""),
    ])
    gap_tokens = _tokens(gap_text)

    for claim in official_verification_claims:
        claim_tokens = _tokens(claim)
        if claim_tokens and len(gap_tokens & claim_tokens) >= min(2, len(claim_tokens)):
            return True

    return False


classification_counts = {
    "AI_DRAFTABLE": 0,
    "USER_EVIDENCE": 0,
    "OFFICIAL_VERIFICATION": 0,
    "OPPORTUNITY_MISMATCH": 0,
}

for item in open_gate_items:
    cls = (
        "OFFICIAL_VERIFICATION"
        if requires_official_verification(item)
        else str(item.get("classification") or "")
    )
    if cls in classification_counts:
        classification_counts[cls] += 1

c1, c2, c3, c4 = st.columns(4)
c1.metric("AI draftable", classification_counts["AI_DRAFTABLE"])
c2.metric("User evidence", classification_counts["USER_EVIDENCE"])
c3.metric("Official verification", classification_counts["OFFICIAL_VERIFICATION"])
c4.metric("Opportunity mismatch", classification_counts["OPPORTUNITY_MISMATCH"])

st.info(
    "Etapa 30 nu declară automat nicio cerință ca oficială și nu inventează dovezi. "
    "Fiecare gap primește un traseu separat de rezolvare."
)


# ---------------------------------------------------------------------
# AI decomposition
# ---------------------------------------------------------------------
SYSTEM = """You are an evidence and official-requirement resolution planner for EU grant workflows.

Your input comes from a prior Opportunity Fit Gate. For each gap, convert it into a safe operational task.

Rules:
- Never invent facts, TRL, budget numbers, consortium members, KPIs, eligibility, official rules or legal conclusions.
- USER_EVIDENCE: ask only for concrete factual inputs/documents that the applicant can provide.
- OFFICIAL_VERIFICATION: formulate exactly what must be checked in official call documentation; do not claim the requirement is official.
- AI_DRAFTABLE: draft only wording supported by existing evidence supplied in the gap.
- OPPORTUNITY_MISMATCH: block continuation and explain what must be clarified before further drafting.
- Return strict JSON only.
- One output item per input gap.
- Preserve the classification supplied in each input gap. Never downgrade OFFICIAL_VERIFICATION to USER_EVIDENCE.

Schema:
{
  "summary": "",
  "items": [
    {
      "opportunity_fit_gate_item_id": "",
      "title": "",
      "classification": "AI_DRAFTABLE|USER_EVIDENCE|OFFICIAL_VERIFICATION|OPPORTUNITY_MISMATCH",
      "severity": "Low|Medium|High|Critical",
      "requested_information": "",
      "requested_document": "",
      "official_verification_query": "",
      "ai_draft": "",
      "resolution_status": "Waiting for user|Waiting for verification|Draft ready|Blocked",
      "can_continue": false,
      "resolution_reason": ""
    }
  ]
}"""


def build_gap_payload():
    payload = []
    for item in open_gate_items:
        classification = str(item.get("classification") or "")
        overridden = requires_official_verification(item)

        if overridden:
            classification = "OFFICIAL_VERIFICATION"

        payload.append({
            "opportunity_fit_gate_item_id": str(item.get("id") or ""),
            "title": str(item.get("title") or ""),
            "classification": classification,
            "severity": str(item.get("severity") or "Medium"),
            "reason": str(item.get("reason") or ""),
            "required_next_action": str(item.get("required_next_action") or ""),
            "evidence": str(item.get("evidence") or ""),
            "classification_override_reason": (
                "Etapa 29 explicitly marked a matching claim as requiring official call verification."
                if overridden else ""
            ),
        })
    return payload


def ai_plan():
    client = get_openai()
    prompt = {
        "project": {
            "id": project_id,
            "name": project.get("name") or project.get("title") or "",
            "description": project.get("description") or "",
        },
        "opportunity_identity": opportunity_identity,
        "gate_verdict": gate_result.get("verdict"),
        "fit_score": gate_result.get("fit_score"),
        "gaps": build_gap_payload(),
    }

    response = client.chat.completions.create(
        model=model_name(),
        temperature=0.1,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
    )

    result = json.loads(clean_json(response.choices[0].message.content))
    if not isinstance(result, dict):
        raise ValueError("AI result is not a JSON object.")

    result.setdefault("summary", "")
    result.setdefault("items", [])
    return result


# ---------------------------------------------------------------------
# Create Stage 30 run
# ---------------------------------------------------------------------
if st.button(
    "🧾 Generează planul de rezolvare pentru gap-uri",
    type="primary",
    use_container_width=True,
):
    with st.spinner("Transform gap-urile în acțiuni controlate..."):
        try:
            result = ai_plan()

            user_evidence_count = sum(
                1 for i in result.get("items", [])
                if i.get("classification") == "USER_EVIDENCE"
            )
            official_count = sum(
                1 for i in result.get("items", [])
                if i.get("classification") == "OFFICIAL_VERIFICATION"
            )
            ai_count = sum(
                1 for i in result.get("items", [])
                if i.get("classification") == "AI_DRAFTABLE"
            )
            mismatch_count = sum(
                1 for i in result.get("items", [])
                if i.get("classification") == "OPPORTUNITY_MISMATCH"
            )

            if mismatch_count:
                overall_status = "Blocked"
            elif official_count:
                overall_status = "Waiting for verification"
            elif user_evidence_count:
                overall_status = "Waiting for user"
            else:
                overall_status = "Ready"

            run_insert = (
                supabase.table("evidence_requirement_resolution_runs")
                .insert({
                    "user_id": user_id,
                    "project_id": project_id,
                    "opportunity_identity": opportunity_identity,
                    "opportunity_fit_gate_run_id": gate_run_id,
                    "total_gaps": len(result.get("items", [])),
                    "user_evidence_gaps": user_evidence_count,
                    "official_verification_gaps": official_count,
                    "ai_draftable_gaps": ai_count,
                    "mismatch_gaps": mismatch_count,
                    "resolved_gaps": 0,
                    "blocked_gaps": mismatch_count,
                    "overall_status": overall_status,
                    "summary": {
                        "stage": 30,
                        "text": result.get("summary", ""),
                    },
                    "updated_at": now_iso(),
                })
                .execute()
            )

            run_data = run_insert.data or []
            if not run_data:
                raise RuntimeError("Nu am putut salva run-ul Etapei 30.")

            resolution_run_id = str(run_data[0]["id"])

            for item in result.get("items", []):
                cls = str(item.get("classification") or "OFFICIAL_VERIFICATION")
                if cls not in (
                    "AI_DRAFTABLE",
                    "USER_EVIDENCE",
                    "OFFICIAL_VERIFICATION",
                    "OPPORTUNITY_MISMATCH",
                ):
                    cls = "OFFICIAL_VERIFICATION"

                status = str(item.get("resolution_status") or "Open")
                if status not in (
                    "Open",
                    "Waiting for user",
                    "Waiting for verification",
                    "Draft ready",
                    "Resolved",
                    "Blocked",
                    "Dismissed",
                ):
                    status = "Open"

                source_gate_item = next(
                    (
                        g for g in open_gate_items
                        if str(g.get("id")) == str(item.get("opportunity_fit_gate_item_id"))
                    ),
                    {},
                )

                supabase.table("evidence_requirement_resolution_items").insert({
                    "user_id": user_id,
                    "project_id": project_id,
                    "opportunity_identity": opportunity_identity,
                    "resolution_run_id": resolution_run_id,
                    "opportunity_fit_gate_item_id": item.get("opportunity_fit_gate_item_id"),
                    "title": str(item.get("title") or source_gate_item.get("title") or "Gap"),
                    "classification": cls,
                    "severity": str(item.get("severity") or source_gate_item.get("severity") or "Medium"),
                    "requirement_or_gap": str(source_gate_item.get("reason") or ""),
                    "current_evidence": str(source_gate_item.get("evidence") or ""),
                    "requested_information": str(item.get("requested_information") or ""),
                    "requested_document": str(item.get("requested_document") or ""),
                    "official_verification_query": str(item.get("official_verification_query") or ""),
                    "official_source_reference": "",
                    "official_verification_result": "",
                    "ai_draft": str(item.get("ai_draft") or ""),
                    "user_response": "",
                    "user_evidence_reference": "",
                    "resolution_status": status,
                    "verification_status": "Unverified",
                    "can_continue": bool(item.get("can_continue")),
                    "resolution_reason": str(item.get("resolution_reason") or ""),
                    "metadata": {
                        "stage": 30,
                        "gate_item": source_gate_item,
                    },
                    "updated_at": now_iso(),
                }).execute()

            st.success("Planul Etapei 30 a fost generat și salvat.")
            st.rerun()

        except Exception as exc:
            st.error(f"Etapa 30 nu a putut genera planul: {exc}")


# ---------------------------------------------------------------------
# Reload latest Stage 30 run/items
# ---------------------------------------------------------------------
existing_runs = rows(
    "evidence_requirement_resolution_runs",
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
    if str(run.get("opportunity_fit_gate_run_id") or "") == gate_run_id:
        latest_run = run
        break

stage30_items = []
if latest_run:
    stage30_items = rows(
        "evidence_requirement_resolution_items",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_identity": opportunity_identity,
            "resolution_run_id": latest_run["id"],
        },
        "created_at",
        200,
    )


# ---------------------------------------------------------------------
# Resolve UI
# ---------------------------------------------------------------------
st.subheader("Gap Resolution Workspace")

if not latest_run:
    st.caption("Nu există încă un plan Etapa 30.")
else:
    r1, r2, r3, r4 = st.columns(4)
    r1.metric("Overall", latest_run.get("overall_status") or "Pending")
    r2.metric("Total gaps", int(latest_run.get("total_gaps") or 0))
    r3.metric("Resolved", int(latest_run.get("resolved_gaps") or 0))
    r4.metric("Blocked", int(latest_run.get("blocked_gaps") or 0))

    for pos, item in enumerate(stage30_items):
        item_id = str(item["id"])
        cls = str(item.get("classification") or "")
        status = str(item.get("resolution_status") or "Open")

        icon = {
            "AI_DRAFTABLE": "✍️",
            "USER_EVIDENCE": "📎",
            "OFFICIAL_VERIFICATION": "🔎",
            "OPPORTUNITY_MISMATCH": "⛔",
        }.get(cls, "•")

        with st.expander(
            f"{icon} {item.get('title') or 'Gap'} — {cls} [{status}]",
            expanded=(pos == 0),
        ):
            if item.get("requirement_or_gap"):
                st.write("**Gap:**")
                st.write(item.get("requirement_or_gap"))

            if item.get("current_evidence"):
                st.write("**Current evidence:**")
                st.write(item.get("current_evidence"))

            if cls == "USER_EVIDENCE":
                if item.get("requested_information"):
                    st.info(f"Informație necesară: {item.get('requested_information')}")
                if item.get("requested_document"):
                    st.info(f"Document/dovadă utilă: {item.get('requested_document')}")

                user_response = st.text_area(
                    "Răspuns / informație furnizată",
                    value=str(item.get("user_response") or ""),
                    height=150,
                    key=f"user_response_{item_id}",
                )

                evidence_ref = st.text_input(
                    "Referință dovadă / document",
                    value=str(item.get("user_evidence_reference") or ""),
                    key=f"user_ref_{item_id}",
                )

                confirm = st.checkbox(
                    "Confirm că informația introdusă este reală și poate fi folosită în proiect",
                    key=f"user_confirm_{item_id}",
                )

                if st.button(
                    "💾 Salvează și rezolvă gap-ul",
                    key=f"save_user_{item_id}",
                    use_container_width=True,
                    disabled=not confirm or not user_response.strip(),
                ):
                    try:
                        supabase.table("evidence_requirement_resolution_items").update({
                            "user_response": user_response.strip(),
                            "user_evidence_reference": evidence_ref.strip(),
                            "resolution_status": "Resolved",
                            "verification_status": "User confirmed",
                            "can_continue": True,
                            "updated_at": now_iso(),
                        }).eq("id", item_id).eq("user_id", user_id).execute()
                        st.success("Gap rezolvat ca User confirmed.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Nu am putut salva: {exc}")

            elif cls == "OFFICIAL_VERIFICATION":
                st.warning(
                    "Acest gap nu poate fi tratat drept cerință oficială până la verificarea documentației apelului."
                )

                st.write("**Ce trebuie verificat oficial:**")
                st.write(item.get("official_verification_query") or "—")

                source_ref = st.text_input(
                    "Referință sursă oficială",
                    value=str(item.get("official_source_reference") or ""),
                    placeholder="Ex.: Guidelines for Applicants, secțiunea X, pagina Y",
                    key=f"official_ref_{item_id}",
                )

                verification_result = st.text_area(
                    "Rezultatul verificării oficiale",
                    value=str(item.get("official_verification_result") or ""),
                    height=150,
                    key=f"official_result_{item_id}",
                )

                verified = st.checkbox(
                    "Am verificat această informație într-o sursă oficială",
                    key=f"official_confirm_{item_id}",
                )

                if st.button(
                    "✅ Salvează verificarea oficială",
                    key=f"save_official_{item_id}",
                    use_container_width=True,
                    disabled=not verified or not source_ref.strip() or not verification_result.strip(),
                ):
                    try:
                        supabase.table("evidence_requirement_resolution_items").update({
                            "official_source_reference": source_ref.strip(),
                            "official_verification_result": verification_result.strip(),
                            "resolution_status": "Resolved",
                            "verification_status": "Officially verified",
                            "can_continue": True,
                            "updated_at": now_iso(),
                        }).eq("id", item_id).eq("user_id", user_id).execute()
                        st.success("Cerința a fost marcată Officially verified.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Nu am putut salva verificarea: {exc}")

            elif cls == "AI_DRAFTABLE":
                draft = st.text_area(
                    "Draft AI",
                    value=str(item.get("ai_draft") or ""),
                    height=180,
                    key=f"draft_{item_id}",
                )

                approve = st.checkbox(
                    "Aprob draftul pentru a continua în flux",
                    key=f"approve_draft_{item_id}",
                )

                if st.button(
                    "✅ Aprobă draftul",
                    key=f"approve_ai_{item_id}",
                    use_container_width=True,
                    disabled=not approve or not draft.strip(),
                ):
                    try:
                        supabase.table("evidence_requirement_resolution_items").update({
                            "ai_draft": draft.strip(),
                            "resolution_status": "Resolved",
                            "verification_status": "User confirmed",
                            "can_continue": True,
                            "updated_at": now_iso(),
                        }).eq("id", item_id).eq("user_id", user_id).execute()
                        st.success("Draft aprobat.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Nu am putut aproba draftul: {exc}")

            elif cls == "OPPORTUNITY_MISMATCH":
                st.error(
                    "Acest gap blochează continuarea. Nu trebuie mascat prin redactare automată."
                )
                st.write(item.get("resolution_reason") or "")
                st.caption(
                    "Continuarea trebuie reluată numai după clarificarea oportunității sau schimbarea apelului."
                )


# ---------------------------------------------------------------------
# Recalculate run
# ---------------------------------------------------------------------
if latest_run:
    fresh_items = rows(
        "evidence_requirement_resolution_items",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_identity": opportunity_identity,
            "resolution_run_id": latest_run["id"],
        },
        "created_at",
        200,
    )

    resolved = sum(
        1 for item in fresh_items
        if str(item.get("resolution_status") or "") == "Resolved"
    )
    blocked = sum(
        1 for item in fresh_items
        if str(item.get("resolution_status") or "") == "Blocked"
        or str(item.get("classification") or "") == "OPPORTUNITY_MISMATCH"
    )
    waiting_user = any(
        str(item.get("resolution_status") or "") == "Waiting for user"
        for item in fresh_items
    )
    waiting_official = any(
        str(item.get("resolution_status") or "") == "Waiting for verification"
        for item in fresh_items
    )

    if blocked:
        overall = "Blocked"
    elif resolved == len(fresh_items) and fresh_items:
        overall = "Ready"
    elif waiting_official:
        overall = "Waiting for verification"
    elif waiting_user:
        overall = "Waiting for user"
    else:
        overall = "Pending"

    try:
        supabase.table("evidence_requirement_resolution_runs").update({
            "resolved_gaps": resolved,
            "blocked_gaps": blocked,
            "overall_status": overall,
            "updated_at": now_iso(),
        }).eq("id", latest_run["id"]).eq("user_id", user_id).execute()
    except Exception:
        pass


# ---------------------------------------------------------------------
# History
# ---------------------------------------------------------------------
st.divider()

with st.expander("Istoric Etapa 30"):
    if existing_runs:
        st.dataframe(
            [
                {
                    "created_at": r.get("created_at"),
                    "total_gaps": r.get("total_gaps"),
                    "resolved_gaps": r.get("resolved_gaps"),
                    "blocked_gaps": r.get("blocked_gaps"),
                    "overall_status": r.get("overall_status"),
                }
                for r in existing_runs
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("Nu există încă rulări Etapa 30.")

st.caption(
    "Etapa 30 separă strict dovezile utilizatorului de verificarea oficială și de redactarea AI. "
    "Un gap rezolvat aici poate alimenta următorul ciclu de Resolution/Writer fără a inventa informații."
)
