
import json
import os
from datetime import datetime, timezone
from typing import Any

import streamlit as st
from openai import OpenAI
from supabase import create_client

st.set_page_config(
    page_title="Etapa 14 — AI Proposal Optimizer",
    page_icon="⚙️",
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
    name = project.get("name") or project.get("project_name") or project.get("title") or "Project"
    return f"{name} — {str(project.get('id',''))[:8]}"

def opportunity_identity(item: dict[str, Any]) -> str:
    for key in ("opportunity_identity", "identity", "call_id", "identifier", "code", "id"):
        if item.get(key) not in (None, ""):
            return str(item[key])
    return str(item.get("title") or item.get("name") or item.get("topic") or "opportunity")[:240]

def opportunity_label(item: dict[str, Any]) -> str:
    title = item.get("title") or item.get("name") or item.get("topic") or "Funding opportunity"
    score = item.get("match_score")
    return (f"{score}% · " if score is not None else "") + str(title)

def load_projects(sb, uid):
    try:
        return rows(sb.table("projects").select("*").eq("user_id", uid).execute())
    except Exception:
        return rows(sb.table("projects").select("*").execute())

def load_opportunities(sb, uid, project_id):
    for table in ("selected_opportunities", "opportunities", "funding_opportunities", "grant_matches"):
        try:
            try:
                r = sb.table(table).select("*").eq("user_id", uid).eq("project_id", project_id).execute()
            except Exception:
                r = sb.table(table).select("*").execute()
            data = rows(r)
            if data:
                return table, data
        except Exception:
            continue
    return None, []

def load_latest_evaluation(sb, uid, project_id, identity):
    try:
        exact = rows(
            sb.table("grant_evaluations").select("*")
            .eq("user_id", uid).eq("project_id", project_id)
            .eq("opportunity_identity", identity)
            .order("created_at", desc=True).limit(1).execute()
        )
        if exact:
            return exact[0], "exact"
    except Exception:
        pass
    try:
        fallback = rows(
            sb.table("grant_evaluations").select("*")
            .eq("user_id", uid).eq("project_id", project_id)
            .order("created_at", desc=True).limit(1).execute()
        )
        if fallback:
            return fallback[0], "project_fallback"
    except Exception:
        pass
    return None, "none"

def load_writer_sections(sb, uid, project_id, identity):
    try:
        exact = rows(
            sb.table("grant_writer_sections").select("*")
            .eq("user_id", uid).eq("project_id", project_id)
            .eq("opportunity_identity", identity)
            .order("updated_at", desc=True).execute()
        )
        if exact:
            return exact, "exact"
    except Exception:
        pass
    try:
        fallback = rows(
            sb.table("grant_writer_sections").select("*")
            .eq("user_id", uid).eq("project_id", project_id)
            .order("updated_at", desc=True).limit(100).execute()
        )
        if fallback:
            did = fallback[0].get("document_id")
            if did:
                fallback = [x for x in fallback if str(x.get("document_id")) == str(did)]
            return fallback, "project_fallback"
    except Exception:
        pass
    return [], "none"

def ai_client():
    return OpenAI(api_key=secret("OPENAI_API_KEY"))

def model_name():
    return secret("OPENAI_MODEL", "gpt-4.1-mini")

def clean_json(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = text.replace("```json", "", 1).replace("```", "").strip()
    return text

def compact(obj, limit=12000):
    return json.dumps(obj, ensure_ascii=False, default=str)[:limit]

def optimize_section(project, opportunity, evaluation, section):
    title = section.get("section_title") or section.get("section_key") or "Section"
    content = section.get("content") or ""
    result = evaluation.get("evaluator_result") or {}
    prompt = f"""
You are a senior EU grant proposal optimizer.

Improve the supplied proposal section using the reviewer feedback.

CRITICAL RULES:
- Never invent TRL, budget, consortium members, partners, KPIs, results, certifications, evidence, or official call requirements.
- Where required information is missing, preserve or add [TO CONFIRM].
- Improve clarity, structure, evaluator-readability, measurability, methodology, and logical flow.
- Do not fabricate evidence merely to raise the score.
- Return ONLY valid JSON.

Return:
{{
  "optimized_content": "",
  "rationale": "",
  "missing_facts": [],
  "changes_made": []
}}

PROJECT:
{compact(project)}

OPPORTUNITY:
{compact(opportunity)}

LATEST REVIEW:
{compact(result)}

SECTION:
{title}

ORIGINAL CONTENT:
{content[:20000]}
"""
    response = ai_client().responses.create(
        model=model_name(),
        instructions="Return valid JSON only. Never invent facts.",
        input=prompt,
    )
    return json.loads(clean_json(response.output_text))

def create_optimization(sb, uid, project_id, identity, evaluation_id, target_score, plan):
    payload = {
        "user_id": uid,
        "project_id": project_id,
        "opportunity_identity": identity,
        "source_evaluation_id": evaluation_id,
        "target_score": target_score,
        "overall_plan": plan,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    r = sb.table("grant_optimizations").insert(payload).execute()
    return rows(r)[0]

def save_optimized_section(sb, uid, optimization, project_id, identity, writer_section, result):
    payload = {
        "user_id": uid,
        "optimization_id": optimization["id"],
        "document_id": writer_section.get("document_id"),
        "writer_section_id": writer_section.get("id"),
        "project_id": project_id,
        "opportunity_identity": identity,
        "section_key": writer_section.get("section_key") or "section",
        "original_content": writer_section.get("content") or "",
        "optimized_content": result.get("optimized_content") or "",
        "rationale": result.get("rationale") or "",
        "missing_facts": result.get("missing_facts") or [],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    sb.table("grant_optimization_sections").insert(payload).execute()

def apply_to_writer(sb, uid, opt_section):
    writer_section_id = opt_section.get("writer_section_id")
    if not writer_section_id:
        raise RuntimeError("Secțiunea Writer originală nu a putut fi identificată.")

    current = rows(
        sb.table("grant_writer_sections").select("*")
        .eq("id", writer_section_id).limit(1).execute()
    )
    if not current:
        raise RuntimeError("Secțiunea Writer nu mai există.")
    current = current[0]

    if current.get("content"):
        sb.table("grant_writer_versions").insert({
            "user_id": uid,
            "document_id": current.get("document_id"),
            "section_key": current.get("section_key"),
            "version_no": int(current.get("version_no") or 1),
            "content": current.get("content") or "",
            "change_note": "Etapa 14 — înainte de optimizare",
        }).execute()

    new_version = int(current.get("version_no") or 1) + 1
    sb.table("grant_writer_sections").update({
        "content": opt_section.get("optimized_content") or "",
        "version_no": new_version,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", writer_section_id).execute()

    sb.table("grant_optimization_sections").update({
        "accepted": True,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", opt_section["id"]).execute()

def load_optimization_sections(sb, optimization_id):
    return rows(
        sb.table("grant_optimization_sections").select("*")
        .eq("optimization_id", optimization_id)
        .order("section_key").execute()
    )

st.title("⚙️ Etapa 14 — AI Proposal Optimizer")
st.caption("Transformă feedback-ul Reviewer-ului în versiuni îmbunătățite, fără a inventa date.")

try:
    sb = db()
except Exception as exc:
    st.error(f"Supabase nu este configurat: {exc}")
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
    st.warning("Nu există oportunități salvate.")
    st.stop()

opportunity = st.selectbox("Oportunitate", opportunities, format_func=opportunity_label)
identity = opportunity_identity(opportunity)

evaluation, eval_mode = load_latest_evaluation(sb, uid, project_id, identity)
sections, writer_mode = load_writer_sections(sb, uid, project_id, identity)

if not evaluation:
    st.warning("Nu există evaluare Etapa 13 pentru acest proiect. Rulează mai întâi AI Proposal Reviewer.")
    st.stop()

if not sections:
    st.warning("Nu există secțiuni Etapa 12 pentru acest proiect.")
    st.stop()

if eval_mode != "exact" or writer_mode != "exact":
    st.info("S-a folosit cel mai recent document/evaluare al proiectului deoarece identificatorul oportunității diferă între module.")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Scor general", f"{evaluation.get('overall_score', 0)}/100")
c2.metric("Excellence", f"{evaluation.get('excellence_score', 0)}/100")
c3.metric("Impact", f"{evaluation.get('impact_score', 0)}/100")
c4.metric("Implementation", f"{evaluation.get('implementation_score', 0)}/100")

target_score = st.slider("Scor țintă", min_value=50, max_value=95, value=80, step=5)

with st.expander("Feedback Reviewer"):
    st.json(evaluation.get("evaluator_result") or evaluation)

if "stage14_optimization" not in st.session_state:
    st.session_state["stage14_optimization"] = None

if st.button("Generează planul de optimizare", type="primary", use_container_width=True):
    plan = {
        "source_score": evaluation.get("overall_score"),
        "target_score": target_score,
        "review_verdict": evaluation.get("verdict"),
        "critical_issues": evaluation.get("critical_issues") or [],
        "recommendations": evaluation.get("recommendations") or [],
    }
    try:
        optimization = create_optimization(
            sb, uid, project_id, identity, evaluation.get("id"), target_score, plan
        )
        with st.spinner("AI optimizează secțiunile existente..."):
            for section in sections:
                if not (section.get("content") or "").strip():
                    continue
                result = optimize_section(project, opportunity, evaluation, section)
                save_optimized_section(
                    sb, uid, optimization, project_id, identity, section, result
                )
        st.session_state["stage14_optimization"] = optimization
        st.success("Planul de optimizare a fost creat.")
        st.rerun()
    except Exception as exc:
        st.error(f"Optimizarea a eșuat: {exc}")

optimization = st.session_state.get("stage14_optimization")

if optimization:
    opt_sections = load_optimization_sections(sb, optimization["id"])
    st.subheader("Propuneri de îmbunătățire")

    for item in opt_sections:
        with st.expander(
            f"{item.get('section_key', 'Section')} · "
            f"{'Aplicată' if item.get('accepted') else 'În așteptare'}",
            expanded=not item.get("accepted"),
        ):
            col1, col2 = st.columns(2)

            with col1:
                st.markdown("**Original**")
                st.text_area(
                    "Original",
                    value=item.get("original_content") or "",
                    height=360,
                    disabled=True,
                    key=f"orig_{item['id']}",
                    label_visibility="collapsed",
                )

            with col2:
                st.markdown("**Optimizat**")
                edited = st.text_area(
                    "Optimizat",
                    value=item.get("optimized_content") or "",
                    height=360,
                    key=f"opt_{item['id']}",
                    label_visibility="collapsed",
                )

            st.markdown("**De ce a fost modificat**")
            st.write(item.get("rationale") or "—")

            missing = item.get("missing_facts") or []
            if missing:
                st.warning("Date care trebuie confirmate înainte de depunere:")
                for value in missing:
                    st.write(f"- {value}")

            if not item.get("accepted"):
                if st.button(
                    "Acceptă și trimite în AI Grant Writer",
                    key=f"accept_{item['id']}",
                    use_container_width=True,
                ):
                    try:
                        if edited != item.get("optimized_content"):
                            sb.table("grant_optimization_sections").update({
                                "optimized_content": edited,
                                "updated_at": datetime.now(timezone.utc).isoformat(),
                            }).eq("id", item["id"]).execute()
                            item["optimized_content"] = edited

                        apply_to_writer(sb, uid, item)
                        st.success("Versiunea optimizată a fost trimisă în AI Grant Writer.")
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))

    if opt_sections and all(x.get("accepted") for x in opt_sections):
        st.success(
            "Toate modificările au fost aplicate. "
            "Revino în Etapa 13 și rulează din nou evaluarea pentru noul scor."
        )

st.divider()
st.caption(
    "Optimizer-ul nu inventează informații lipsă. Datele marcate [TO CONFIRM] trebuie validate înainte de depunere."
)
