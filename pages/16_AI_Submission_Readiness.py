import json
import os
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import streamlit as st
from openai import OpenAI
from supabase import create_client

st.set_page_config(
    page_title="Etapa 16 — Submission Readiness Manager",
    page_icon="✅",
    layout="wide",
)

PRIORITIES = ["High", "Medium", "Low"]
STATUSES = ["Open", "In progress", "Blocked", "Done"]
CATEGORIES = [
    "Eligibility",
    "Call alignment",
    "TRL",
    "KPIs",
    "Consortium",
    "Budget",
    "Risks",
    "Evidence",
    "Ethics",
    "Administrative",
    "Excellence",
    "Impact",
    "Implementation",
    "Other",
]


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
                result = (
                    sb.table(table)
                    .select("*")
                    .eq("user_id", uid)
                    .eq("project_id", project_id)
                    .execute()
                )
            except Exception:
                result = sb.table(table).select("*").execute()

            data = rows(result)
            if data:
                return table, data
        except Exception:
            continue
    return None, []


def load_latest_reviewer(sb, uid: str, project_id: str, identity: str):
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


def load_latest_optimization(sb, uid: str, project_id: str, identity: str):
    try:
        exact = rows(
            sb.table("grant_optimizations")
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
            sb.table("grant_optimizations")
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
                    x for x in fallback
                    if str(x.get("document_id")) == str(did)
                ]
            return fallback, "project_fallback"
    except Exception:
        pass

    return [], "none"


def compact_json(obj: Any, limit: int = 14000) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)[:limit]


def ai_client() -> OpenAI:
    return OpenAI(api_key=secret("OPENAI_API_KEY"))


def model_name() -> str:
    return secret("OPENAI_MODEL", "gpt-4.1-mini")


def clean_json(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = text.replace("```json", "", 1).replace("```", "").strip()
    return text


def generate_readiness_plan(
    project: dict[str, Any],
    opportunity: dict[str, Any],
    reviewer: dict[str, Any] | None,
    compliance: dict[str, Any] | None,
    optimization: dict[str, Any] | None,
    writer_sections: list[dict[str, Any]],
) -> dict[str, Any]:
    reviewer_result = (
        reviewer.get("evaluator_result") or reviewer
        if reviewer else {}
    )
    compliance_result = extract_compliance_result(compliance)

    prompt = f"""
You are an EU grant submission readiness manager.

Transform the supplied reviewer, compliance, optimizer and writer information
into a practical pre-submission action list.

CRITICAL RULES:
- Never invent official call requirements.
- Never invent TRL, budget, partners, KPIs, evidence, certifications or eligibility facts.
- If information is unknown, create a task to verify or provide it.
- Consolidate duplicates: one issue should become one actionable task.
- Prefer specific, concrete task titles.
- Blocking issues must be priority High unless clearly administrative and non-blocking.
- Return ONLY valid JSON.

Return exactly:
{{
  "summary": "",
  "readiness_assessment": "Ready|Almost ready|Needs work|Blocked|Insufficient call data",
  "items": [
    {{
      "source_type": "reviewer|optimizer|compliance|writer|manual",
      "category": "Eligibility|Call alignment|TRL|KPIs|Consortium|Budget|Risks|Evidence|Ethics|Administrative|Excellence|Impact|Implementation|Other",
      "title": "",
      "description": "",
      "priority": "High|Medium|Low",
      "blocking": false,
      "evidence_required": ""
    }}
  ]
}}

PROJECT:
{compact_json(project)}

OPPORTUNITY:
{compact_json(opportunity)}

REVIEWER:
{compact_json(reviewer_result)}

COMPLIANCE:
{compact_json(compliance_result)}

OPTIMIZER:
{compact_json(optimization or {})}

WRITER SECTIONS:
{compact_json(writer_sections)}
"""

    response = ai_client().responses.create(
        model=model_name(),
        instructions=(
            "Return valid JSON only. "
            "Create actionable submission-readiness tasks without inventing facts."
        ),
        input=prompt,
    )

    result = json.loads(clean_json(response.output_text))
    result.setdefault("summary", "")
    result.setdefault("readiness_assessment", "Needs work")
    result.setdefault("items", [])
    return result


def normalize_category(value: str) -> str:
    value = (value or "Other").strip()
    return value if value in CATEGORIES else "Other"


def normalize_priority(value: str, blocking: bool) -> str:
    if blocking:
        return "High"
    value = (value or "Medium").strip()
    return value if value in PRIORITIES else "Medium"


def item_fingerprint(item: dict[str, Any]) -> str:
    return (
        f"{item.get('category','').strip().lower()}|"
        f"{item.get('title','').strip().lower()}"
    )


def existing_fingerprints(items: list[dict[str, Any]]) -> set[str]:
    return {item_fingerprint(item) for item in items}


def save_generated_items(
    sb,
    uid: str,
    project_id: str,
    identity: str,
    result: dict[str, Any],
):
    existing = rows(
        sb.table("submission_readiness_items")
        .select("*")
        .eq("user_id", uid)
        .eq("project_id", project_id)
        .eq("opportunity_identity", identity)
        .execute()
    )

    fingerprints = existing_fingerprints(existing)
    payloads = []

    for item in result.get("items", []):
        blocking = bool(item.get("blocking"))
        payload = {
            "user_id": uid,
            "project_id": project_id,
            "opportunity_identity": identity,
            "source_type": (
                item.get("source_type")
                if item.get("source_type") in (
                    "reviewer", "optimizer", "compliance", "writer", "manual"
                )
                else "manual"
            ),
            "category": normalize_category(item.get("category")),
            "title": str(item.get("title") or "Task")[:500],
            "description": str(item.get("description") or ""),
            "priority": normalize_priority(item.get("priority"), blocking),
            "status": "Blocked" if blocking else "Open",
            "blocking": blocking,
            "evidence_required": str(item.get("evidence_required") or ""),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        if item_fingerprint(payload) not in fingerprints:
            payloads.append(payload)
            fingerprints.add(item_fingerprint(payload))

    if payloads:
        sb.table("submission_readiness_items").insert(payloads).execute()

    return len(payloads)


def load_items(sb, uid: str, project_id: str, identity: str):
    try:
        return rows(
            sb.table("submission_readiness_items")
            .select("*")
            .eq("user_id", uid)
            .eq("project_id", project_id)
            .eq("opportunity_identity", identity)
            .order("blocking", desc=True)
            .order("priority")
            .order("created_at")
            .execute()
        )
    except Exception:
        return rows(
            sb.table("submission_readiness_items")
            .select("*")
            .eq("user_id", uid)
            .eq("project_id", project_id)
            .execute()
        )


def calculate_readiness(items: list[dict[str, Any]]) -> dict[str, int]:
    total = len(items)
    done = sum(1 for x in items if x.get("status") == "Done")
    blocked = sum(1 for x in items if x.get("status") == "Blocked")
    open_items = sum(
        1 for x in items if x.get("status") in ("Open", "In progress")
    )

    weighted_total = 0
    weighted_done = 0
    weight_map = {"High": 3, "Medium": 2, "Low": 1}

    for item in items:
        weight = weight_map.get(item.get("priority"), 2)
        weighted_total += weight
        if item.get("status") == "Done":
            weighted_done += weight

    score = round((weighted_done / weighted_total) * 100) if weighted_total else 100

    # Any unresolved blocking item prevents a "ready" interpretation.
    unresolved_blockers = sum(
        1
        for x in items
        if x.get("blocking") and x.get("status") != "Done"
    )
    if unresolved_blockers and score > 79:
        score = 79

    return {
        "score": score,
        "total": total,
        "done": done,
        "blocked": blocked,
        "open": open_items,
        "unresolved_blockers": unresolved_blockers,
    }


def save_run(
    sb,
    uid: str,
    project_id: str,
    identity: str,
    metrics: dict[str, int],
    summary: dict[str, Any],
):
    payload = {
        "user_id": uid,
        "project_id": project_id,
        "opportunity_identity": identity,
        "readiness_score": metrics["score"],
        "total_items": metrics["total"],
        "open_items": metrics["open"],
        "blocked_items": metrics["blocked"],
        "done_items": metrics["done"],
        "summary": summary,
    }
    sb.table("submission_readiness_runs").insert(payload).execute()


def update_item(sb, item_id: str, status: str, notes: str):
    sb.table("submission_readiness_items").update(
        {
            "status": status,
            "resolution_notes": notes,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    ).eq("id", item_id).execute()


def add_manual_item(
    sb,
    uid: str,
    project_id: str,
    identity: str,
    category: str,
    title: str,
    description: str,
    priority: str,
    blocking: bool,
    evidence_required: str,
):
    sb.table("submission_readiness_items").insert(
        {
            "user_id": uid,
            "project_id": project_id,
            "opportunity_identity": identity,
            "source_type": "manual",
            "category": category,
            "title": title,
            "description": description,
            "priority": "High" if blocking else priority,
            "status": "Blocked" if blocking else "Open",
            "blocking": blocking,
            "evidence_required": evidence_required,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    ).execute()


def delete_item(sb, item_id: str):
    sb.table("submission_readiness_items").delete().eq("id", item_id).execute()


st.title("✅ Etapa 16 — Submission Readiness Manager")
st.caption(
    "Transformă problemele din Reviewer, Optimizer și Compliance într-un checklist executabil înainte de depunere."
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
    st.warning("Nu există proiecte.")
    st.stop()

project = st.selectbox("Project", projects, format_func=project_label)
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

reviewer, reviewer_mode = load_latest_reviewer(sb, uid, project_id, identity)
compliance, compliance_mode = load_latest_compliance(sb, uid, project_id, identity)
optimization, optimization_mode = load_latest_optimization(sb, uid, project_id, identity)
writer_sections, writer_mode = load_writer_sections(sb, uid, project_id, identity)

if any(
    mode == "project_fallback"
    for mode in (
        reviewer_mode,
        compliance_mode,
        optimization_mode,
        writer_mode,
    )
):
    st.info(
        "Identificatorul oportunității diferă între module; "
        "s-au folosit cele mai recente date disponibile pentru proiect."
    )

compliance_result = extract_compliance_result(compliance)

c1, c2, c3, c4 = st.columns(4)
c1.metric(
    "Reviewer",
    f"{reviewer.get('overall_score', 0)}/100" if reviewer else "—",
)
c2.metric(
    "Compliance",
    f"{compliance_result.get('compliance_score', 0)}/100"
    if compliance_result
    else "—",
)
c3.metric(
    "Depunere",
    (
        "BLOCATĂ"
        if compliance_result.get("submission_blocked")
        else "Neblocată"
    )
    if compliance_result
    else "—",
)
c4.metric(
    "Secțiuni Writer",
    len([x for x in writer_sections if (x.get("content") or "").strip()]),
)

if not reviewer and not compliance_result:
    st.warning(
        "Nu există suficiente rezultate din Etapele 13 și 15. "
        "Rulează Reviewer și Compliance Checker înainte de a genera planul."
    )

if st.button(
    "Generează / actualizează planul de pregătire",
    type="primary",
    use_container_width=True,
):
    with st.spinner("AI consolidează problemele și creează task-urile de pregătire..."):
        try:
            result = generate_readiness_plan(
                project,
                opportunity,
                reviewer,
                compliance,
                optimization,
                writer_sections,
            )
            created = save_generated_items(
                sb,
                uid,
                project_id,
                identity,
                result,
            )
            st.session_state["stage16_summary"] = result
            st.success(f"Plan actualizat. {created} task-uri noi au fost adăugate.")
            st.rerun()
        except Exception as exc:
            st.error(f"Nu am putut genera planul: {exc}")

items = load_items(sb, uid, project_id, identity)
metrics = calculate_readiness(items)

st.subheader("Submission Readiness")
m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Scor", f"{metrics['score']}/100")
m2.metric("Total", metrics["total"])
m3.metric("Deschise", metrics["open"])
m4.metric("Blocate", metrics["blocked"])
m5.metric("Finalizate", metrics["done"])

st.progress(metrics["score"])

if metrics["unresolved_blockers"]:
    st.error(
        f"Există {metrics['unresolved_blockers']} probleme blocante nerezolvate."
    )
elif metrics["score"] >= 90:
    st.success("Checklist-ul este aproape complet.")
elif metrics["total"]:
    st.warning("Mai există task-uri de rezolvat înainte de depunere.")
else:
    st.info("Nu există încă task-uri. Generează planul de pregătire.")

tab_tasks, tab_add, tab_history = st.tabs(
    ["Checklist", "Adaugă manual", "Istoric"]
)

with tab_tasks:
    if not items:
        st.info("Nu există task-uri.")
    else:
        filters = st.columns(3)
        status_filter = filters[0].multiselect(
            "Status",
            STATUSES,
            default=STATUSES,
        )
        priority_filter = filters[1].multiselect(
            "Prioritate",
            PRIORITIES,
            default=PRIORITIES,
        )
        category_filter = filters[2].multiselect(
            "Categorie",
            CATEGORIES,
            default=CATEGORIES,
        )

        filtered = [
            x for x in items
            if x.get("status") in status_filter
            and x.get("priority") in priority_filter
            and x.get("category") in category_filter
        ]

        if filtered:
            df = pd.DataFrame(
                [
                    {
                        "Prioritate": x.get("priority"),
                        "Status": x.get("status"),
                        "Blocant": "Da" if x.get("blocking") else "Nu",
                        "Categorie": x.get("category"),
                        "Task": x.get("title"),
                        "Sursă": x.get("source_type"),
                    }
                    for x in filtered
                ]
            )
            st.dataframe(df, hide_index=True, use_container_width=True)

        for item in filtered:
            icon = "🚫" if item.get("blocking") and item.get("status") != "Done" else "📌"
            label = (
                f"{icon} {item.get('priority')} · {item.get('status')} · "
                f"{item.get('category')} — {item.get('title')}"
            )
            with st.expander(label):
                st.write(item.get("description") or "—")
                if item.get("evidence_required"):
                    st.write(
                        f"**Dovadă / informație necesară:** "
                        f"{item.get('evidence_required')}"
                    )
                st.caption(f"Sursă: {item.get('source_type')}")

                current_status = item.get("status") or "Open"
                status = st.selectbox(
                    "Status",
                    STATUSES,
                    index=STATUSES.index(current_status)
                    if current_status in STATUSES
                    else 0,
                    key=f"status_{item['id']}",
                )
                notes = st.text_area(
                    "Note de rezolvare",
                    value=item.get("resolution_notes") or "",
                    key=f"notes_{item['id']}",
                )

                a, b = st.columns([4, 1])
                if a.button(
                    "Salvează task-ul",
                    key=f"save_{item['id']}",
                    use_container_width=True,
                ):
                    try:
                        update_item(sb, item["id"], status, notes)
                        st.success("Task actualizat.")
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))

                if b.button(
                    "Șterge",
                    key=f"delete_{item['id']}",
                    use_container_width=True,
                ):
                    try:
                        delete_item(sb, item["id"])
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))

with tab_add:
    with st.form("manual_task"):
        category = st.selectbox("Categorie", CATEGORIES)
        title = st.text_input("Titlu task")
        description = st.text_area("Descriere")
        priority = st.selectbox("Prioritate", PRIORITIES, index=1)
        blocking = st.checkbox("Problemă blocantă")
        evidence = st.text_input("Dovadă / informație necesară")
        submitted = st.form_submit_button(
            "Adaugă task",
            use_container_width=True,
        )

        if submitted:
            if not title.strip():
                st.error("Titlul este obligatoriu.")
            else:
                try:
                    add_manual_item(
                        sb,
                        uid,
                        project_id,
                        identity,
                        category,
                        title.strip(),
                        description.strip(),
                        priority,
                        blocking,
                        evidence.strip(),
                    )
                    st.success("Task adăugat.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))

with tab_history:
    runs = rows(
        sb.table("submission_readiness_runs")
        .select("*")
        .eq("user_id", uid)
        .eq("project_id", project_id)
        .eq("opportunity_identity", identity)
        .order("created_at", desc=True)
        .limit(30)
        .execute()
    )

    if not runs:
        st.info("Nu există încă snapshot-uri salvate.")
    else:
        table = pd.DataFrame(
            [
                {
                    "Data": run.get("created_at"),
                    "Scor": run.get("readiness_score"),
                    "Total": run.get("total_items"),
                    "Deschise": run.get("open_items"),
                    "Blocate": run.get("blocked_items"),
                    "Finalizate": run.get("done_items"),
                }
                for run in runs
            ]
        )
        st.dataframe(table, hide_index=True, use_container_width=True)

st.divider()

if st.button(
    "Salvează snapshot-ul curent",
    use_container_width=True,
):
    try:
        summary = st.session_state.get("stage16_summary") or {
            "note": "Snapshot manual"
        }
        save_run(
            sb,
            uid,
            project_id,
            identity,
            metrics,
            summary,
        )
        st.success("Snapshot salvat în istoric.")
    except Exception as exc:
        st.error(str(exc))

st.caption(
    "Scorul Submission Readiness reflectă finalizarea task-urilor din checklist. "
    "Nu reprezintă o confirmare oficială de eligibilitate sau finanțare."
)
