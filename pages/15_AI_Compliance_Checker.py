import json
import os
import re
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import streamlit as st
from openai import OpenAI
from supabase import create_client

st.set_page_config(page_title="Etapa 15 — AI Compliance Checker", page_icon="🛡️", layout="wide")


def secret(name: str, default: str = "") -> str:
    try:
        return str(st.secrets.get(name, default))
    except Exception:
        return os.getenv(name, default)


@st.cache_resource
def db():
    return create_client(secret("SUPABASE_URL"), secret("SUPABASE_KEY") or secret("SUPABASE_ANON_KEY"))


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


def project_label(p):
    return f"{p.get('name') or p.get('project_name') or p.get('title') or 'Project'} — {str(p.get('id',''))[:8]}"


def opportunity_identity(x):
    for key in ("opportunity_identity", "identity", "call_id", "identifier", "code", "id"):
        if x.get(key) not in (None, ""):
            return str(x[key])
    return str(x.get("title") or x.get("name") or x.get("topic") or "opportunity")[:240]


def opportunity_label(x):
    title = x.get("title") or x.get("name") or x.get("topic") or "Funding opportunity"
    score = x.get("match_score")
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


def load_writer_sections(sb, uid, project_id, identity):
    try:
        exact = rows(sb.table("grant_writer_sections").select("*")
                     .eq("user_id", uid).eq("project_id", project_id)
                     .eq("opportunity_identity", identity).order("updated_at", desc=True).execute())
        if exact:
            return exact, "exact"
    except Exception:
        pass
    try:
        fallback = rows(sb.table("grant_writer_sections").select("*")
                        .eq("user_id", uid).eq("project_id", project_id)
                        .order("updated_at", desc=True).limit(100).execute())
        if fallback:
            did = fallback[0].get("document_id")
            if did:
                fallback = [x for x in fallback if str(x.get("document_id")) == str(did)]
            return fallback, "project_fallback"
    except Exception:
        pass
    return [], "none"


def load_latest_evaluation(sb, uid, project_id, identity):
    try:
        exact = rows(sb.table("grant_evaluations").select("*")
                     .eq("user_id", uid).eq("project_id", project_id)
                     .eq("opportunity_identity", identity)
                     .order("created_at", desc=True).limit(1).execute())
        if exact:
            return exact[0], "exact"
    except Exception:
        pass
    try:
        fallback = rows(sb.table("grant_evaluations").select("*")
                        .eq("user_id", uid).eq("project_id", project_id)
                        .order("created_at", desc=True).limit(1).execute())
        if fallback:
            return fallback[0], "project_fallback"
    except Exception:
        pass
    return None, "none"


def combine_sections(sections):
    parts = []
    for section in sections:
        content = (section.get("content") or "").strip()
        if content:
            title = section.get("section_title") or section.get("section_key") or "Section"
            parts.append(f"# {title}\n\n{content}")
    return "\n\n---\n\n".join(parts)


def compact(obj, limit=14000):
    return json.dumps(obj, ensure_ascii=False, default=str)[:limit]


def ai_client():
    return OpenAI(api_key=secret("OPENAI_API_KEY"))


def model_name():
    return secret("OPENAI_MODEL", "gpt-4.1-mini")


def clean_json(raw):
    text = raw.strip()
    if text.startswith("```"):
        text = text.replace("```json", "", 1).replace("```", "").strip()
    return text


def clamp(value):
    try:
        return max(0, min(100, int(round(float(value)))))
    except Exception:
        return 0


def placeholder_scan(text):
    pats = [r"\[TO CONFIRM\]", r"\[TBC\]", r"\[TBD\]", r"\[INSERT[^\]]*\]", r"\[PLACEHOLDER[^\]]*\]"]
    out = []
    for p in pats:
        out.extend(re.findall(p, text, flags=re.I))
    return sorted(set(out))


def run_check(project, opportunity, proposal, evaluation):
    review = (evaluation.get("evaluator_result") or evaluation) if evaluation else {}
    prompt = f"""
You are a rigorous EU grant proposal compliance and submission-readiness checker.
Check ONLY requirements supported by the supplied funding opportunity and supplied proposal/project data.

RULES:
- Never invent eligibility conditions, TRL requirements, consortium rules, budget ceilings,
  mandatory annexes, KPIs, ethics rules, deadlines or other official call requirements.
- If an official requirement is absent, mark it UNKNOWN, not FAIL.
- Distinguish confirmed compliance, confirmed non-compliance, missing applicant facts,
  and unknown official call requirements.
- Treat [TO CONFIRM], [TBC], [TBD], [INSERT ...] and similar placeholders as unresolved.
- Never fabricate partners, budget, TRL, KPIs, results, certifications or evidence.
- Return ONLY valid JSON.

Return exactly:
{{
 "overall_status":"READY|READY WITH WARNINGS|NOT READY|INSUFFICIENT CALL DATA",
 "compliance_score":0,
 "submission_blocked":false,
 "confidence":"High|Medium|Low",
 "summary":"",
 "confirmed_items":[],
 "blocking_issues":[],
 "warnings":[],
 "missing_applicant_facts":[],
 "unknown_call_requirements":[],
 "placeholders_found":[],
 "checks":[{{
   "category":"Eligibility|Call alignment|Excellence|Impact|Implementation|TRL|KPIs|Consortium|Budget|Risks|Evidence|Ethics|Administrative|Other",
   "requirement":"",
   "source":"Funding opportunity|Proposal|Internal completeness",
   "status":"PASS|FAIL|MISSING|UNKNOWN|WARNING",
   "evidence":"",
   "action_required":""
 }}],
 "priority_actions":[]
}}

PROJECT:
{compact(project)}

FUNDING OPPORTUNITY:
{compact(opportunity)}

LATEST REVIEW:
{compact(review)}

PROPOSAL:
{proposal[:35000]}
"""
    response = ai_client().responses.create(
        model=model_name(),
        instructions="Return valid JSON only. Never invent official requirements or applicant facts.",
        input=prompt,
    )
    result = json.loads(clean_json(response.output_text))
    result["compliance_score"] = clamp(result.get("compliance_score"))
    local = placeholder_scan(proposal)
    result["placeholders_found"] = sorted(set([str(x) for x in (result.get("placeholders_found") or [])] + local))
    if local:
        result["submission_blocked"] = True
        if result.get("overall_status") == "READY":
            result["overall_status"] = "NOT READY"
    return result


def save_check(sb, uid, project_id, identity, result):
    now = datetime.now(timezone.utc).isoformat()
    # Folosește coloanele de bază; rezultatul complet este păstrat JSON.
    candidates = [
        {
            "user_id": uid, "project_id": project_id, "opportunity_identity": identity,
            "status": str(result.get("overall_status", "NOT READY")),
            "score": clamp(result.get("compliance_score")),
            "check_result": result, "created_at": now, "updated_at": now,
        },
        {
            "user_id": uid, "project_id": project_id, "opportunity_identity": identity,
            "compliance_result": result, "created_at": now, "updated_at": now,
        },
        {
            "user_id": uid, "project_id": project_id, "opportunity_identity": identity,
            "result": result, "created_at": now, "updated_at": now,
        },
    ]
    last = None
    for payload in candidates:
        try:
            r = sb.table("grant_compliance_checks").insert(payload).execute()
            data = rows(r)
            return data[0] if data else payload
        except Exception as exc:
            last = exc
    raise RuntimeError(f"Nu am putut salva în grant_compliance_checks: {last}")


def load_checks(sb, uid, project_id, identity):
    try:
        return rows(sb.table("grant_compliance_checks").select("*")
                    .eq("user_id", uid).eq("project_id", project_id)
                    .eq("opportunity_identity", identity)
                    .order("created_at", desc=True).limit(50).execute())
    except Exception:
        return []


def get_saved_result(check):
    for key in ("check_result", "compliance_result", "result"):
        if isinstance(check.get(key), dict):
            return check[key]
    return {}


def render_list(title, values, expanded=False):
    with st.expander(title, expanded=expanded):
        if not values:
            st.write("Nimic raportat.")
        for value in values or []:
            st.write(f"- {value}")


def render_result(result):
    score = clamp(result.get("compliance_score"))
    blocked = bool(result.get("submission_blocked"))
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Compliance score", f"{score}/100")
    c2.metric("Status", result.get("overall_status", "—"))
    c3.metric("Depunere", "BLOCATĂ" if blocked else "Neblocată")
    c4.metric("Încredere", result.get("confidence", "—"))
    st.progress(score)

    if blocked:
        st.error("Există probleme care trebuie rezolvate înainte de depunere.")
    elif result.get("overall_status") == "INSUFFICIENT CALL DATA":
        st.warning("Datele oficiale ale apelului sunt insuficiente pentru confirmarea completă.")
    else:
        st.success("Nu au fost identificate blocaje pe baza informațiilor disponibile.")

    st.write(result.get("summary", ""))
    render_list("Probleme blocante", result.get("blocking_issues", []), True)
    render_list("Acțiuni prioritare", result.get("priority_actions", []), True)
    render_list("Date lipsă de la aplicant", result.get("missing_applicant_facts", []))
    render_list("Cerințe oficiale necunoscute", result.get("unknown_call_requirements", []))
    render_list("Avertismente", result.get("warnings", []))
    render_list("Elemente confirmate", result.get("confirmed_items", []))

    placeholders = result.get("placeholders_found") or []
    if placeholders:
        st.subheader("Placeholders nerezolvate")
        for x in placeholders:
            st.code(str(x))

    checks = result.get("checks") or []
    if checks:
        st.subheader("Matrice de conformitate")
        df = pd.DataFrame([{
            "Categorie": x.get("category",""), "Cerință": x.get("requirement",""),
            "Sursă": x.get("source",""), "Status": x.get("status",""),
            "Dovadă": x.get("evidence",""), "Acțiune": x.get("action_required","")
        } for x in checks])
        st.dataframe(df, hide_index=True, use_container_width=True)


st.title("🛡️ Etapa 15 — AI Compliance Checker")
st.caption("Verifică pregătirea pentru depunere fără să inventeze criterii oficiale sau date lipsă.")

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

opportunity = st.selectbox("Oportunitate", opportunities, format_func=opportunity_label)
identity = opportunity_identity(opportunity)

with st.expander("Datele apelului selectat"):
    st.json(opportunity)

sections, writer_mode = load_writer_sections(sb, uid, project_id, identity)
proposal = combine_sections(sections)
evaluation, eval_mode = load_latest_evaluation(sb, uid, project_id, identity)

if writer_mode == "project_fallback" or eval_mode == "project_fallback":
    st.info("Identificatorul oportunității diferă între module; s-au folosit cele mai recente date ale proiectului.")

if not proposal:
    st.warning("Nu există conținut în AI Grant Writer pentru verificare.")
    st.stop()

st.success(f"Am încărcat {len([s for s in sections if (s.get('content') or '').strip()])} secțiuni din AI Grant Writer.")

if evaluation:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Ultimul scor", f"{evaluation.get('overall_score',0)}/100")
    c2.metric("Excellence", f"{evaluation.get('excellence_score',0)}/100")
    c3.metric("Impact", f"{evaluation.get('impact_score',0)}/100")
    c4.metric("Implementation", f"{evaluation.get('implementation_score',0)}/100")
else:
    st.warning("Nu există evaluare Etapa 13. Checker-ul poate rula, dar recomand re-evaluarea după Etapa 14.")

tab1, tab2 = st.tabs(["Compliance Checker", "Istoric"])

with tab1:
    st.text_area("Propunerea verificată", value=proposal, height=360, disabled=True)
    local = placeholder_scan(proposal)
    if local:
        st.warning(f"Au fost detectate {len(local)} tipuri de placeholder nerezolvat.")

    if st.button("Rulează verificarea de conformitate", type="primary", use_container_width=True):
        with st.spinner("AI verifică eligibilitatea, completitudinea și pregătirea pentru depunere..."):
            try:
                result = run_check(project, opportunity, proposal, evaluation)
                saved = save_check(sb, uid, project_id, identity, result)
                st.session_state["stage15_result"] = result
                sid = str(saved.get("id", ""))
                st.success("Verificarea a fost salvată." + (f" ID: {sid[:8]}" if sid else ""))
            except Exception as exc:
                st.error(str(exc))

    if st.session_state.get("stage15_result"):
        render_result(st.session_state["stage15_result"])

with tab2:
    history = load_checks(sb, uid, project_id, identity)
    if not history:
        st.info("Nu există încă verificări salvate.")
    for check in history:
        result = get_saved_result(check)
        title = f"{result.get('compliance_score', check.get('score',0))}/100 · {result.get('overall_status', check.get('status','—'))} · {check.get('created_at','')}"
        with st.expander(title):
            render_result(result) if result else st.json(check)

st.divider()
st.caption("Cerințele oficiale absente sunt marcate UNKNOWN și trebuie validate din documentația apelului înainte de depunere.")
