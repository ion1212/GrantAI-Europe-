import streamlit as st
from datetime import datetime, timezone

st.set_page_config(
    page_title="AI Submission Pack",
    page_icon="📦",
    layout="wide",
)

st.title("📦 Etapa 17 — AI Submission Pack")
st.caption(
    "Construiește pachetul final de depunere folosind rezultatele din Writer, "
    "Reviewer, Compliance și Submission Readiness."
)

# ---------------------------------------------------------
# SUPABASE
# ---------------------------------------------------------

if "supabase" not in st.session_state:
    st.error("Conexiunea Supabase nu este disponibilă.")
    st.stop()

supabase = st.session_state["supabase"]

user = st.session_state.get("user")

if not user:
    st.warning("Trebuie să fii autentificat.")
    st.stop()

user_id = user.id if hasattr(user, "id") else user.get("id")

if not user_id:
    st.error("Nu am putut identifica utilizatorul.")
    st.stop()


# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------

def safe_execute(query):
    try:
        return query.execute()
    except Exception:
        return None


def get_rows(response):
    if response is None:
        return []

    data = getattr(response, "data", None)

    if isinstance(data, list):
        return data

    return []


def latest_row(table, project_id):
    response = safe_execute(
        supabase.table(table)
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .order("created_at", desc=True)
        .limit(1)
    )

    rows = get_rows(response)

    return rows[0] if rows else None


def first_value(data, keys, default=None):
    if not isinstance(data, dict):
        return default

    for key in keys:
        value = data.get(key)

        if value not in (None, "", [], {}):
            return value

    return default


def normalize_score(value):
    try:
        value = int(float(value))
        return max(0, min(100, value))
    except Exception:
        return 0


# ---------------------------------------------------------
# PROJECTS
# ---------------------------------------------------------

projects_response = safe_execute(
    supabase.table("projects")
    .select("*")
    .eq("user_id", user_id)
)

projects = get_rows(projects_response)

if not projects:
    st.warning("Nu există proiecte disponibile.")
    st.stop()


project_options = {}

for project in projects:
    project_id = project.get("id")

    name = (
        project.get("name")
        or project.get("project_name")
        or project.get("title")
        or "Project"
    )

    short_id = str(project_id)[:8]

    project_options[f"{name} — {short_id}"] = project


selected_project_label = st.selectbox(
    "Project",
    list(project_options.keys()),
)

project = project_options[selected_project_label]

project_id = project.get("id")


# ---------------------------------------------------------
# LOAD PREVIOUS STAGES
# ---------------------------------------------------------

review = latest_row("proposal_reviews", project_id)
compliance = latest_row("grant_compliance_checks", project_id)
readiness = latest_row("submission_readiness_runs", project_id)


# Writer sections
writer_response = safe_execute(
    supabase.table("proposal_sections")
    .select("*")
    .eq("user_id", user_id)
    .eq("project_id", project_id)
)

writer_sections = get_rows(writer_response)


# ---------------------------------------------------------
# SCORES
# ---------------------------------------------------------

review_score = normalize_score(
    first_value(
        review,
        [
            "overall_score",
            "score",
            "total_score",
        ],
        0,
    )
)

compliance_score = normalize_score(
    first_value(
        compliance,
        [
            "compliance_score",
            "score",
            "overall_score",
        ],
        0,
    )
)

readiness_score = normalize_score(
    first_value(
        readiness,
        [
            "readiness_score",
            "score",
            "overall_score",
        ],
        0,
    )
)


# ---------------------------------------------------------
# READINESS ITEMS
# ---------------------------------------------------------

readiness_items = []

if readiness:
    readiness_id = readiness.get("id")

    if readiness_id:
        items_response = safe_execute(
            supabase.table("submission_readiness_items")
            .select("*")
            .eq("user_id", user_id)
            .eq("run_id", readiness_id)
        )

        readiness_items = get_rows(items_response)


blocked_items = []

open_items = []

done_items = []

for item in readiness_items:

    status = str(item.get("status", "")).lower()

    is_blocking = bool(
        item.get("is_blocking")
        or item.get("blocking")
        or item.get("blocker")
    )

    if status == "done":
        done_items.append(item)

    elif status in ["open", "in progress", "in_progress"]:
        open_items.append(item)

    if status == "blocked" or (
        is_blocking and status != "done"
    ):
        blocked_items.append(item)


submission_blocked = len(blocked_items) > 0


# ---------------------------------------------------------
# HEADER METRICS
# ---------------------------------------------------------

col1, col2, col3, col4 = st.columns(4)

col1.metric(
    "Reviewer",
    f"{review_score}/100",
)

col2.metric(
    "Compliance",
    f"{compliance_score}/100",
)

col3.metric(
    "Readiness",
    f"{readiness_score}/100",
)

col4.metric(
    "Writer sections",
    len(writer_sections),
)


st.divider()


# ---------------------------------------------------------
# SUBMISSION GATE
# ---------------------------------------------------------

st.subheader("Submission Gate")

if submission_blocked:

    st.error(
        f"DEPUNERE BLOCATĂ — există "
        f"{len(blocked_items)} probleme blocante."
    )

else:

    if readiness_score >= 80 and compliance_score >= 70:

        st.success(
            "READY FOR FINAL PACK — nu există probleme "
            "blocante detectate."
        )

    else:

        st.warning(
            "Nu există blocaje explicite, dar scorurile "
            "trebuie îmbunătățite înainte de depunere."
        )


# ---------------------------------------------------------
# PROJECT INFORMATION
# ---------------------------------------------------------

st.subheader("Date pachet")

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

opportunity_identity = (
    first_value(readiness, ["opportunity_identity"])
    or first_value(compliance, ["opportunity_identity"])
    or first_value(review, ["opportunity_identity"])
    or ""
)

st.text_input(
    "Funding opportunity",
    value=str(opportunity_identity),
    disabled=True,
)


# ---------------------------------------------------------
# WRITER CONTENT
# ---------------------------------------------------------

st.subheader("Conținut Proposal Writer")

section_map = {}

for section in writer_sections:

    section_name = str(
        section.get("section_name")
        or section.get("section")
        or section.get("title")
        or ""
    ).lower()

    content = (
        section.get("content")
        or section.get("section_content")
        or section.get("text")
        or ""
    )

    if section_name:
        section_map[section_name] = content


def find_section(keyword):
    for name, content in section_map.items():
        if keyword in name:
            return content

    return ""


excellence_content = find_section("excellence")
impact_content = find_section("impact")
implementation_content = find_section("implementation")


tab1, tab2, tab3 = st.tabs(
    [
        "Excellence",
        "Impact",
        "Implementation",
    ]
)

with tab1:
    excellence_content = st.text_area(
        "Excellence",
        value=excellence_content,
        height=300,
    )

with tab2:
    impact_content = st.text_area(
        "Impact",
        value=impact_content,
        height=300,
    )

with tab3:
    implementation_content = st.text_area(
        "Implementation",
        value=implementation_content,
        height=300,
    )


# ---------------------------------------------------------
# UNRESOLVED ITEMS
# ---------------------------------------------------------

st.subheader("Probleme nerezolvate")

unresolved_items = []

for item in readiness_items:

    status = str(item.get("status", "")).lower()

    if status != "done":

        unresolved_items.append(
            {
                "id": str(item.get("id", "")),
                "task": (
                    item.get("task")
                    or item.get("title")
                    or item.get("description")
                    or ""
                ),
                "status": item.get("status"),
                "priority": item.get("priority"),
                "category": item.get("category"),
                "blocking": bool(
                    item.get("is_blocking")
                    or item.get("blocking")
                    or item.get("blocker")
                    or status == "blocked"
                ),
            }
        )


if unresolved_items:

    for item in unresolved_items:

        icon = "🔴" if item["blocking"] else "🟠"

        st.write(
            f"{icon} **{item['category'] or 'General'}** — "
            f"{item['task']}"
        )

else:

    st.success("Nu există probleme nerezolvate.")


# ---------------------------------------------------------
# FINAL CHECKS
# ---------------------------------------------------------

st.subheader("Final checks")

check_proposal = st.checkbox(
    "Proposal content verificat"
)

check_budget = st.checkbox(
    "Bugetul a fost verificat"
)

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
    {
        "check": "proposal_content",
        "done": check_proposal,
    },
    {
        "check": "budget",
        "done": check_budget,
    },
    {
        "check": "eligibility",
        "done": check_eligibility,
    },
    {
        "check": "documents",
        "done": check_documents,
    },
    {
        "check": "official_call",
        "done": check_call,
    },
]

all_final_checks = all(
    item["done"]
    for item in final_checks
)


# ---------------------------------------------------------
# EXECUTIVE SUMMARY
# ---------------------------------------------------------

executive_summary = st.text_area(
    "Executive Summary",
    height=200,
    placeholder=(
        "Rezumatul executiv final al propunerii..."
    ),
)


# ---------------------------------------------------------
# FINAL PACK STATUS
# ---------------------------------------------------------

if submission_blocked:

    pack_status = "blocked"

elif not all_final_checks:

    pack_status = "draft"

elif readiness_score < 80:

    pack_status = "needs_review"

else:

    pack_status = "ready"


generated_pack = {
    "project_id": str(project_id),
    "proposal_title": proposal_title,
    "acronym": acronym,
    "opportunity_identity": opportunity_identity,
    "review_score": review_score,
    "compliance_score": compliance_score,
    "readiness_score": readiness_score,
    "submission_blocked": submission_blocked,
    "writer_sections": len(writer_sections),
    "unresolved_count": len(unresolved_items),
    "blocking_count": len(blocked_items),
    "final_checks_complete": all_final_checks,
    "generated_at": datetime.now(
        timezone.utc
    ).isoformat(),
}


# ---------------------------------------------------------
# SAVE
# ---------------------------------------------------------

st.divider()

if st.button(
    "📦 Generează / actualizează Submission Pack",
    type="primary",
    use_container_width=True,
):

    payload = {
        "user_id": str(user_id),
        "project_id": str(project_id),
        "opportunity_identity": str(opportunity_identity),
        "pack_status": pack_status,
        "readiness_score": readiness_score,
        "submission_blocked": submission_blocked,
        "proposal_title": proposal_title,
        "acronym": acronym,
        "executive_summary": executive_summary,
        "excellence_content": excellence_content,
        "impact_content": impact_content,
        "implementation_content": implementation_content,
        "compliance_summary": compliance or {},
        "readiness_summary": readiness or {},
        "unresolved_items": unresolved_items,
        "final_checks": final_checks,
        "generated_pack": generated_pack,
        "updated_at": datetime.now(
            timezone.utc
        ).isoformat(),
    }

    try:

        existing_response = (
            supabase.table("submission_packs")
            .select("id")
            .eq("user_id", user_id)
            .eq("project_id", project_id)
            .eq(
                "opportunity_identity",
                str(opportunity_identity),
            )
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

        existing = get_rows(existing_response)

        if existing:

            pack_id = existing[0]["id"]

            result = (
                supabase.table("submission_packs")
                .update(payload)
                .eq("id", pack_id)
                .eq("user_id", user_id)
                .execute()
            )

        else:

            result = (
                supabase.table("submission_packs")
                .insert(payload)
                .execute()
            )

        saved = get_rows(result)

        if saved:

            pack_id = saved[0].get("id")

            st.success(
                f"Submission Pack salvat. ID: "
                f"{str(pack_id)[:8]}"
            )

        else:

            st.success("Submission Pack salvat.")

    except Exception as exc:

        st.error(
            f"Nu am putut salva Submission Pack: {exc}"
        )


# ---------------------------------------------------------
# FINAL STATUS
# ---------------------------------------------------------

st.subheader("Status final")

c1, c2, c3, c4 = st.columns(4)

c1.metric(
    "Pack status",
    pack_status.upper(),
)

c2.metric(
    "Readiness",
    f"{readiness_score}/100",
)

c3.metric(
    "Blocante",
    len(blocked_items),
)

c4.metric(
    "Final checks",
    f"{sum(x['done'] for x in final_checks)}/5",
)


if pack_status == "ready":

    st.success(
        "Pachetul este pregătit pentru verificarea finală."
    )

elif pack_status == "blocked":

    st.error(
        "Pachetul nu poate fi declarat READY până când "
        "problemele blocante nu sunt rezolvate."
    )

else:

    st.warning(
        "Pachetul este încă în lucru."
    )
