import os
from datetime import date, datetime, timezone
from typing import Any

import streamlit as st
from supabase import create_client

st.set_page_config(
    page_title="Opportunity Engine Validity Guard",
    page_icon="🛡️",
    layout="wide",
)

st.title("🛡️ Etapa 33 — Opportunity Engine Validity Guard")
st.caption(
    "Scanează oportunitățile înainte de scoring și workflow. "
    "Doar oportunitățile confirmate VALID pot continua."
)


# ---------------------------------------------------------------------
# Supabase / authentication
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


def lower(value: Any) -> str:
    return str(value or "").strip().lower()


def parse_date(value: Any):
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except Exception:
            pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except Exception:
        return None


def rows(table: str, filters=None, order="created_at", limit=500):
    try:
        q = supabase.table(table).select("*")
        for key, value in (filters or {}).items():
            if value not in (None, ""):
                q = q.eq(key, value)
        if order:
            q = q.order(order, desc=True)
        if limit:
            q = q.limit(limit)
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
# Project selector
# ---------------------------------------------------------------------
projects = rows("projects", {"user_id": user_id}, "created_at", 100)

project_options = {"Toate proiectele": None}
for p in projects:
    label = f"{p.get('name') or p.get('title') or 'Project'} — {str(p.get('id'))[:8]}"
    project_options[label] = p

selected_project_label = st.selectbox("Project", list(project_options.keys()))
selected_project = project_options[selected_project_label]
project_id = str(selected_project["id"]) if selected_project else None


# ---------------------------------------------------------------------
# Discover opportunity records from existing tables
# ---------------------------------------------------------------------
candidate_tables = (
    "opportunities",
    "grant_opportunities",
    "funding_opportunities",
)

opportunities = []
source_table = None

for table in candidate_tables:
    data = rows(table, {"user_id": user_id}, "created_at", 500)
    if data:
        opportunities = data
        source_table = table
        break

# Fallback: derive unique opportunity identities already used by projects/workflow.
if not opportunities:
    seen = set()
    fallback_tables = (
        "opportunity_fit_gate_runs",
        "evidence_requirement_resolution_runs",
        "official_call_verification_runs",
        "opportunity_validity_runs",
        "rereview_orchestration_runs",
    )
    for table in fallback_tables:
        filters = {"user_id": user_id}
        if project_id:
            filters["project_id"] = project_id
        for row in rows(table, filters, "created_at", 500):
            identity = str(row.get("opportunity_identity") or "").strip()
            if identity and identity not in seen:
                seen.add(identity)
                opportunities.append({
                    "id": None,
                    "opportunity_identity": identity,
                    "title": identity,
                    "project_id": row.get("project_id"),
                    "_derived": True,
                })
    source_table = "workflow history"

if project_id:
    filtered = []
    for opp in opportunities:
        opp_project = str(opp.get("project_id") or "")
        if not opp_project or opp_project == project_id:
            filtered.append(opp)
    opportunities = filtered

if not opportunities:
    st.warning("Nu am găsit oportunități de verificat.")
    st.stop()

st.write(f"**Sursă oportunități:** {source_table}")
st.write(f"**Oportunități găsite:** {len(opportunities)}")


# ---------------------------------------------------------------------
# Helpers to normalize opportunity fields
# ---------------------------------------------------------------------
def first_value(row: dict, *keys):
    for key in keys:
        value = row.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def opportunity_identity_of(row: dict) -> str:
    return str(
        first_value(
            row,
            "opportunity_identity",
            "reference",
            "call_id",
            "identifier",
            "id",
        )
        or ""
    ).strip()


def title_of(row: dict) -> str:
    return str(first_value(row, "title", "name", "opportunity_title") or opportunity_identity_of(row))


def programme_of(row: dict) -> str:
    return str(first_value(row, "programme", "program", "programme_name") or "")


def region_of(row: dict) -> str:
    return str(first_value(row, "country_or_region", "country", "region", "eligible_region") or "")


def deadline_of(row: dict):
    return parse_date(first_value(row, "deadline_date", "deadline", "submission_deadline"))


def status_of(row: dict) -> str:
    return str(first_value(row, "official_status", "status", "call_status") or "")


def url_of(row: dict) -> str:
    return str(first_value(row, "official_url", "source_url", "url") or "")


# ---------------------------------------------------------------------
# Latest Stage 32 verdicts are authoritative guard input when present
# ---------------------------------------------------------------------
stage32_rows = rows(
    "opportunity_validity_runs",
    {"user_id": user_id},
    "created_at",
    500,
)

latest_stage32 = {}
for r in stage32_rows:
    if project_id and str(r.get("project_id") or "") != project_id:
        continue
    identity = str(r.get("opportunity_identity") or "").strip()
    if identity and identity not in latest_stage32:
        latest_stage32[identity] = r


# ---------------------------------------------------------------------
# Deterministic guard classification
# ---------------------------------------------------------------------
today = date.today()


def classify(row: dict):
    identity = opportunity_identity_of(row)
    stage32 = latest_stage32.get(identity)

    title = title_of(row)
    programme = programme_of(row)
    region = region_of(row)
    deadline = deadline_of(row)
    status = status_of(row)
    source_url = url_of(row)

    if stage32:
        title = str(stage32.get("opportunity_title") or title)
        programme = str(stage32.get("programme") or programme)
        region = str(stage32.get("country_or_region") or region)
        deadline = parse_date(stage32.get("deadline_date")) or deadline
        status = str(stage32.get("official_status") or status)
        source_url = str(stage32.get("official_source_url") or source_url)

        verdict = str(stage32.get("validity_verdict") or "UNKNOWN")
        confidence = str(stage32.get("confidence") or "Low")
        reason = str(stage32.get("verification_reason") or "")
        stage32_blocked = bool(stage32.get("workflow_blocked"))

        if verdict == "VALID" and not stage32_blocked:
            validity = "VALID"
            deadline_verified = deadline is not None
            status_verified = bool(status)
            eligible = True
        elif verdict == "EXPIRED":
            validity = "EXPIRED"
            deadline_verified = deadline is not None
            status_verified = bool(status)
            eligible = False
        elif verdict == "CLOSED":
            validity = "CLOSED"
            deadline_verified = deadline is not None
            status_verified = bool(status)
            eligible = False
        else:
            validity = "BLOCKED" if verdict in ("INELIGIBLE_REGION", "INELIGIBLE_PROGRAMME") else "UNKNOWN"
            deadline_verified = deadline is not None
            status_verified = bool(status)
            eligible = False

        return {
            "identity": identity,
            "title": title,
            "programme": programme,
            "region": region,
            "deadline": deadline,
            "status": status,
            "validity": validity,
            "deadline_verified": deadline_verified,
            "status_verified": status_verified,
            "eligible": eligible,
            "reason": reason or f"Preluat din Etapa 32: {verdict}.",
            "source_url": source_url,
            "confidence": confidence,
            "source": "Etapa 32",
            "opportunity_id": row.get("id"),
        }

    status_l = lower(status)

    if deadline and deadline < today:
        validity = "EXPIRED"
        reason = f"Deadline {deadline.isoformat()} este anterior datei curente {today.isoformat()}."
        confidence = "High"
        eligible = False
        deadline_verified = True
        status_verified = bool(status)

    elif any(x in status_l for x in ("closed", "expired", "archived", "ended")):
        validity = "CLOSED"
        reason = f"Statusul stocat indică faptul că apelul nu este deschis: {status}."
        confidence = "Medium"
        eligible = False
        deadline_verified = deadline is not None
        status_verified = True

    elif deadline and deadline >= today and any(x in status_l for x in ("open", "active", "accepting")):
        validity = "VALID"
        reason = "Deadline-ul este în viitor și statusul stocat indică un apel deschis."
        confidence = "Medium"
        eligible = True
        deadline_verified = True
        status_verified = True

    else:
        validity = "UNKNOWN"
        reason = "Nu există suficiente date verificate pentru a permite scoring/workflow."
        confidence = "Low"
        eligible = False
        deadline_verified = deadline is not None
        status_verified = False

    return {
        "identity": identity,
        "title": title,
        "programme": programme,
        "region": region,
        "deadline": deadline,
        "status": status,
        "validity": validity,
        "deadline_verified": deadline_verified,
        "status_verified": status_verified,
        "eligible": eligible,
        "reason": reason,
        "source_url": source_url,
        "confidence": confidence,
        "source": source_table,
        "opportunity_id": row.get("id"),
    }


classified = [classify(o) for o in opportunities]
classified = [c for c in classified if c["identity"]]


# ---------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------
counts = {
    key: sum(1 for c in classified if c["validity"] == key)
    for key in ("VALID", "EXPIRED", "CLOSED", "UNKNOWN", "BLOCKED")
}

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("VALID", counts["VALID"])
c2.metric("EXPIRED", counts["EXPIRED"])
c3.metric("CLOSED", counts["CLOSED"])
c4.metric("UNKNOWN", counts["UNKNOWN"])
c5.metric("BLOCKED", counts["BLOCKED"])

st.info(
    "Regula Etapei 33: UNKNOWN nu este eligibil pentru scoring. "
    "Doar VALID setează eligible_for_scoring=true și eligible_for_workflow=true."
)

st.subheader("Guard preview")
st.dataframe(
    [
        {
            "Opportunity": c["title"],
            "Identity": c["identity"],
            "Deadline": c["deadline"].isoformat() if c["deadline"] else "",
            "Status": c["status"],
            "Validity": c["validity"],
            "Scoring": "YES" if c["eligible"] else "NO",
            "Workflow": "YES" if c["eligible"] else "NO",
            "Confidence": c["confidence"],
            "Source": c["source"],
        }
        for c in classified
    ],
    use_container_width=True,
    hide_index=True,
)


# ---------------------------------------------------------------------
# Save guard run
# ---------------------------------------------------------------------
if st.button(
    "🛡️ Rulează Opportunity Engine Validity Guard",
    type="primary",
    use_container_width=True,
):
    with st.spinner("Aplic gate-ul de validitate oportunităților..."):
        try:
            run_insert = (
                supabase.table("opportunity_engine_guard_runs")
                .insert({
                    "user_id": user_id,
                    "project_id": project_id,
                    "total_opportunities": len(classified),
                    "valid_opportunities": counts["VALID"],
                    "expired_opportunities": counts["EXPIRED"],
                    "closed_opportunities": counts["CLOSED"],
                    "unknown_opportunities": counts["UNKNOWN"],
                    "blocked_opportunities": counts["BLOCKED"],
                    "run_status": "Running",
                    "summary": {
                        "stage": 33,
                        "source_table": source_table,
                        "rule": "Only VALID opportunities are eligible for scoring/workflow.",
                    },
                    "updated_at": now_iso(),
                })
                .execute()
            ).data or []

            if not run_insert:
                raise RuntimeError("Nu am putut crea run-ul Etapei 33.")

            run_id = str(run_insert[0]["id"])

            for c in classified:
                # opportunity_id is UUID only when it really looks like a UUID.
                opportunity_id = c["opportunity_id"]
                if opportunity_id:
                    opportunity_id = str(opportunity_id)
                    if len(opportunity_id) != 36:
                        opportunity_id = None

                supabase.table("opportunity_engine_validity_checks").insert({
                    "user_id": user_id,
                    "project_id": project_id,
                    "opportunity_id": opportunity_id,
                    "opportunity_identity": c["identity"],
                    "opportunity_title": c["title"],
                    "programme": c["programme"],
                    "country_or_region": c["region"],
                    "deadline_date": c["deadline"].isoformat() if c["deadline"] else None,
                    "opportunity_status": c["status"],
                    "validity_status": c["validity"],
                    "deadline_verified": bool(c["deadline_verified"]),
                    "status_verified": bool(c["status_verified"]),
                    "eligible_for_scoring": bool(c["eligible"]),
                    "eligible_for_workflow": bool(c["eligible"]),
                    "verification_reason": c["reason"],
                    "source_title": c["source"],
                    "source_url": c["source_url"],
                    "source_reference": (
                        f"Stage 32 run {latest_stage32[c['identity']]['id']}"
                        if c["identity"] in latest_stage32
                        else ""
                    ),
                    "confidence": c["confidence"],
                    "metadata": {
                        "stage": 33,
                        "guard_run_id": run_id,
                        "guard_date": today.isoformat(),
                    },
                    "updated_at": now_iso(),
                }).execute()

            supabase.table("opportunity_engine_guard_runs").update({
                "run_status": "Completed",
                "updated_at": now_iso(),
            }).eq("id", run_id).eq("user_id", user_id).execute()

            st.success(
                f"Guard finalizat: {counts['VALID']} VALID din {len(classified)} oportunități."
            )
            st.rerun()

        except Exception as exc:
            st.error(f"Etapa 33 nu a putut salva rezultatele: {exc}")


# ---------------------------------------------------------------------
# Latest saved checks
# ---------------------------------------------------------------------
st.divider()
st.subheader("Latest Guard Results")

saved_checks = rows(
    "opportunity_engine_validity_checks",
    {"user_id": user_id},
    "created_at",
    500,
)

if project_id:
    saved_checks = [
        r for r in saved_checks
        if str(r.get("project_id") or "") == project_id
    ]

latest_by_identity = {}
for row in saved_checks:
    identity = str(row.get("opportunity_identity") or "")
    if identity and identity not in latest_by_identity:
        latest_by_identity[identity] = row

if latest_by_identity:
    st.dataframe(
        [
            {
                "Opportunity": r.get("opportunity_title"),
                "Identity": r.get("opportunity_identity"),
                "Validity": r.get("validity_status"),
                "Deadline": r.get("deadline_date"),
                "Scoring": r.get("eligible_for_scoring"),
                "Workflow": r.get("eligible_for_workflow"),
                "Confidence": r.get("confidence"),
                "Reason": r.get("verification_reason"),
            }
            for r in latest_by_identity.values()
        ],
        use_container_width=True,
        hide_index=True,
    )
else:
    st.caption("Nu există încă rezultate salvate.")


# ---------------------------------------------------------------------
# History
# ---------------------------------------------------------------------
with st.expander("Istoric Etapa 33"):
    filters = {"user_id": user_id}
    if project_id:
        filters["project_id"] = project_id

    history = rows(
        "opportunity_engine_guard_runs",
        filters,
        "created_at",
        100,
    )

    if history:
        st.dataframe(
            [
                {
                    "created_at": r.get("created_at"),
                    "total": r.get("total_opportunities"),
                    "valid": r.get("valid_opportunities"),
                    "expired": r.get("expired_opportunities"),
                    "closed": r.get("closed_opportunities"),
                    "unknown": r.get("unknown_opportunities"),
                    "blocked": r.get("blocked_opportunities"),
                    "status": r.get("run_status"),
                }
                for r in history
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("Nu există rulări Etapa 33.")

st.caption(
    "Important: această pagină creează guard-ul și rezultatele de eligibilitate. "
    "Modulele de Opportunity Scoring / Writer trebuie să consulte "
    "opportunity_engine_validity_checks și să accepte numai ultimul rezultat VALID "
    "cu eligible_for_scoring=true / eligible_for_workflow=true."
)
