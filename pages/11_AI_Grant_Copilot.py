import json
import os
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import streamlit as st
from openai import OpenAI
from supabase import Client, create_client

st.set_page_config(page_title="AI Grant Copilot", page_icon="🧭", layout="wide")

REVIEW_AREAS = [
    "Eligibility",
    "Excellence",
    "Impact",
    "Implementation",
    "Innovation",
    "Budget",
    "Consortium",
    "Risks",
    "Sustainability",
    "Dissemination",
]

PRIORITIES = ["High", "Medium", "Low"]


# ---------- configuration and authentication ----------

def get_secret(name: str, default: str = "") -> str:
    try:
        return str(st.secrets.get(name, default))
    except Exception:
        return os.getenv(name, default)


@st.cache_resource
def get_supabase() -> Client:
    url = get_secret("SUPABASE_URL")
    key = get_secret("SUPABASE_ANON_KEY")
    if not url or not key:
        raise RuntimeError("Lipsesc SUPABASE_URL sau SUPABASE_ANON_KEY.")
    return create_client(url, key)


def user_id() -> str | None:
    user = st.session_state.get("auth_user")
    return getattr(user, "id", None) if user else None


def require_login() -> None:
    if not user_id():
        st.error("Autentifică-te mai întâi în pagina principală GrantAI Europe.")
        st.stop()


def ai_client() -> OpenAI:
    key = get_secret("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("Lipsește OPENAI_API_KEY.")
    return OpenAI(api_key=key)


def model_name() -> str:
    return get_secret("OPENAI_MODEL", "gpt-4.1-mini")


# ---------- generic helpers ----------

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def opportunity_identity(item: dict[str, Any]) -> str:
    return str(
        item.get("id")
        or item.get("reference")
        or item.get("identity")
        or item.get("title")
        or ""
    )


def clean_json_text(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = text.replace("```json", "", 1).replace("```", "").strip()
    return text


def call_ai_json(prompt: str) -> dict[str, Any]:
    response = ai_client().responses.create(
        model=model_name(),
        instructions=(
            "You are a senior EU grants consultant and evaluator. "
            "Return valid JSON only. Never invent official call requirements, "
            "eligibility, budgets, consortium members, TRL, evidence or results. "
            "Explicitly label missing information."
        ),
        input=prompt,
    )
    raw = clean_json_text(response.output_text)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "AI nu a returnat JSON valid. Încearcă din nou. "
            f"Detaliu tehnic: {exc}"
        ) from exc


def call_ai_text(prompt: str) -> str:
    response = ai_client().responses.create(
        model=model_name(),
        instructions=(
            "You are a senior EU grants consultant. Use only the supplied context. "
            "Do not invent official requirements or facts. Clearly mark unknowns."
        ),
        input=prompt,
    )
    return response.output_text


# ---------- Supabase reads ----------

def load_organisation() -> dict[str, Any]:
    result = get_supabase().table("organisations").select("data").limit(1).execute()
    return result.data[0]["data"] if result.data else {}


def load_projects() -> list[dict[str, Any]]:
    result = (
        get_supabase()
        .table("projects")
        .select("id,data,updated_at")
        .order("updated_at", desc=True)
        .execute()
    )
    return [{"id": row["id"], **(row.get("data") or {})} for row in result.data]


def load_opportunities() -> list[dict[str, Any]]:
    result = (
        get_supabase()
        .table("opportunities")
        .select("identity,data,updated_at")
        .order("updated_at", desc=True)
        .execute()
    )
    items = []
    for row in result.data:
        item = dict(row.get("data") or {})
        item.setdefault("identity", row.get("identity"))
        items.append(item)
    return items


def load_latest_proposal(project_id: str, identity: str) -> dict[str, Any] | None:
    """
    Încearcă mai întâi schema nouă Etapa 10.
    Dacă baza de date folosește schema veche proposal_versions, face fallback
    la ultima propunere a proiectului și normalizează câmpurile.
    """
    try:
        result = (
            get_supabase()
            .table("proposal_versions")
            .select("*")
            .eq("project_id", project_id)
            .eq("opportunity_identity", identity)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
    except Exception:
        try:
            result = (
                get_supabase()
                .table("proposal_versions")
                .select("*")
                .eq("project_id", project_id)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
        except Exception:
            return None

    if not result.data:
        return None

    proposal = dict(result.data[0])
    proposal.setdefault(
        "document_type",
        proposal.get("section") or proposal.get("title") or "Proposal draft",
    )
    proposal.setdefault("version", 1)
    proposal.setdefault("content", "")
    return proposal


def load_match(project_id: str, identity: str) -> dict[str, Any] | None:
    result = (
        get_supabase()
        .table("grant_matches")
        .select("*")
        .eq("project_id", project_id)
        .eq("opportunity_identity", identity)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def load_reviews(project_id: str, identity: str) -> list[dict[str, Any]]:
    return (
        get_supabase()
        .table("grant_reviews")
        .select("*")
        .eq("project_id", project_id)
        .eq("opportunity_identity", identity)
        .order("created_at", desc=True)
        .execute()
        .data
    )


def load_tasks(project_id: str, identity: str) -> list[dict[str, Any]]:
    return (
        get_supabase()
        .table("grant_tasks")
        .select("*")
        .eq("project_id", project_id)
        .eq("opportunity_identity", identity)
        .order("created_at", desc=False)
        .execute()
        .data
    )


def load_chat(project_id: str, identity: str) -> list[dict[str, Any]]:
    return (
        get_supabase()
        .table("grant_chat_messages")
        .select("*")
        .eq("project_id", project_id)
        .eq("opportunity_identity", identity)
        .order("created_at", desc=False)
        .execute()
        .data
    )


# ---------- Supabase writes ----------

def save_match(
    project_id: str,
    identity: str,
    result: dict[str, Any],
) -> None:
    payload = {
        "user_id": user_id(),
        "project_id": project_id,
        "opportunity_identity": identity,
        "match_score": int(result.get("match_score", 0)),
        "eligibility_score": int(result.get("eligibility_score", 0)),
        "recommendation": str(result.get("recommendation", "Review")),
        "analysis": result,
        "updated_at": utc_now(),
    }
    existing = (
        get_supabase()
        .table("grant_matches")
        .select("id")
        .eq("project_id", project_id)
        .eq("opportunity_identity", identity)
        .limit(1)
        .execute()
    )
    if existing.data:
        (
            get_supabase()
            .table("grant_matches")
            .update(payload)
            .eq("id", existing.data[0]["id"])
            .execute()
        )
    else:
        get_supabase().table("grant_matches").insert(payload).execute()


def save_review(
    project_id: str,
    identity: str,
    review_type: str,
    result: dict[str, Any],
    proposal_version_id: str | None = None,
) -> str:
    payload = {
        "user_id": user_id(),
        "project_id": project_id,
        "opportunity_identity": identity,
        "proposal_version_id": proposal_version_id,
        "review_type": review_type,
        "overall_score": int(result.get("overall_score", 0)),
        "result": result,
    }
    inserted = get_supabase().table("grant_reviews").insert(payload).execute()
    return inserted.data[0]["id"] if inserted.data else ""


def save_generated_tasks(
    project_id: str,
    identity: str,
    review_id: str,
    tasks: list[dict[str, Any]],
) -> int:
    rows = []
    for task in tasks:
        title = str(task.get("title", "")).strip()
        if not title:
            continue
        priority = str(task.get("priority", "Medium")).title()
        if priority not in PRIORITIES:
            priority = "Medium"
        rows.append({
            "user_id": user_id(),
            "project_id": project_id,
            "opportunity_identity": identity,
            "review_id": review_id or None,
            "title": title,
            "description": str(task.get("description", "")),
            "priority": priority,
            "status": "Open",
        })
    if rows:
        get_supabase().table("grant_tasks").insert(rows).execute()
    return len(rows)


def update_task(task_id: str, status: str) -> None:
    (
        get_supabase()
        .table("grant_tasks")
        .update({"status": status, "updated_at": utc_now()})
        .eq("id", task_id)
        .execute()
    )


def save_chat_message(
    project_id: str,
    identity: str,
    role: str,
    content: str,
) -> None:
    get_supabase().table("grant_chat_messages").insert({
        "user_id": user_id(),
        "project_id": project_id,
        "opportunity_identity": identity,
        "role": role,
        "content": content,
    }).execute()


# ---------- AI analyses ----------

def smart_match_analysis(
    organisation: dict[str, Any],
    project: dict[str, Any],
    opportunity: dict[str, Any],
) -> dict[str, Any]:
    prompt = f"""
Assess the fit between this applicant/project and this EU funding opportunity.

Return exactly this JSON structure:
{{
  "match_score": 0,
  "eligibility_score": 0,
  "recommendation": "Apply|Conditional|Do not apply|Insufficient information",
  "confidence": "High|Medium|Low",
  "strengths": [],
  "weaknesses": [],
  "eligibility_confirmed": [],
  "eligibility_unknown": [],
  "critical_questions": [],
  "reasoning": ""
}}

Scoring rules:
- Scores must be integers from 0 to 100.
- A high thematic fit is not proof of eligibility.
- If official eligibility data is absent, reduce confidence and list it as unknown.
- Do not claim the applicant is eligible unless the supplied opportunity explicitly proves it.

ORGANISATION:
{json.dumps(organisation, ensure_ascii=False, indent=2)}

PROJECT:
{json.dumps(project, ensure_ascii=False, indent=2)}

OPPORTUNITY:
{json.dumps(opportunity, ensure_ascii=False, indent=2)}
"""
    return call_ai_json(prompt)


def gap_and_review_analysis(
    organisation: dict[str, Any],
    project: dict[str, Any],
    opportunity: dict[str, Any],
    proposal: dict[str, Any] | None,
) -> dict[str, Any]:
    proposal_content = proposal.get("content", "") if proposal else ""
    prompt = f"""
Perform an evaluator-style gap analysis and readiness review.

Return exactly this JSON structure:
{{
  "overall_score": 0,
  "readiness_status": "Ready|Needs work|Not ready|Insufficient information",
  "area_scores": {{
    "Eligibility": 0,
    "Excellence": 0,
    "Impact": 0,
    "Implementation": 0,
    "Innovation": 0,
    "Budget": 0,
    "Consortium": 0,
    "Risks": 0,
    "Sustainability": 0,
    "Dissemination": 0
  }},
  "confirmed_strengths": [],
  "missing_information": [],
  "gaps": [
    {{
      "area": "",
      "severity": "Critical|Major|Minor",
      "finding": "",
      "recommended_action": ""
    }}
  ],
  "unsupported_claims": [],
  "tasks": [
    {{
      "title": "",
      "description": "",
      "priority": "High|Medium|Low"
    }}
  ],
  "executive_comment": ""
}}

Rules:
- Scores must be integers from 0 to 100.
- Missing official call information must not be treated as a failed requirement.
- Distinguish project gaps from unavailable call data.
- Produce a practical action plan.
- Evaluate the proposal draft when supplied; otherwise assess project readiness.

ORGANISATION:
{json.dumps(organisation, ensure_ascii=False, indent=2)}

PROJECT:
{json.dumps(project, ensure_ascii=False, indent=2)}

OPPORTUNITY:
{json.dumps(opportunity, ensure_ascii=False, indent=2)}

LATEST PROPOSAL DRAFT:
{proposal_content or "[No saved proposal version]"}
"""
    return call_ai_json(prompt)


def copilot_answer(
    organisation: dict[str, Any],
    project: dict[str, Any],
    opportunity: dict[str, Any],
    match: dict[str, Any] | None,
    review: dict[str, Any] | None,
    proposal: dict[str, Any] | None,
    history: list[dict[str, Any]],
    question: str,
) -> str:
    compact_history = [
        {"role": item.get("role"), "content": item.get("content")}
        for item in history[-10:]
    ]
    prompt = f"""
Answer the user's grant question using only the context below.
Write in Romanian unless the user asks for another language.
Be practical and precise.
Never invent official call rules. When information is unavailable, say what must
be checked in the official call documentation.

ORGANISATION:
{json.dumps(organisation, ensure_ascii=False, indent=2)}

PROJECT:
{json.dumps(project, ensure_ascii=False, indent=2)}

OPPORTUNITY:
{json.dumps(opportunity, ensure_ascii=False, indent=2)}

SMART MATCH:
{json.dumps((match or {}).get("analysis", {}), ensure_ascii=False, indent=2)}

LATEST REVIEW:
{json.dumps((review or {}).get("result", {}), ensure_ascii=False, indent=2)}

LATEST PROPOSAL:
{(proposal or {}).get("content", "[No saved proposal version]")}

RECENT CHAT:
{json.dumps(compact_history, ensure_ascii=False, indent=2)}

USER QUESTION:
{question}
"""
    return call_ai_text(prompt)


# ---------- UI helpers ----------

def clamp_score(value: Any) -> int:
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return 0


def show_string_list(title: str, values: list[Any]) -> None:
    with st.expander(title):
        if values:
            for value in values:
                st.write(f"- {value}")
        else:
            st.write("Nimic înregistrat.")


def show_match(result: dict[str, Any]) -> None:
    c1, c2, c3, c4 = st.columns(4)
    match_score = clamp_score(result.get("match_score"))
    eligibility = clamp_score(result.get("eligibility_score"))
    c1.metric("Compatibilitate", f"{match_score}/100")
    c2.metric("Eligibilitate estimată", f"{eligibility}/100")
    c3.metric("Recomandare", result.get("recommendation", "—"))
    c4.metric("Încredere", result.get("confidence", "—"))
    st.progress(match_score)
    st.write(result.get("reasoning", ""))
    show_string_list("Puncte forte", result.get("strengths", []))
    show_string_list("Puncte slabe", result.get("weaknesses", []))
    show_string_list("Eligibilitate confirmată", result.get("eligibility_confirmed", []))
    show_string_list("Eligibilitate necunoscută", result.get("eligibility_unknown", []))
    show_string_list("Întrebări critice", result.get("critical_questions", []))


def show_review(result: dict[str, Any]) -> None:
    score = clamp_score(result.get("overall_score"))
    c1, c2 = st.columns([1, 3])
    c1.metric("Grant Readiness", f"{score}/100")
    c2.metric("Stare", result.get("readiness_status", "—"))
    st.progress(score)

    area_scores = result.get("area_scores", {})
    rows = [
        {"Domeniu": area, "Scor": clamp_score(area_scores.get(area, 0))}
        for area in REVIEW_AREAS
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    st.info(result.get("executive_comment", ""))
    show_string_list("Puncte forte confirmate", result.get("confirmed_strengths", []))
    show_string_list("Informații lipsă", result.get("missing_information", []))
    show_string_list("Afirmații fără suport", result.get("unsupported_claims", []))

    with st.expander("Gap Analysis", expanded=True):
        gaps = result.get("gaps", [])
        if not gaps:
            st.write("Nu au fost raportate lacune.")
        for gap in gaps:
            st.markdown(
                f'**{gap.get("severity", "—")} · {gap.get("area", "General")}**'
            )
            st.write(gap.get("finding", ""))
            st.caption(f'Acțiune: {gap.get("recommended_action", "—")}')


# ---------- application ----------

require_login()

organisation = load_organisation()
projects = load_projects()
opportunities = load_opportunities()

st.title("🧭 Etapa 11 — AI Grant Copilot")
st.caption(
    "Smart Match, verificare de pregătire, Gap Analysis, plan de acțiune și chat contextual"
)

if not projects:
    st.error("Nu există proiecte.")
    st.stop()

if not opportunities:
    st.error(
        "Nu există oportunități salvate. Folosește Opportunity Engine pentru a salva apeluri."
    )
    st.stop()

project_map = {
    f'{project.get("name", "Proiect")} — {project["id"][:8]}': project
    for project in projects
}
project_label = st.selectbox("Proiect", list(project_map))
project = project_map[project_label]
project_id = project["id"]

opportunities = sorted(
    opportunities,
    key=lambda item: float(item.get("score", 0) or 0),
    reverse=True,
)

opportunity_index = st.selectbox(
    "Oportunitate",
    range(len(opportunities)),
    format_func=lambda index: (
        f'{opportunities[index].get("score", 0)}% · '
        f'{opportunities[index].get("reference") or "fără cod"} — '
        f'{opportunities[index].get("title") or "fără titlu"}'
    ),
)
opportunity = opportunities[opportunity_index]
identity = opportunity_identity(opportunity)

with st.expander("Datele apelului selectat"):
    st.json(opportunity)

match_tab, review_tab, tasks_tab, chat_tab, history_tab = st.tabs([
    "Smart Match",
    "Gap & Reviewer",
    "Plan de acțiune",
    "AI Chat",
    "Istoric",
])

with match_tab:
    st.subheader("Smart Match")
    st.write(
        "Analiza separă potrivirea tematică de eligibilitatea care poate fi confirmată."
    )

    if st.button("Analizează compatibilitatea", type="primary", use_container_width=True):
        with st.spinner("AI compară proiectul cu apelul..."):
            try:
                result = smart_match_analysis(organisation, project, opportunity)
                save_match(project_id, identity, result)
                st.session_state["stage11_match"] = result
                st.success("Analiza Smart Match a fost salvată.")
            except Exception as exc:
                st.error(str(exc))

    stored_match = load_match(project_id, identity)
    result = st.session_state.get("stage11_match") or (
        stored_match.get("analysis") if stored_match else None
    )
    if result:
        show_match(result)
    else:
        st.info("Nu există încă o analiză Smart Match pentru această combinație.")

with review_tab:
    st.subheader("Gap Analysis și evaluator AI")
    latest_proposal = load_latest_proposal(project_id, identity)
    if latest_proposal:
        st.success(
            f'Va fi evaluată ultima versiune salvată: '
            f'{latest_proposal.get("document_type")} v{latest_proposal.get("version")}.'
        )
    else:
        st.warning(
            "Nu există o versiune de propunere salvată pentru acest apel. "
            "Analiza va evalua pregătirea proiectului."
        )

    if st.button("Rulează analiza completă", type="primary", use_container_width=True):
        with st.spinner("AI evaluează proiectul și pregătește planul de acțiune..."):
            try:
                result = gap_and_review_analysis(
                    organisation,
                    project,
                    opportunity,
                    latest_proposal,
                )
                review_id = save_review(
                    project_id,
                    identity,
                    "full_readiness_review",
                    result,
                    latest_proposal.get("id") if latest_proposal else None,
                )
                created = save_generated_tasks(
                    project_id,
                    identity,
                    review_id,
                    result.get("tasks", []),
                )
                st.session_state["stage11_review"] = result
                st.success(f"Analiza a fost salvată. Au fost create {created} sarcini.")
            except Exception as exc:
                st.error(str(exc))

    reviews = load_reviews(project_id, identity)
    result = st.session_state.get("stage11_review") or (
        reviews[0].get("result") if reviews else None
    )
    if result:
        show_review(result)
    else:
        st.info("Nu există încă o analiză completă pentru acest apel.")

with tasks_tab:
    st.subheader("Plan de acțiune")
    tasks = load_tasks(project_id, identity)

    if not tasks:
        st.info("Rulează Gap Analysis pentru a genera automat sarcinile.")
    else:
        priority_order = {"High": 0, "Medium": 1, "Low": 2}
        tasks.sort(key=lambda item: (
            item.get("status") == "Done",
            priority_order.get(item.get("priority", "Medium"), 1),
        ))

        for task in tasks:
            with st.container(border=True):
                c1, c2, c3 = st.columns([5, 1, 1])
                c1.markdown(f'**{task.get("title", "Sarcină")}**')
                c1.write(task.get("description", ""))
                c2.write(f'**{task.get("priority", "Medium")}**')
                status = c3.selectbox(
                    "Status",
                    ["Open", "In progress", "Done"],
                    index=["Open", "In progress", "Done"].index(
                        task.get("status", "Open")
                        if task.get("status", "Open") in ["Open", "In progress", "Done"]
                        else "Open"
                    ),
                    key=f'task_status_{task["id"]}',
                    label_visibility="collapsed",
                )
                if status != task.get("status"):
                    update_task(task["id"], status)
                    st.rerun()

with chat_tab:
    st.subheader("AI Chat pentru proiect și apel")
    match = load_match(project_id, identity)
    reviews = load_reviews(project_id, identity)
    latest_review = reviews[0] if reviews else None
    latest_proposal = load_latest_proposal(project_id, identity)
    messages = load_chat(project_id, identity)

    for message in messages:
        role = message.get("role", "assistant")
        with st.chat_message(role):
            st.markdown(message.get("content", ""))

    question = st.chat_input("Ex.: Cum cresc scorul la Impact?")
    if question:
        save_chat_message(project_id, identity, "user", question)
        with st.chat_message("user"):
            st.markdown(question)

        with st.chat_message("assistant"):
            with st.spinner("Copilotul analizează contextul..."):
                try:
                    answer = copilot_answer(
                        organisation,
                        project,
                        opportunity,
                        match,
                        latest_review,
                        latest_proposal,
                        messages,
                        question,
                    )
                    st.markdown(answer)
                    save_chat_message(project_id, identity, "assistant", answer)
                except Exception as exc:
                    st.error(str(exc))

with history_tab:
    st.subheader("Istoric analize")
    match = load_match(project_id, identity)
    reviews = load_reviews(project_id, identity)

    if match:
        with st.expander(
            f'Smart Match · {match.get("match_score", 0)}/100 · '
            f'{match.get("updated_at", "")}',
            expanded=False,
        ):
            st.json(match.get("analysis", {}))

    if not reviews:
        st.info("Nu există review-uri salvate.")
    for review in reviews:
        with st.expander(
            f'{review.get("review_type")} · '
            f'{review.get("overall_score", 0)}/100 · '
            f'{review.get("created_at", "")}'
        ):
            st.json(review.get("result", {}))

st.divider()
st.caption(
    "Scorurile sunt estimări de lucru, nu confirmări de eligibilitate sau șanse de finanțare. "
    "Documentația oficială a apelului rămâne sursa decisivă."
)
