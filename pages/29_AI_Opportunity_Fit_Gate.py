# pages/29_AI_Opportunity_Fit_Gate.py
# Etapa 29 — AI Opportunity Fit & Critical Gap Gate
#
# Streamlit + Supabase + OpenAI
# Scop:
# - citește proiectul/oportunitatea curentă;
# - reconstruiește snapshot-ul propunerii din sursele existente;
# - citește cel mai recent Re-Review (Etapa 28);
# - evaluează FIT-ul fără a inventa TRL, buget, parteneri, KPI sau eligibilitate;
# - clasifică gap-urile: AI_DRAFTABLE / USER_EVIDENCE / OFFICIAL_VERIFICATION / OPPORTUNITY_MISMATCH;
# - persistă run + items pentru Etapa 30.
#
# Necesită secrets:
# SUPABASE_URL, SUPABASE_KEY (sau SUPABASE_ANON_KEY), OPENAI_API_KEY
#
# Opțional:
# OPENAI_MODEL (default gpt-4.1-mini)

import json
import os
import re
from datetime import datetime, timezone

import streamlit as st
from supabase import create_client
from openai import OpenAI

st.set_page_config(page_title="Etapa 29 — Opportunity Fit Gate", page_icon="🧭", layout="wide")
st.title("🧭 Etapa 29 — AI Opportunity Fit & Critical Gap Gate")
st.caption(
    "Verifică dacă proiectul se potrivește oportunității înainte de alte corecții. "
    "Nu inventează TRL, buget, parteneri, KPI-uri sau dovezi de eligibilitate."
)

# ---------- Clients ----------
def secret(name, fallback=None):
    try:
        return st.secrets.get(name, fallback)
    except Exception:
        return os.getenv(name, fallback)

SUPABASE_URL = secret("SUPABASE_URL")
SUPABASE_KEY = secret("SUPABASE_KEY") or secret("SUPABASE_ANON_KEY")
OPENAI_API_KEY = secret("OPENAI_API_KEY")
OPENAI_MODEL = secret("OPENAI_MODEL", "gpt-4.1-mini")

if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Lipsesc SUPABASE_URL / SUPABASE_KEY din secrets.")
    st.stop()

sb = create_client(SUPABASE_URL, SUPABASE_KEY)
oa = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# ---------- Auth ----------
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


def current_user_id(sb):
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


restore_auth_session(sb)
USER_ID = current_user_id(sb)

if not USER_ID:
    st.error("Nu am putut identifica utilizatorul autentificat. Revino în pagina principală și apoi redeschide Etapa 29.")
    st.stop()

# ---------- Helpers ----------
def rows(table, filters=None, order=None, limit=100):
    q = sb.table(table).select("*")
    for k, v in (filters or {}).items():
        if v is not None:
            q = q.eq(k, v)
    if order:
        q = q.order(order, desc=True)
    if limit:
        q = q.limit(limit)
    try:
        return q.execute().data or []
    except Exception:
        return []

def norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip()

def safe_json(x):
    if isinstance(x, (dict, list)):
        return x
    if not x:
        return {}
    try:
        return json.loads(x)
    except Exception:
        return {}

def score(v):
    try:
        return float(v) if v is not None else None
    except Exception:
        return None

def get_projects():
    # proiectele pot exista sub nume diferite; încercăm sursele uzuale
    candidates = []
    for t in ("projects", "grant_projects"):
        rr = rows(t, {"user_id": USER_ID}, "created_at", 100)
        for r in rr:
            r["_source_table"] = t
            candidates.append(r)
    # fallback: proiecte distincte din proposal_versions
    if not candidates:
        pv = rows("proposal_versions", {"user_id": USER_ID}, "created_at", 300)
        seen = set()
        for r in pv:
            pid = str(r.get("project_id") or "")
            if pid and pid not in seen:
                seen.add(pid)
                candidates.append({"id": pid, "name": f"Project {pid[:8]}", "_source_table": "proposal_versions"})
    return candidates

def project_label(r):
    name = r.get("name") or r.get("title") or r.get("project_name") or "Project"
    return f"{name} — {str(r.get('id',''))[:8]}"

projects = get_projects()
if not projects:
    st.error("Nu am găsit niciun proiect pentru utilizatorul curent.")
    st.stop()

selected = st.selectbox("Project", projects, format_func=project_label)
PROJECT_ID = str(selected.get("id"))

# ---------- Determine opportunity ----------
opportunity = (
    selected.get("opportunity_identity")
    or selected.get("opportunity_id")
    or ""
)

# Prefer latest stage records carrying opportunity_identity
for table in (
    "rereview_orchestration_runs",
    "post_execution_validation_runs",
    "controlled_resolution_runs",
    "submission_readiness_runs",
):
    rr = rows(table, {"user_id": USER_ID, "project_id": PROJECT_ID}, "created_at", 1)
    if rr and rr[0].get("opportunity_identity"):
        opportunity = rr[0]["opportunity_identity"]
        break

st.text_input("Oportunitate", value=str(opportunity or ""), disabled=True)
OPPORTUNITY = str(opportunity or "")

# ---------- Snapshot ----------
# Keep the most complete text per logical section.
snapshot = {}
snapshot_sources = {}

def put_section(section, content, source):
    sec = norm(section) or "General"
    txt = (content or "").strip()
    if not txt:
        return
    if len(txt) > len(snapshot.get(sec, "")):
        snapshot[sec] = txt
        snapshot_sources[sec] = source

for r in rows("grant_writer_sections", {"user_id": USER_ID, "project_id": PROJECT_ID}, "updated_at", 300):
    put_section(r.get("section_key") or r.get("section_title"), r.get("content"), "grant_writer_sections")

for r in rows("grant_writer_versions", {"user_id": USER_ID, "project_id": PROJECT_ID}, "created_at", 300):
    put_section(r.get("section_key"), r.get("content"), "grant_writer_versions")

for r in rows("grant_optimization_sections", {"user_id": USER_ID, "project_id": PROJECT_ID}, "updated_at", 300):
    put_section(r.get("section_key"), r.get("optimized_content") or r.get("original_content"), "grant_optimization_sections")

for r in rows("proposal_versions", {"user_id": USER_ID, "project_id": PROJECT_ID}, "updated_at", 300):
    put_section(r.get("section") or r.get("title"), r.get("content"), "proposal_versions")

for r in rows("submission_packs", {"user_id": USER_ID, "project_id": PROJECT_ID}, "created_at", 20):
    put_section("Excellence", r.get("excellence_content"), "submission_packs")
    put_section("Impact", r.get("impact_content"), "submission_packs")
    put_section("Implementation", r.get("implementation_content"), "submission_packs")

# Overlay validated/applied content from Etapa 27
validated = rows(
    "post_execution_validation_items",
    {"user_id": USER_ID, "project_id": PROJECT_ID},
    "created_at",
    200,
)
validated_ok = [
    r for r in validated
    if r.get("content_matches_approval") is True
    and r.get("target_section_valid") is True
    and (r.get("applied_content") or "").strip()
]
for r in reversed(validated_ok):
    sec = norm(r.get("target_section")) or "Implementation"
    snapshot[sec] = r.get("applied_content", "")
    snapshot_sources[sec] = "Etapa 27 validated overlay"

# ---------- Etapa 28 ----------
runs28 = rows(
    "rereview_orchestration_runs",
    {"user_id": USER_ID, "project_id": PROJECT_ID},
    "created_at",
    1,
)
run28 = runs28[0] if runs28 else {}
items28 = []
if run28:
    items28 = rows(
        "rereview_orchestration_items",
        {"user_id": USER_ID, "project_id": PROJECT_ID, "orchestration_run_id": run28.get("id")},
        "created_at",
        100,
    )

c1, c2, c3 = st.columns(3)
c1.metric("Snapshot", f"{len(snapshot)} secțiuni")
c2.metric("Validated Etapa 27", len(validated_ok))
c3.metric("Etapa 28", run28.get("overall_status") or "—")

with st.expander("Surse snapshot", expanded=True):
    if snapshot:
        st.dataframe(
            [
                {"Secțiune": k, "Sursă": snapshot_sources.get(k), "Caractere": len(v)}
                for k, v in snapshot.items()
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.warning("Nu există conținut suficient pentru snapshot.")

# ---------- Existing Stage 29 history ----------
history = rows(
    "opportunity_fit_gate_runs",
    {"user_id": USER_ID, "project_id": PROJECT_ID},
    "created_at",
    20,
)

# ---------- Prompt ----------
def stage28_context():
    out = {
        "overall_status": run28.get("overall_status"),
        "reviewer_result": safe_json(run28.get("reviewer_result")),
        "compliance_result": safe_json(run28.get("compliance_result")),
        "readiness_result": safe_json(run28.get("readiness_result")),
        "summary": safe_json(run28.get("summary")),
        "items": [],
    }
    for i in items28:
        out["items"].append({
            "module": i.get("module_name"),
            "score_before": score(i.get("score_before")),
            "score_after": score(i.get("score_after")),
            "result": safe_json(i.get("result")),
        })
    return out

SYSTEM = """You are the Opportunity Fit & Critical Gap Gate for a grant proposal workflow.
Your task is NOT to improve the proposal and NOT to fabricate missing facts.

Evaluate whether the PROJECT CONTENT actually fits the SELECTED OPPORTUNITY using only the evidence supplied.
Distinguish carefully between:
1. AI_DRAFTABLE: wording/structure can be drafted from already supported facts.
2. USER_EVIDENCE: requires real facts, documents, confirmations or decisions from the applicant.
3. OFFICIAL_VERIFICATION: requires verification against authoritative call documentation/eligibility rules not contained in the supplied evidence.
4. OPPORTUNITY_MISMATCH: the proposal theme/objectives appear materially inconsistent with the selected call.

Rules:
- Never invent TRL, consortium members, eligibility, budget values, KPI baselines/targets, legal status, or call requirements.
- A criticism from an earlier AI review is not proof that the official call truly contains that requirement.
- If official call text is absent, mark claims about official requirements as OFFICIAL_VERIFICATION rather than treating them as established facts.
- Separate 'proposal incomplete' from 'opportunity mismatch'.
- A mismatch verdict requires positive evidence of thematic inconsistency, not merely missing information.
- Return strict JSON only.

JSON schema:
{
  "fit_score": 0-100,
  "verdict": "PROCEED" | "PROCEED_WITH_CONDITIONS" | "STOP_OPPORTUNITY_MISMATCH",
  "confidence": "Low" | "Medium" | "High",
  "project_theme": "string",
  "opportunity_theme_supported_by_evidence": "string",
  "fit_reason": "string",
  "supported_alignment": ["string"],
  "critical_gaps": [
    {
      "title": "string",
      "classification": "AI_DRAFTABLE" | "USER_EVIDENCE" | "OFFICIAL_VERIFICATION" | "OPPORTUNITY_MISMATCH",
      "severity": "Low" | "Medium" | "High" | "Critical",
      "reason": "string",
      "required_next_action": "string",
      "evidence": "string"
    }
  ],
  "claims_requiring_official_call_verification": ["string"],
  "safe_ai_actions": ["string"],
  "blocked_actions": ["string"],
  "next_stage_recommendation": "string"
}"""

def build_payload():
    return {
        "project_id": PROJECT_ID,
        "opportunity_identity": OPPORTUNITY,
        "proposal_snapshot": snapshot,
        "snapshot_sources": snapshot_sources,
        "stage_27_validated_changes_count": len(validated_ok),
        "stage_28_rereview": stage28_context(),
        "important_note": (
            "No official call document is asserted to be present unless its actual text appears in the supplied snapshot/context. "
            "Do not convert prior AI reviewer claims into official call requirements."
        ),
    }

def ai_gate():
    if not oa:
        raise RuntimeError("OPENAI_API_KEY lipsește din secrets.")
    payload = build_payload()
    resp = oa.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0.1,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    )
    return json.loads(resp.choices[0].message.content)

# ---------- Persistence ----------
def persist(result):
    now = datetime.now(timezone.utc).isoformat()
    run_payload = {
        "user_id": USER_ID,
        "project_id": PROJECT_ID,
        "opportunity_identity": OPPORTUNITY or None,
        "rereview_run_id": run28.get("id") or None,
        "snapshot_sections": len(snapshot),
        "fit_score": result.get("fit_score"),
        "verdict": result.get("verdict", "PROCEED_WITH_CONDITIONS"),
        "confidence": result.get("confidence", "Low"),
        "project_theme": result.get("project_theme", ""),
        "opportunity_theme": result.get("opportunity_theme_supported_by_evidence", ""),
        "fit_reason": result.get("fit_reason", ""),
        "result": result,
        "overall_status": "Completed",
        "started_at": now,
        "completed_at": now,
        "updated_at": now,
    }
    created = sb.table("opportunity_fit_gate_runs").insert(run_payload).execute().data
    if not created:
        raise RuntimeError("Nu s-a putut salva run-ul Etapei 29.")
    run_id = created[0]["id"]

    for gap in result.get("critical_gaps", []):
        sb.table("opportunity_fit_gate_items").insert({
            "user_id": USER_ID,
            "project_id": PROJECT_ID,
            "opportunity_identity": OPPORTUNITY or None,
            "gate_run_id": run_id,
            "title": gap.get("title", ""),
            "classification": gap.get("classification", "OFFICIAL_VERIFICATION"),
            "severity": gap.get("severity", "Medium"),
            "reason": gap.get("reason", ""),
            "required_next_action": gap.get("required_next_action", ""),
            "evidence": gap.get("evidence", ""),
            "status": "Open",
        }).execute()
    return run_id

# ---------- UI ----------
st.subheader("Opportunity Fit Gate")

if not snapshot:
    st.error("Etapa 29 este blocată: snapshot-ul propunerii este gol.")
elif st.button("🧭 Rulează Opportunity Fit & Critical Gap Gate", type="primary", use_container_width=True):
    try:
        with st.spinner("Analizez fit-ul fără a inventa informații lipsă..."):
            result = ai_gate()
            persist(result)
        st.success("Etapa 29 a fost executată și salvată.")
        st.rerun()
    except Exception as e:
        st.error(f"Eroare Etapa 29: {e}")

# reload latest
history = rows(
    "opportunity_fit_gate_runs",
    {"user_id": USER_ID, "project_id": PROJECT_ID},
    "created_at",
    20,
)
latest = history[0] if history else None

if latest:
    result = safe_json(latest.get("result"))
    st.divider()
    a, b, c = st.columns(3)
    a.metric("Fit score", f"{result.get('fit_score', latest.get('fit_score', '—'))}/100")
    b.metric("Verdict", result.get("verdict", latest.get("verdict", "—")))
    c.metric("Confidence", result.get("confidence", latest.get("confidence", "—")))

    st.markdown("### Motiv")
    st.write(result.get("fit_reason") or latest.get("fit_reason") or "—")

    st.markdown("### Tema proiectului")
    st.write(result.get("project_theme") or "—")

    st.markdown("### Tema oportunității susținută de dovezile disponibile")
    st.write(result.get("opportunity_theme_supported_by_evidence") or "Nu poate fi confirmată din datele disponibile.")

    gaps = result.get("critical_gaps", [])
    st.markdown("### Critical Gap Gate")
    if gaps:
        for g in gaps:
            cls = g.get("classification", "OFFICIAL_VERIFICATION")
            sev = g.get("severity", "Medium")
            icon = {
                "AI_DRAFTABLE": "✍️",
                "USER_EVIDENCE": "📎",
                "OFFICIAL_VERIFICATION": "🔎",
                "OPPORTUNITY_MISMATCH": "⛔",
            }.get(cls, "•")
            with st.expander(f"{icon} {g.get('title','Gap')} — {cls} / {sev}", expanded=True):
                st.write(g.get("reason", ""))
                st.markdown("**Next action:**")
                st.write(g.get("required_next_action", ""))
                if g.get("evidence"):
                    st.markdown("**Evidence:**")
                    st.write(g.get("evidence"))
    else:
        st.success("Nu au fost identificate gap-uri critice.")

    official = result.get("claims_requiring_official_call_verification", [])
    if official:
        st.warning("Cerințe care NU trebuie tratate ca oficiale până la verificarea documentației apelului:")
        for x in official:
            st.write("•", x)

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("#### AI poate continua în siguranță")
        for x in result.get("safe_ai_actions", []):
            st.write("•", x)
    with col2:
        st.markdown("#### Blocat fără dovezi/verificare")
        for x in result.get("blocked_actions", []):
            st.write("•", x)

    st.info(result.get("next_stage_recommendation", "Etapa 30 trebuie construită pe baza verdictului Etapei 29."))

with st.expander("Istoric Opportunity Fit Gate"):
    if history:
        st.dataframe(
            [{
                "created_at": h.get("created_at"),
                "fit_score": h.get("fit_score"),
                "verdict": h.get("verdict"),
                "confidence": h.get("confidence"),
                "overall_status": h.get("overall_status"),
            } for h in history],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("Nu există rulări Etapa 29.")
