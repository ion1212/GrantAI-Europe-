
import json
import os
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import streamlit as st
from openai import OpenAI
from supabase import create_client

st.set_page_config(
    page_title="Etapa 13 — AI Proposal Reviewer",
    page_icon="🧪",
    layout="wide",
)

CORE_SECTIONS = ["Excellence", "Impact", "Implementation"]


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


def load_writer_sections(sb, uid: str, project_id: str, identity: str):
    """
    Încarcă documentul Etapei 12 pentru oportunitatea curentă.
    Dacă opportunity_identity diferă între pagini, folosește ca fallback
    cel mai recent document Writer pentru același utilizator și proiect.
    """
    try:
        docs = rows(
            sb.table("grant_writer_documents")
            .select("*")
            .eq("user_id", uid)
            .eq("project_id", project_id)
            .eq("opportunity_identity", identity)
            .order("updated_at", desc=True)
            .limit(1)
            .execute()
        )
    except Exception:
        docs = []

    if not docs:
        try:
            docs = rows(
                sb.table("grant_writer_documents")
                .select("*")
                .eq("user_id", uid)
                .eq("project_id", project_id)
                .order("updated_at", desc=True)
                .limit(1)
                .execute()
            )
        except Exception:
            docs = []

    if not docs:
        return None, []

    doc = docs[0]

    try:
        sections = rows(
            sb.table("grant_writer_sections")
            .select("*")
            .eq("document_id", doc["id"])
            .order("section_key")
            .execute()
        )
    except Exception:
        sections = []

    return doc, sections


def normalize_section_title(section: dict[str, Any]) -> str:
    return (
        section.get("section_title")
        or section.get("section_key")
        or "Section"
    )


def combine_sections(sections: list[dict[str, Any]]) -> str:
    parts = []
    for section in sections:
        content = (section.get("content") or "").strip()
        if not content:
            continue
        title = normalize_section_title(section)
        parts.append(f"# {title}\n\n{content}")
    return "\n\n---\n\n".join(parts)


def compact_json(obj: Any, limit: int = 12000) -> str:
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


def evaluate_proposal(
    project: dict[str, Any],
    opportunity: dict[str, Any],
    proposal_text: str,
) -> dict[str, Any]:
    prompt = f"""
You are acting as a rigorous EU grant proposal evaluator.

Evaluate the proposal against the supplied funding opportunity and project context.
Do not invent official criteria that are not present in the supplied opportunity.
If official call criteria are missing, explicitly state that limitation.

Return ONLY valid JSON with this exact structure:

{{
  "overall_score": 0,
  "excellence_score": 0,
  "impact_score": 0,
  "implementation_score": 0,
  "verdict": "Strong|Competitive but needs work|Needs major revision|Not ready|Insufficient information",
  "confidence": "High|Medium|Low",
  "strengths": [],
  "weaknesses": [],
  "critical_issues": [],
  "recommendations": [],
  "section_reviews": [
    {{
      "section_key": "Excellence",
      "score": 0,
      "strengths": [],
      "weaknesses": [],
      "recommendations": []
    }},
    {{
      "section_key": "Impact",
      "score": 0,
      "strengths": [],
      "weaknesses": [],
      "recommendations": []
    }},
    {{
      "section_key": "Implementation",
      "score": 0,
      "strengths": [],
      "weaknesses": [],
      "recommendations": []
    }}
  ],
  "evaluator_summary": ""
}}

Scoring:
- all scores are integers 0-100.
- penalize unsupported claims, vague KPIs, unclear methodology, weak implementation logic,
  missing budget rationale, missing consortium logic, missing risk mitigation, and missing evidence.
- do NOT penalize the applicant for official call data that is not supplied; mark it as unknown.
- distinguish proposal quality from eligibility.

PROJECT:
{compact_json(project)}

FUNDING OPPORTUNITY:
{compact_json(opportunity)}

PROPOSAL:
{proposal_text[:30000]}
"""
    response = ai_client().responses.create(
        model=model_name(),
        instructions=(
            "You are a senior EU proposal evaluator. "
            "Return valid JSON only and do not invent official requirements."
        ),
        input=prompt,
    )
    try:
        return json.loads(clean_json(response.output_text))
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Evaluatorul AI nu a returnat JSON valid: {exc}"
        ) from exc


def clamp(value: Any) -> int:
    try:
        return max(0, min(100, int(round(float(value)))))
    except Exception:
        return 0


def save_evaluation(
    sb,
    uid: str,
    project_id: str,
    identity: str,
    result: dict[str, Any],
):
    payload = {
        "user_id": uid,
        "project_id": project_id,
        "opportunity_identity": identity,
        "overall_score": clamp(result.get("overall_score")),
        "excellence_score": clamp(result.get("excellence_score")),
        "impact_score": clamp(result.get("impact_score")),
        "implementation_score": clamp(result.get("implementation_score")),
        "verdict": str(result.get("verdict", "Needs review")),
        "strengths": result.get("strengths", []),
        "weaknesses": result.get("weaknesses", []),
        "critical_issues": result.get("critical_issues", []),
        "recommendations": result.get("recommendations", []),
        "evaluator_result": result,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    inserted = sb.table("grant_evaluations").insert(payload).execute()
    evaluation_id = rows(inserted)[0]["id"]

    section_rows = []
    for section in result.get("section_reviews", []):
        section_rows.append(
            {
                "user_id": uid,
                "project_id": project_id,
                "evaluation_id": evaluation_id,
                "section_key": str(section.get("section_key", "Section")),
                "score": clamp(section.get("score")),
                "strengths": section.get("strengths", []),
                "weaknesses": section.get("weaknesses", []),
                "recommendations": section.get("recommendations", []),
            }
        )
    if section_rows:
        sb.table("grant_evaluation_sections").insert(section_rows).execute()
    return evaluation_id


def load_evaluations(sb, uid: str, project_id: str, identity: str):
    try:
        return rows(
            sb.table("grant_evaluations")
            .select("*")
            .eq("user_id", uid)
            .eq("project_id", project_id)
            .eq("opportunity_identity", identity)
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
    except Exception:
        return []


def render_list(title: str, values: list[Any], expanded: bool = False):
    with st.expander(title, expanded=expanded):
        if not values:
            st.write("Nimic raportat.")
        for value in values:
            st.write(f"- {value}")


def render_result(result: dict[str, Any]):
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Scor general", f"{clamp(result.get('overall_score'))}/100")
    c2.metric("Excellence", f"{clamp(result.get('excellence_score'))}/100")
    c3.metric("Impact", f"{clamp(result.get('impact_score'))}/100")
    c4.metric("Implementation", f"{clamp(result.get('implementation_score'))}/100")

    overall = clamp(result.get("overall_score"))
    st.progress(overall)
    st.subheader(result.get("verdict", "Needs review"))
    st.caption(f"Încredere evaluator: {result.get('confidence', '—')}")
    st.write(result.get("evaluator_summary", ""))

    render_list("Puncte forte", result.get("strengths", []))
    render_list("Puncte slabe", result.get("weaknesses", []))
    render_list("Probleme critice", result.get("critical_issues", []), expanded=True)
    render_list("Recomandări", result.get("recommendations", []), expanded=True)

    section_reviews = result.get("section_reviews", [])
    if section_reviews:
        st.subheader("Evaluare pe secțiuni")
        table = pd.DataFrame(
            [
                {
                    "Secțiune": s.get("section_key", "Section"),
                    "Scor": clamp(s.get("score")),
                }
                for s in section_reviews
            ]
        )
        st.dataframe(table, hide_index=True, use_container_width=True)

        for section in section_reviews:
            with st.expander(
                f"{section.get('section_key', 'Section')} · {clamp(section.get('score'))}/100"
            ):
                render_list("Puncte forte", section.get("strengths", []))
                render_list("Puncte slabe", section.get("weaknesses", []))
                render_list(
                    "Recomandări",
                    section.get("recommendations", []),
                    expanded=True,
                )


st.title("🧪 Etapa 13 — AI Proposal Reviewer")
st.caption(
    "Evaluator AI pentru Excellence, Impact și Implementation, cu scoruri și feedback salvate în Supabase"
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

source_table, opportunities = load_opportunities(sb, uid, project_id)
if not opportunities:
    st.warning("Nu există oportunități salvate pentru proiect.")
    st.stop()

opportunity = st.selectbox(
    "Oportunitate",
    opportunities,
    format_func=opportunity_label,
)
identity = opportunity_identity(opportunity)

with st.expander("Datele apelului selectat"):
    st.json(opportunity)

document, sections = load_writer_sections(sb, uid, project_id, identity)
proposal_text = combine_sections(sections)

if document and document.get("opportunity_identity") != identity:
    st.info(
        "Reviewer-ul a folosit cel mai recent document din AI Grant Writer pentru acest proiect, "
        "deoarece identificatorul oportunității diferă între module."
    )

if not proposal_text:
    st.warning(
        "Nu există conținut generat în Etapa 12 pentru această oportunitate. "
        "Generează și salvează cel puțin o secțiune în AI Grant Writer."
    )
else:
    st.success(
        f"Am încărcat {len([s for s in sections if (s.get('content') or '').strip()])} "
        "secțiuni din AI Grant Writer."
    )

review_tab, history_tab = st.tabs(["Evaluator", "Istoric"])

with review_tab:
    if proposal_text:
        st.text_area(
            "Propunerea evaluată",
            value=proposal_text,
            height=420,
            disabled=True,
        )

        if st.button(
            "Evaluează propunerea cu AI",
            type="primary",
            use_container_width=True,
        ):
            with st.spinner("Evaluatorul AI analizează propunerea..."):
                try:
                    result = evaluate_proposal(
                        project,
                        opportunity,
                        proposal_text,
                    )
                    evaluation_id = save_evaluation(
                        sb,
                        uid,
                        project_id,
                        identity,
                        result,
                    )
                    st.session_state["stage13_result"] = result
                    st.success(f"Evaluarea a fost salvată. ID: {evaluation_id[:8]}")
                except Exception as exc:
                    st.error(str(exc))

        result = st.session_state.get("stage13_result")
        if result:
            render_result(result)

with history_tab:
    evaluations = load_evaluations(
        sb,
        uid,
        project_id,
        identity,
    )

    if not evaluations:
        st.info("Nu există încă evaluări salvate pentru această oportunitate.")
    else:
        for evaluation in evaluations:
            title = (
                f"{evaluation.get('overall_score', 0)}/100 · "
                f"{evaluation.get('verdict', 'Needs review')} · "
                f"{evaluation.get('created_at', '')}"
            )
            with st.expander(title):
                result = evaluation.get("evaluator_result") or {}
                if result:
                    render_result(result)
                else:
                    st.json(evaluation)

st.divider()
st.caption(
    "Scorurile AI sunt estimări de lucru și nu reprezintă evaluarea oficială a Comisiei Europene."
)
