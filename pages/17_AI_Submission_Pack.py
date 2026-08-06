import os
from datetime import datetime, timezone
from typing import Any

import streamlit as st
from supabase import create_client

st.set_page_config(
    page_title="Etapa 17 — AI Submission Pack",
    page_icon="📦",
    layout="wide",
)


def secret(name: str, default: str = "") -> str:
    try:
        return str(st.secrets.get(name, default))
    except Exception:
        return os.getenv(name, default)


@st.cache_resource
def db():
    return create_client(
        secret("SUPABASE_URL"),
        secret("SUPABASE_KEY") or secret("SUPABASE_ANON_KEY"),
    )


def rows(resp):
    data = getattr(resp, "data", None)
    return data if isinstance(data, list) else []


def current_user_id(sb) -> str | None:
    for key in ("user", "auth_user", "current_user"):
        user = st.session_state.get(key)
        if isinstance(user, dict) and user.get("id"):
            return str(user["id"])
        if getattr(user, "id", None):
            return str(user.id)

    try:
        user = sb.auth.get_user().user
        if user and getattr(user, "id", None):
            return str(user.id)
    except Exception:
        pass

    return None


def project_label(project: dict[str, Any]) -> str:
    name = (
        project.get("name")
        or project.get("project_name")
        or project.get("title")
        or "Project"
    )
    return f"{name} — {str(project.get('id', ''))[:8]}"


def opportunity_identity(item: dict[str, Any]) -> str:
    for key in (
        "opportunity_identity",
        "identity",
        "call_id",
        "identifier",
        "code",
        "id",
    ):
        if item.get(key) not in (None, ""):
            return str(item[key])

    return str(
        item.get("title")
        or item.get("name")
        or item.get("topic")
        or "opportunity"
    )[:240]


def opportunity_label(item: dict[str, Any]) -> str:
    title = (
        item.get("title")
        or item.get("name")
        or item.get("topic")
        or "Funding opportunity"
    )
    score = item.get("match_score")
    return (f"{score}% · " if score is not None else "") + str(title)


def load_projects(sb, uid: str):
    try:
        return rows(
            sb.table("projects")
            .select("*")
            .eq("user_id", uid)
            .execute()
        )
    except Exception:
        return rows(sb.table("projects").select("*").execute())


def load_opportunities(sb, uid: str, project_id: str):
    for table in (
        "selected_opportunities",
        "opportunities",
        "funding_opportunities",
        "grant_matches",
    ):
        try:
            try:
                response = (
                    sb.table(table)
                    .select("*")
                    .eq("user_id", uid)
                    .eq("project_id", project_id)
                    .execute()
                )
            except Exception:
                response = sb.table(table).select("*").execute()

            data = rows(response)
            if data:
                return table, data
        except Exception:
            continue

    return None, []


def load_latest_review(sb, uid: str, project_id: str, identity: str):
    try:
        exact = rows(
            sb.table("grant_evaluations")
            .select("*")
            .eq("user_id", uid)
            .eq("project_id", project_id)
            .eq("opportunity_identity", identity)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if exact:
            return exact[0], "exact"
    except Exception:
        pass

    try:
        fallback = rows(
            sb.table("grant_evaluations")
            .select("*")
            .eq("user_id", uid)
            .eq("project_id", project_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if fallback:
            return fallback[0], "project_fallback"
    except Exception:
        pass

    return None, "none"


def load_latest_compliance(sb, uid: str, project_id: str, identity: str):
    try:
        exact = rows(
            sb.table("grant_compliance_checks")
            .select("*")
            .eq("user_id", uid)
            .eq("project_id", project_id)
            .eq("opportunity_identity", identity)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if exact:
            return exact[0], "exact"
    except Exception:
        pass

    try:
        fallback = rows(
            sb.table("grant_compliance_checks")
            .select("*")
            .eq("user_id", uid)
            .eq("project_id", project_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if fallback:
            return fallback[0], "project_fallback"
    except Exception:
        pass

    return None, "none"


def extract_compliance_result(check: dict[str, Any] | None) -> dict[str, Any]:
    if not check:
        return {}

    for key in ("result", "check_result", "compliance_result"):
        value = check.get(key)
        if isinstance(value, dict):
            return value

    return {}


def load_latest_readiness_run(sb, uid: str, project_id: str, identity: str):
    try:
        exact = rows(
            sb.table("submission_readiness_runs")
            .select("*")
            .eq("user_id", uid)
            .eq("project_id", project_id)
            .eq("opportunity_identity", identity)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if exact:
            return exact[0], "exact"
    except Exception:
        pass

    try:
        fallback = rows(
            sb.table("submission_readiness_runs")
            .select("*")
            .eq("user_id", uid)
            .eq("project_id", project_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if fallback:
            return fallback[0], "project_fallback"
    except Exception:
        pass

    return None, "none"


def load_readiness_items(sb, uid: str, project_id: str, identity: str):
    try:
        exact = rows(
            sb.table("submission_readiness_items")
            .select("*")
            .eq("user_id", uid)
            .eq("project_id", project_id)
            .eq("opportunity_identity", identity)
            .order("created_at")
            .execute()
        )
        if exact:
            return exact, "exact"
    except Exception:
        pass

    try:
        fallback = rows(
            sb.table("submission_readiness_items")
            .select("*")
            .eq("user_id", uid)
            .eq("project_id", project_id)
            .order("created_at")
            .execute()
        )
        if fallback:
            return fallback, "project_fallback"
    except Exception:
        pass

    return [], "none"


def load_writer_sections(sb, uid: str, project_id: str, identity: str):
    try:
        exact = rows(
            sb.table("grant_writer_sections")
            .select("*")
            .eq("user_id", uid)
            .eq("project_id", project_id)
            .eq("opportunity_identity", identity)
            .order("updated_at", desc=True)
            .execute()
        )
        if exact:
            return exact, "exact"
    except Exception:
        pass

    try:
        fallback = rows(
            sb.table("grant_writer_sections")
            .select("*")
            .eq("user_id", uid)
            .eq("project_id", project_id)
            .order("updated_at", desc=True)
            .limit(100)
            .execute()
        )

        if fallback:
            did = fallback[0].get("document_id")
            if did:
                fallback = [
                    item
                    for item in fallback
                    if str(item.get("document_id")) == str(did)
                ]

            return fallback, "project_fallback"
    except Exception:
        pass

    return [], "none"


def normalize_score(value: Any) -> int:
    try:
        return max(0, min(100, int(round(float(value)))))
    except Exception:
        return 0


def find_writer_section(sections: list[dict[str, Any]], keyword: str) -> str:
    keyword = keyword.lower()

    for section in sections:
        name = str(
            section.get("section_title")
            or section.get("section_key")
            or section.get("section_name")
            or section.get("section")
            or section.get("title")
            or ""
        ).lower()

        if keyword in name:
            return str(
                section.get("content")
                or section.get("section_content")
                or section.get("text")
                or ""
            )

    return ""


def calculate_readiness_from_items(items: list[dict[str, Any]]) -> int:
    if not items:
        return 100

    weights = {"High": 3, "Medium": 2, "Low": 1}
    total_weight = 0
    done_weight = 0

    for item in items:
        weight = weights.get(item.get("priority"), 2)
        total_weight += weight

        if item.get("status") == "Done":
            done_weight += weight

    score = round((done_weight / total_weight) * 100) if total_weight else 100

    unresolved_blocker = any(
        bool(item.get("blocking"))
        and item.get("status") != "Done"
        for item in items
    )

    if unresolved_blocker and score > 79:
        score = 79

    return score


st.title("📦 Etapa 17 — AI Submission Pack")
st.caption(
    "Construiește pachetul final de depunere folosind rezultatele din Writer, "
    "Reviewer, Compliance și Submission Readiness."
)

try:
    sb = db()
except Exception as exc:
    st.error(f"Supabase nu este configurat corect: {exc}")
    st.stop()

uid = current_user_id(sb)
if not uid:
    st.error("Intră în cont din pagina principală și revino.")
    st.stop()

projects = load_projects(sb, uid)
if not projects:
    st.warning("Nu există proiecte disponibile.")
    st.stop()

project = st.selectbox(
    "Project",
    projects,
    format_func=project_label,
)
project_id = str(project["id"])

_, opportunities = load_opportunities(sb, uid, project_id)
if not opportunities:
    st.warning("Nu există oportunități salvate pentru proiect.")
    st.stop()

opportunity = st.selectbox(
    "Oportunitate",
    opportunities,
    format_func=opportunity_label,
)
identity = opportunity_identity(opportunity)

review, review_mode = load_latest_review(sb, uid, project_id, identity)
compliance, compliance_mode = load_latest_compliance(
    sb,
    uid,
    project_id,
    identity,
)
readiness_run, readiness_mode = load_latest_readiness_run(
    sb,
    uid,
    project_id,
    identity,
)
readiness_items, readiness_items_mode = load_readiness_items(
    sb,
    uid,
    project_id,
    identity,
)
writer_sections, writer_mode = load_writer_sections(
    sb,
    uid,
    project_id,
    identity,
)

if any(
    mode == "project_fallback"
    for mode in (
        review_mode,
        compliance_mode,
        readiness_mode,
        readiness_items_mode,
        writer_mode,
    )
):
    st.info(
        "Identificatorul oportunității diferă între module; "
        "s-au folosit cele mai recente date disponibile pentru proiect."
    )

compliance_result = extract_compliance_result(compliance)

review_score = normalize_score(
    review.get("overall_score", 0) if review else 0
)
compliance_score = normalize_score(
    compliance_result.get("compliance_score", 0)
)

stored_readiness_score = normalize_score(
    readiness_run.get("readiness_score", 0)
    if readiness_run
    else 0
)

live_readiness_score = calculate_readiness_from_items(readiness_items)
readiness_score = (
    live_readiness_score
    if readiness_items
    else stored_readiness_score
)

blocking_items = [
    item
    for item in readiness_items
    if (
        bool(item.get("blocking"))
        or item.get("status") == "Blocked"
    )
    and item.get("status") != "Done"
]

unresolved_items = [
    item
    for item in readiness_items
    if item.get("status") != "Done"
]

submission_blocked = (
    bool(compliance_result.get("submission_blocked"))
    or len(blocking_items) > 0
)

m1, m2, m3, m4 = st.columns(4)
m1.metric("Reviewer", f"{review_score}/100")
m2.metric("Compliance", f"{compliance_score}/100")
m3.metric("Readiness", f"{readiness_score}/100")
m4.metric(
    "Secțiuni Writer",
    len(
        [
            item
            for item in writer_sections
            if (item.get("content") or "").strip()
        ]
    ),
)

st.subheader("Submission Gate")

if submission_blocked:
    st.error(
        f"DEPUNERE BLOCATĂ — există {len(blocking_items)} "
        "probleme blocante nerezolvate."
    )
elif readiness_score < 80 or compliance_score < 70:
    st.warning(
        "Nu există blocaje explicite, dar scorurile sunt încă "
        "sub pragurile interne de pregătire."
    )
else:
    st.success(
        "READY FOR FINAL PACK — nu există blocaje detectate "
        "în datele disponibile."
    )

proposal_title = st.text_input(
    "Proposal title",
    value=(
        project.get("title")
        or project.get("project_name")
        or project.get("name")
        or ""
    ),
)

acronym = st.text_input(
    "Acronym",
    value=project.get("acronym") or "",
)

st.text_input(
    "Funding opportunity",
    value=identity,
    disabled=True,
)

excellence_content = find_writer_section(writer_sections, "excellence")
impact_content = find_writer_section(writer_sections, "impact")
implementation_content = find_writer_section(
    writer_sections,
    "implementation",
)

st.subheader("Conținut final")

tab1, tab2, tab3 = st.tabs(
    ["Excellence", "Impact", "Implementation"]
)

with tab1:
    excellence_content = st.text_area(
        "Excellence",
        value=excellence_content,
        height=320,
    )

with tab2:
    impact_content = st.text_area(
        "Impact",
        value=impact_content,
        height=320,
    )

with tab3:
    implementation_content = st.text_area(
        "Implementation",
        value=implementation_content,
        height=320,
    )

st.subheader("Probleme nerezolvate")

if unresolved_items:
    for item in unresolved_items:
        is_blocking = (
            bool(item.get("blocking"))
            or item.get("status") == "Blocked"
        )
        icon = "🔴" if is_blocking else "🟠"

        st.write(
            f"{icon} **{item.get('category') or 'General'}** — "
            f"{item.get('title') or item.get('description') or 'Task'} "
            f"({item.get('status')})"
        )
else:
    st.success("Nu există probleme nerezolvate în checklist.")

st.subheader("Final checks")

check_proposal = st.checkbox("Proposal content verificat")
check_budget = st.checkbox("Bugetul a fost verificat")
check_eligibility = st.checkbox(
    "Eligibilitatea solicitantului a fost verificată"
)
check_documents = st.checkbox(
    "Documentele obligatorii sunt pregătite"
)
check_call = st.checkbox(
    "Datele apelului au fost verificate în documentația oficială"
)

final_checks = [
    {"check": "proposal_content", "done": check_proposal},
    {"check": "budget", "done": check_budget},
    {"check": "eligibility", "done": check_eligibility},
    {"check": "documents", "done": check_documents},
    {"check": "official_call", "done": check_call},
]

all_final_checks = all(item["done"] for item in final_checks)

executive_summary = st.text_area(
    "Executive Summary",
    height=220,
    placeholder="Rezumatul executiv final al propunerii...",
)

if submission_blocked:
    pack_status = "blocked"
elif not all_final_checks:
    pack_status = "draft"
elif readiness_score < 80 or compliance_score < 70:
    pack_status = "needs_review"
else:
    pack_status = "ready"

generated_pack = {
    "project_id": project_id,
    "proposal_title": proposal_title,
    "acronym": acronym,
    "opportunity_identity": identity,
    "review_score": review_score,
    "compliance_score": compliance_score,
    "readiness_score": readiness_score,
    "submission_blocked": submission_blocked,
    "writer_sections": len(writer_sections),
    "unresolved_count": len(unresolved_items),
    "blocking_count": len(blocking_items),
    "final_checks_complete": all_final_checks,
    "generated_at": datetime.now(timezone.utc).isoformat(),
}

unresolved_payload = [
    {
        "id": str(item.get("id", "")),
        "task": item.get("title") or item.get("description") or "",
        "status": item.get("status"),
        "priority": item.get("priority"),
        "category": item.get("category"),
        "blocking": bool(item.get("blocking"))
        or item.get("status") == "Blocked",
    }
    for item in unresolved_items
]

if st.button(
    "📦 Generează / actualizează Submission Pack",
    type="primary",
    use_container_width=True,
):
    payload = {
        "user_id": uid,
        "project_id": project_id,
        "opportunity_identity": identity,
        "pack_status": pack_status,
        "readiness_score": readiness_score,
        "submission_blocked": submission_blocked,
        "proposal_title": proposal_title,
        "acronym": acronym,
        "executive_summary": executive_summary,
        "excellence_content": excellence_content,
        "impact_content": impact_content,
        "implementation_content": implementation_content,
        "compliance_summary": compliance_result,
        "readiness_summary": readiness_run or {},
        "unresolved_items": unresolved_payload,
        "final_checks": final_checks,
        "generated_pack": generated_pack,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        existing = rows(
            sb.table("submission_packs")
            .select("id")
            .eq("user_id", uid)
            .eq("project_id", project_id)
            .eq("opportunity_identity", identity)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

        if existing:
            response = (
                sb.table("submission_packs")
                .update(payload)
                .eq("id", existing[0]["id"])
                .eq("user_id", uid)
                .execute()
            )
        else:
            response = (
                sb.table("submission_packs")
                .insert(payload)
                .execute()
            )

        saved = rows(response)
        if saved and saved[0].get("id"):
            st.success(
                f"Submission Pack salvat. ID: "
                f"{str(saved[0]['id'])[:8]}"
            )
        else:
            st.success("Submission Pack salvat.")

    except Exception as exc:
        st.error(f"Nu am putut salva Submission Pack: {exc}")

st.subheader("Status final")

s1, s2, s3, s4 = st.columns(4)
s1.metric("Pack status", pack_status.upper())
s2.metric("Readiness", f"{readiness_score}/100")
s3.metric("Blocante", len(blocking_items))
s4.metric(
    "Final checks",
    f"{sum(item['done'] for item in final_checks)}/5",
)

if pack_status == "ready":
    st.success("Pachetul este pregătit pentru verificarea finală.")
elif pack_status == "blocked":
    st.error(
        "Pachetul nu poate fi declarat READY până când "
        "problemele blocante nu sunt rezolvate."
    )
else:
    st.warning("Pachetul este încă în lucru.")

st.caption(
    "Etapa 17 construiește un pachet de lucru. "
    "Eligibilitatea și cerințele de depunere trebuie validate în documentația oficială a apelului."
)
