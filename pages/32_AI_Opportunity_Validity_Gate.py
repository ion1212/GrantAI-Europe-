import os
import json
from datetime import datetime, timezone, date
from typing import Any

import streamlit as st
from openai import OpenAI
from supabase import create_client

st.set_page_config(
    page_title="AI Opportunity Validity Gate",
    page_icon="⏳",
    layout="wide",
)

st.title("⏳ Etapa 32 — AI Opportunity Validity & Deadline Gate")
st.caption(
    "Verifică dacă oportunitatea selectată este încă validă pentru continuarea fluxului: "
    "existență, deadline, status, program, regiune și eligibilitate. "
    "Fluxul rămâne blocat dacă oportunitatea este expirată, închisă sau neverificată."
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


def lower(v: Any) -> str:
    return str(v or "").strip().lower()


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


def parse_date(value):
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(text, fmt).date()
        except Exception:
            pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except Exception:
        return None


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
# Opportunity identity
# ---------------------------------------------------------------------
opportunity_identity = ""

for table in (
    "official_call_verification_runs",
    "evidence_requirement_resolution_runs",
    "opportunity_fit_gate_runs",
    "rereview_orchestration_runs",
    "submission_readiness_runs",
):
    r = rows(table, {"user_id": user_id, "project_id": project_id}, "created_at", 1)
    if r and r[0].get("opportunity_identity"):
        opportunity_identity = str(r[0]["opportunity_identity"])
        break

if not opportunity_identity:
    opportunity_identity = str(
        project.get("opportunity_identity")
        or project.get("opportunity_id")
        or ""
    )

st.text_input("Oportunitate", value=opportunity_identity or "—", disabled=True)

if not opportunity_identity:
    st.warning("Nu am putut identifica oportunitatea selectată.")
    st.stop()


# ---------------------------------------------------------------------
# Collect stored opportunity data from likely sources
# ---------------------------------------------------------------------
stored_opportunity = {}

for table in (
    "opportunities",
    "grant_opportunities",
    "funding_opportunities",
):
    rr = rows(table, {"user_id": user_id}, "created_at", 100)
    for row in rr:
        identity_candidates = {
            str(row.get("id") or ""),
            str(row.get("opportunity_identity") or ""),
            str(row.get("reference") or ""),
            str(row.get("call_id") or ""),
            str(row.get("identifier") or ""),
        }
        if opportunity_identity in identity_candidates:
            stored_opportunity = row
            stored_opportunity["_source_table"] = table
            break
    if stored_opportunity:
        break


def field(*names):
    for name in names:
        if stored_opportunity.get(name) not in (None, "", [], {}):
            return stored_opportunity.get(name)
    return None


stored_title = field("title", "name", "opportunity_title")
stored_programme = field("programme", "program", "programme_name")
stored_region = field("country", "country_or_region", "region", "eligible_region")
stored_deadline = field("deadline", "deadline_date", "submission_deadline")
stored_published = field("published_date", "publication_date", "date_published")
stored_status = field("status", "official_status", "call_status")
stored_url = field("url", "source_url", "official_url")
stored_description = field("description", "summary", "content")


# ---------------------------------------------------------------------
# Existing Stage 31 official source info, if any
# ---------------------------------------------------------------------
stage31_runs = rows(
    "official_call_verification_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_identity": opportunity_identity,
    },
    "created_at",
    10,
)
stage31_run = stage31_runs[0] if stage31_runs else None

stage31_items = []
if stage31_run:
    stage31_items = rows(
        "official_call_verification_items",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_identity": opportunity_identity,
            "verification_run_id": stage31_run["id"],
        },
        "created_at",
        100,
    )

official_source_title = ""
official_source_url = ""
official_source_reference = ""

for item in stage31_items:
    if item.get("official_source_title"):
        official_source_title = str(item.get("official_source_title"))
    if item.get("official_source_url"):
        official_source_url = str(item.get("official_source_url"))
    if item.get("official_source_reference"):
        official_source_reference = str(item.get("official_source_reference"))
    if official_source_title or official_source_url:
        break


# ---------------------------------------------------------------------
# User-verifiable source fields
# ---------------------------------------------------------------------
st.subheader("Opportunity source")

source_title = st.text_input(
    "Titlu sursă oficială",
    value=official_source_title or str(stored_title or ""),
    placeholder="Ex.: Funding & Tenders Portal / Guidelines for Applicants",
)

source_url = st.text_input(
    "URL sursă oficială",
    value=official_source_url or str(stored_url or ""),
    placeholder="Pagina oficială a apelului",
)

source_reference = st.text_input(
    "Referință exactă",
    value=official_source_reference,
    placeholder="Ex.: call page / section / page",
)

official_text = st.text_area(
    "Text oficial relevant",
    value=str(stored_description or ""),
    height=220,
    placeholder=(
        "Lipește aici textul oficial relevant privind statusul apelului, deadline-ul, "
        "programul, țara/regiunea și eligibilitatea."
    ),
)

c1, c2 = st.columns(2)

with c1:
    manual_deadline = st.text_input(
        "Deadline cunoscut",
        value=str(stored_deadline or ""),
        placeholder="YYYY-MM-DD",
    )
    manual_status = st.text_input(
        "Status oficial cunoscut",
        value=str(stored_status or ""),
        placeholder="Open / Closed / Archived / etc.",
    )

with c2:
    manual_programme = st.text_input(
        "Program",
        value=str(stored_programme or ""),
    )
    manual_region = st.text_input(
        "Țară / regiune eligibilă",
        value=str(stored_region or ""),
    )


# ---------------------------------------------------------------------
# Local deterministic deadline check
# ---------------------------------------------------------------------
deadline_date = parse_date(manual_deadline)
today = date.today()

if deadline_date:
    if deadline_date < today:
        local_deadline_status = "Fail"
        local_deadline_reason = f"Deadline {deadline_date.isoformat()} este anterior datei curente {today.isoformat()}."
    else:
        local_deadline_status = "Pass"
        local_deadline_reason = f"Deadline {deadline_date.isoformat()} nu a expirat."
else:
    local_deadline_status = "Unknown"
    local_deadline_reason = "Deadline-ul nu poate fi determinat din datele disponibile."

status_lower = lower(manual_status)
if any(x in status_lower for x in ("closed", "expired", "archived", "ended")):
    local_open_status = "Fail"
elif any(x in status_lower for x in ("open", "active", "accepting")):
    local_open_status = "Pass"
else:
    local_open_status = "Unknown"


# ---------------------------------------------------------------------
# Existing Stage 32 run
# ---------------------------------------------------------------------
existing_runs = rows(
    "opportunity_validity_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_identity": opportunity_identity,
    },
    "created_at",
    50,
)

latest_run = existing_runs[0] if existing_runs else None


# ---------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------
m1, m2, m3, m4 = st.columns(4)
m1.metric("Deadline check", local_deadline_status)
m2.metric("Open status", local_open_status)
m3.metric(
    "Latest verdict",
    str(latest_run.get("validity_verdict") if latest_run else "UNKNOWN"),
)
m4.metric(
    "Workflow",
    "BLOCKED"
    if not latest_run or bool(latest_run.get("workflow_blocked"))
    else "CAN CONTINUE",
)

st.info(
    "Etapa 32 nu presupune că oportunitatea este validă doar pentru că există în baza de date. "
    "Dacă statusul sau deadline-ul nu pot fi confirmate, verdictul rămâne UNKNOWN și fluxul rămâne blocat."
)


# ---------------------------------------------------------------------
# AI verification
# ---------------------------------------------------------------------
SYSTEM = """You are a strict grant opportunity validity and deadline verifier.

You receive:
- an opportunity identifier;
- stored opportunity metadata;
- official-source title/url/reference;
- official source text supplied by the user/application;
- current date;
- a project context.

Your task is to classify whether the opportunity is still valid for continuing the grant workflow.

STRICT RULES:
- Use only supplied data.
- Never invent a deadline, programme, country, region, eligibility rule or call status.
- If the official text does not establish a fact, mark it Unknown / Needs verification.
- If deadline is before current date, verdict must be EXPIRED.
- If official status clearly says closed/archived/ended, verdict must be CLOSED.
- INELIGIBLE_REGION only if supplied data positively establishes geographic incompatibility.
- INELIGIBLE_PROGRAMME only if supplied data positively establishes programme incompatibility.
- VALID only when existence, deadline/open status, and basic programme/region fit are sufficiently supported.
- If evidence is incomplete, verdict must be UNKNOWN.
- Return JSON only.

Schema:
{
  "opportunity_title": "",
  "programme": "",
  "country_or_region": "",
  "published_date": null,
  "deadline_date": null,
  "official_status": "",
  "inferred_status": "",
  "accepts_applications": false,
  "validity_verdict": "VALID|EXPIRED|CLOSED|UNKNOWN|INELIGIBLE_REGION|INELIGIBLE_PROGRAMME",
  "confidence": "Low|Medium|High",
  "verification_reason": "",
  "workflow_blocked": true,
  "checks": [
    {
      "check_type": "Existence|Deadline|Open status|Programme|Country or region|Applicant eligibility|Official source",
      "check_status": "Pass|Fail|Unknown|Needs verification",
      "observed_value": "",
      "expected_value": "",
      "verification_reason": "",
      "confidence": "Low|Medium|High"
    }
  ]
}"""


def verify_opportunity():
    client = get_openai()

    payload = {
        "current_date": today.isoformat(),
        "opportunity_identity": opportunity_identity,
        "stored_opportunity": stored_opportunity,
        "project": {
            "id": project_id,
            "name": project.get("name") or project.get("title") or "",
            "description": project.get("description") or "",
        },
        "official_source": {
            "title": source_title,
            "url": source_url,
            "reference": source_reference,
            "text": official_text,
        },
        "manual_fields": {
            "deadline": manual_deadline,
            "official_status": manual_status,
            "programme": manual_programme,
            "country_or_region": manual_region,
        },
        "deterministic_checks": {
            "deadline_status": local_deadline_status,
            "deadline_reason": local_deadline_reason,
            "open_status": local_open_status,
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
        raise ValueError("Răspuns AI invalid.")

    return result


# ---------------------------------------------------------------------
# Run Stage 32
# ---------------------------------------------------------------------
if st.button(
    "⏳ Verifică validitatea oportunității",
    type="primary",
    use_container_width=True,
):
    with st.spinner("Verific oportunitatea și deadline-ul..."):
        try:
            result = verify_opportunity()

            verdict = str(result.get("validity_verdict") or "UNKNOWN")
            if verdict not in (
                "VALID",
                "EXPIRED",
                "CLOSED",
                "UNKNOWN",
                "INELIGIBLE_REGION",
                "INELIGIBLE_PROGRAMME",
            ):
                verdict = "UNKNOWN"

            # Deterministic deadline/status always overrides AI optimism.
            if local_deadline_status == "Fail":
                verdict = "EXPIRED"
                result["workflow_blocked"] = True

            if local_open_status == "Fail" and verdict != "EXPIRED":
                verdict = "CLOSED"
                result["workflow_blocked"] = True

            confidence = str(result.get("confidence") or "Low")
            if confidence not in ("Low", "Medium", "High"):
                confidence = "Low"

            workflow_blocked = bool(result.get("workflow_blocked"))
            if verdict != "VALID":
                workflow_blocked = True

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
            fit_run_id = fit_runs[0].get("id") if fit_runs else None

            run_insert = (
                supabase.table("opportunity_validity_runs")
                .insert({
                    "user_id": user_id,
                    "project_id": project_id,
                    "opportunity_identity": opportunity_identity,
                    "opportunity_fit_gate_run_id": fit_run_id,
                    "official_call_verification_run_id": stage31_run.get("id") if stage31_run else None,
                    "opportunity_title": str(result.get("opportunity_title") or stored_title or ""),
                    "programme": str(result.get("programme") or manual_programme or ""),
                    "country_or_region": str(result.get("country_or_region") or manual_region or ""),
                    "published_date": result.get("published_date") or None,
                    "deadline_date": result.get("deadline_date") or (deadline_date.isoformat() if deadline_date else None),
                    "official_status": str(result.get("official_status") or manual_status or ""),
                    "inferred_status": str(result.get("inferred_status") or ""),
                    "accepts_applications": bool(result.get("accepts_applications")),
                    "validity_verdict": verdict,
                    "confidence": confidence,
                    "official_source_title": source_title.strip(),
                    "official_source_url": source_url.strip(),
                    "official_source_reference": source_reference.strip(),
                    "verification_reason": str(result.get("verification_reason") or ""),
                    "workflow_blocked": workflow_blocked,
                    "overall_status": (
                        "Completed"
                        if verdict == "VALID"
                        else "Blocked"
                        if verdict in ("EXPIRED", "CLOSED", "INELIGIBLE_REGION", "INELIGIBLE_PROGRAMME")
                        else "Needs verification"
                    ),
                    "metadata": {
                        "stage": 32,
                        "source_table": stored_opportunity.get("_source_table"),
                        "deterministic_deadline_status": local_deadline_status,
                        "deterministic_open_status": local_open_status,
                    },
                    "updated_at": now_iso(),
                })
                .execute()
            )

            run_data = run_insert.data or []
            if not run_data:
                raise RuntimeError("Nu am putut salva run-ul Etapei 32.")

            validity_run_id = str(run_data[0]["id"])

            for check in result.get("checks", []):
                check_type = str(check.get("check_type") or "Official source")
                if check_type not in (
                    "Existence",
                    "Deadline",
                    "Open status",
                    "Programme",
                    "Country or region",
                    "Applicant eligibility",
                    "Official source",
                ):
                    check_type = "Official source"

                check_status = str(check.get("check_status") or "Unknown")
                if check_status not in ("Pass", "Fail", "Unknown", "Needs verification"):
                    check_status = "Unknown"

                check_confidence = str(check.get("confidence") or "Low")
                if check_confidence not in ("Low", "Medium", "High"):
                    check_confidence = "Low"

                supabase.table("opportunity_validity_checks").insert({
                    "user_id": user_id,
                    "project_id": project_id,
                    "opportunity_identity": opportunity_identity,
                    "validity_run_id": validity_run_id,
                    "check_type": check_type,
                    "check_status": check_status,
                    "observed_value": str(check.get("observed_value") or ""),
                    "expected_value": str(check.get("expected_value") or ""),
                    "verification_reason": str(check.get("verification_reason") or ""),
                    "official_source_title": source_title.strip(),
                    "official_source_url": source_url.strip(),
                    "official_source_reference": source_reference.strip(),
                    "confidence": check_confidence,
                    "updated_at": now_iso(),
                }).execute()

            st.success(f"Verdict Etapa 32: {verdict}")
            st.rerun()

        except Exception as exc:
            st.error(f"Etapa 32 nu a putut finaliza verificarea: {exc}")


# ---------------------------------------------------------------------
# Reload result
# ---------------------------------------------------------------------
existing_runs = rows(
    "opportunity_validity_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_identity": opportunity_identity,
    },
    "created_at",
    50,
)

latest_run = existing_runs[0] if existing_runs else None

st.divider()
st.subheader("Opportunity Validity Result")

if not latest_run:
    st.caption("Nu există încă o verificare Etapa 32.")
else:
    r1, r2, r3, r4 = st.columns(4)
    r1.metric("Verdict", str(latest_run.get("validity_verdict") or "UNKNOWN"))
    r2.metric("Confidence", str(latest_run.get("confidence") or "Low"))
    r3.metric(
        "Accepts applications",
        "YES" if bool(latest_run.get("accepts_applications")) else "NO / UNKNOWN",
    )
    r4.metric(
        "Workflow",
        "BLOCKED" if bool(latest_run.get("workflow_blocked")) else "CAN CONTINUE",
    )

    if latest_run.get("deadline_date"):
        st.write(f"**Deadline:** {latest_run.get('deadline_date')}")

    if latest_run.get("official_status"):
        st.write(f"**Official status:** {latest_run.get('official_status')}")

    if latest_run.get("programme"):
        st.write(f"**Programme:** {latest_run.get('programme')}")

    if latest_run.get("country_or_region"):
        st.write(f"**Country / region:** {latest_run.get('country_or_region')}")

    st.write("**Verification reason:**")
    st.write(latest_run.get("verification_reason") or "—")

    if bool(latest_run.get("workflow_blocked")):
        st.error(
            "Fluxul este blocat pentru această oportunitate. "
            "Nu continua cu pregătirea finală până când oportunitatea nu devine VALID."
        )
    else:
        st.success(
            "Oportunitatea a trecut gate-ul de validitate. "
            "Fluxul poate continua către etapa următoare."
        )

    checks = rows(
        "opportunity_validity_checks",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_identity": opportunity_identity,
            "validity_run_id": latest_run["id"],
        },
        "created_at",
        100,
    )

    with st.expander("Detalii verificări", expanded=True):
        if checks:
            st.dataframe(
                [
                    {
                        "Check": c.get("check_type"),
                        "Status": c.get("check_status"),
                        "Observed": c.get("observed_value"),
                        "Reason": c.get("verification_reason"),
                        "Confidence": c.get("confidence"),
                    }
                    for c in checks
                ],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.caption("Nu există checks salvate.")


with st.expander("Istoric Etapa 32"):
    if existing_runs:
        st.dataframe(
            [
                {
                    "created_at": r.get("created_at"),
                    "verdict": r.get("validity_verdict"),
                    "deadline": r.get("deadline_date"),
                    "official_status": r.get("official_status"),
                    "confidence": r.get("confidence"),
                    "workflow_blocked": r.get("workflow_blocked"),
                    "overall_status": r.get("overall_status"),
                }
                for r in existing_runs
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("Nu există rulări Etapa 32.")

st.caption(
    "Etapa 32 este un gate de siguranță: oportunitățile expirate, închise, incompatibile "
    "sau insuficient verificate nu trebuie propagate în etapele următoare."
)
