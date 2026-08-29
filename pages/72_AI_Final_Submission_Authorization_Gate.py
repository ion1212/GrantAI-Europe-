import os
import json
import hashlib
from datetime import datetime, timezone, date
from typing import Any
from urllib.parse import urlparse

import streamlit as st
from supabase import create_client


# =====================================================================
# STAGE 72 v1.0 — AI FINAL SUBMISSION AUTHORIZATION GATE
#
# Purpose:
#   Consume ONLY a completed Stage 71 run with outcome
#   READY_FOR_SUBMISSION_AUTHORIZATION and record explicit human
#   authorization for a future controlled submission-execution stage.
#
# Stage 72 DOES NOT:
#   - press Submit
#   - upload files
#   - sign declarations
#   - create financial commitments
#   - claim European Commission receipt
#   - collect credentials, MFA, cookies or tokens
#
# Stage 72 verifies:
#   - ACTIVE opportunity lock
#   - valid deadline
#   - Stage 71 COMPLETED + READY_FOR_SUBMISSION_AUTHORIZATION
#   - Stage 71 readiness evidence SHA256 stable
#   - Stage 71 run fingerprint stable
#   - exact application reference remains bound
#   - Part A is explicitly confirmed complete/resolved
#   - no blocking validation errors are visible in the portal
#   - Part B/package remains attached
#   - proposal remains editable and unsubmitted
#   - no final submission receipt exists
#
# Human authorization phrase:
#   AUTHORIZE STAGE 72 FINAL SUBMISSION
#
# Outcome:
#   FINAL_SUBMISSION_AUTHORIZED
#
# Handoff:
#   A future Stage 73 may consume ONLY FINAL_SUBMISSION_AUTHORIZED.
# =====================================================================


st.set_page_config(
    page_title="Stage 72 v1.0 — Final Submission Authorization",
    page_icon="🔐",
    layout="wide",
)

st.title("🔐 Etapa 72 v1.0 — AI Final Submission Authorization Gate")

st.caption(
    "Stage 72 înregistrează numai autorizarea explicită pentru o etapă viitoare "
    "de submit controlat. NU apasă Submit și NU trimite propunerea."
)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

OFFICIAL_HOST_SUFFIXES = ("ec.europa.eu", "europa.eu")


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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def stable_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def future_deadline(value: Any) -> bool:
    if not value:
        return False
    try:
        return date.fromisoformat(str(value)[:10]) >= datetime.now(timezone.utc).date()
    except Exception:
        return False


def official_domain_ok(url: str) -> bool:
    try:
        parsed = urlparse(normalize_text(url))
        if parsed.scheme.lower() != "https":
            return False
        host = (parsed.hostname or "").lower().strip(".")
        return any(
            host == suffix or host.endswith("." + suffix)
            for suffix in OFFICIAL_HOST_SUFFIXES
        )
    except Exception:
        return False


def rows(table: str, filters=None, order="created_at", limit=1000):
    q = supabase.table(table).select("*")
    for key, value in (filters or {}).items():
        if value not in (None, ""):
            q = q.eq(key, value)
    if order:
        q = q.order(order, desc=True)
    if limit:
        q = q.limit(limit)
    return q.execute().data or []


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


def project_label(project: dict) -> str:
    return f"{project.get('name') or 'Project'} — {str(project.get('id') or '')[:8]}"


# ---------------------------------------------------------------------
# Supabase / auth
# ---------------------------------------------------------------------

try:
    supabase = get_supabase()
except Exception as exc:
    st.error(f"Supabase initialization failed: {type(exc).__name__}: {exc}")
    st.stop()

restore_auth_session(supabase)

user_id = current_user_id(supabase)
if not user_id:
    st.error("Stage 72 BLOCKED: utilizatorul autentificat nu poate fi identificat.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)
if not projects:
    st.warning("Nu există proiecte.")
    st.stop()

project_map = {project_label(p): p for p in projects}
project = project_map[
    st.selectbox(
        "Project",
        list(project_map.keys()),
        key="stage72_project",
    )
]
project_id = str(project["id"])


# ---------------------------------------------------------------------
# Opportunity lock
# ---------------------------------------------------------------------

locks = rows(
    "selected_opportunity_locks",
    {
        "user_id": user_id,
        "project_id": project_id,
        "lock_status": "ACTIVE",
    },
    "created_at",
    10,
)

if not locks:
    st.error("Stage 72 BLOCKED: nu există opportunity lock ACTIVE.")
    st.stop()

lock = locks[0]
lock_id = str(lock["id"])
identity = normalize_text(lock.get("opportunity_identity"))
deadline = lock.get("official_deadline")
workflow_allowed = bool(lock.get("workflow_allowed"))

st.write(f"**Locked opportunity:** {identity or '—'}")
st.write(f"**Deadline:** {str(deadline or '—')[:10]}")
st.write(f"**Lock ID:** `{lock_id}`")


# ---------------------------------------------------------------------
# Stage 71
# ---------------------------------------------------------------------

stage71_candidates = rows(
    "stage71_final_submission_readiness_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    100,
)

stage71 = next(
    (
        r for r in stage71_candidates
        if normalize_text(r.get("run_status")).upper() == "COMPLETED"
        and normalize_text(r.get("readiness_outcome")).upper()
        == "READY_FOR_SUBMISSION_AUTHORIZATION"
        and not bool(r.get("blocking_errors_present"))
        and not bool(r.get("external_submission_performed"))
        and not bool(r.get("external_receipt_obtained"))
    ),
    None,
)

if not stage71:
    st.error(
        "Stage 72 BLOCKED: nu există un Stage 71 COMPLETED cu "
        "READY_FOR_SUBMISSION_AUTHORIZATION pentru acest proiect/lock."
    )
    st.stop()

stage71_run_id = str(stage71.get("id") or "")
stage70_run_id = str(stage71.get("stage70_run_id") or "")
application_reference = normalize_text(stage71.get("application_reference"))
draft_title = normalize_text(stage71.get("draft_title"))
current_portal_url = normalize_text(stage71.get("current_portal_url"))

stage71_readiness_sha = normalize_text(stage71.get("readiness_evidence_sha256"))
stage71_run_fingerprint = normalize_text(stage71.get("run_fingerprint"))

stage71_readiness_payload = as_dict(stage71.get("readiness_payload"))
stage71_run_payload = as_dict(stage71.get("run_payload"))

recomputed_stage71_readiness_sha = (
    stable_sha256(stage71_readiness_payload)
    if stage71_readiness_payload
    else ""
)

recomputed_stage71_run_fingerprint = (
    stable_sha256(stage71_run_payload)
    if stage71_run_payload
    else ""
)


# ---------------------------------------------------------------------
# Hard gate
# ---------------------------------------------------------------------

checks = []


def add_check(name: str, passed: bool, detail: str):
    checks.append({
        "Check": name,
        "PASS": bool(passed),
        "Detail": detail,
    })


add_check(
    "ACTIVE lock",
    normalize_text(lock.get("lock_status")).upper() == "ACTIVE",
    normalize_text(lock.get("lock_status")).upper(),
)

add_check(
    "Workflow allowed",
    workflow_allowed,
    f"workflow_allowed={workflow_allowed}",
)

add_check(
    "Deadline valid",
    future_deadline(deadline),
    str(deadline or "")[:10],
)

add_check(
    "Stage 71 exists",
    bool(stage71),
    stage71_run_id or "MISSING",
)

add_check(
    "Stage 71 COMPLETED",
    normalize_text(stage71.get("run_status")).upper() == "COMPLETED",
    normalize_text(stage71.get("run_status")).upper(),
)

add_check(
    "Stage 71 READY_FOR_SUBMISSION_AUTHORIZATION",
    normalize_text(stage71.get("readiness_outcome")).upper()
    == "READY_FOR_SUBMISSION_AUTHORIZATION",
    normalize_text(stage71.get("readiness_outcome")).upper(),
)

add_check(
    "Stage 71 no blocking errors",
    not bool(stage71.get("blocking_errors_present")),
    f"blocking_errors_present={stage71.get('blocking_errors_present')}",
)

add_check(
    "Stage 71 not submitted",
    not bool(stage71.get("external_submission_performed")),
    f"external_submission_performed={stage71.get('external_submission_performed')}",
)

add_check(
    "Stage 71 no receipt",
    not bool(stage71.get("external_receipt_obtained")),
    f"external_receipt_obtained={stage71.get('external_receipt_obtained')}",
)

add_check(
    "Stage 71 readiness SHA256 stable",
    bool(stage71_readiness_sha)
    and stage71_readiness_sha == recomputed_stage71_readiness_sha,
    (
        f"stored={stage71_readiness_sha[:16]}..., "
        f"recomputed={recomputed_stage71_readiness_sha[:16]}..."
    ),
)

add_check(
    "Stage 71 run fingerprint stable",
    bool(stage71_run_fingerprint)
    and stage71_run_fingerprint == recomputed_stage71_run_fingerprint,
    (
        f"stored={stage71_run_fingerprint[:16]}..., "
        f"recomputed={recomputed_stage71_run_fingerprint[:16]}..."
    ),
)

add_check(
    "Application reference present",
    len(application_reference) >= 3,
    application_reference or "MISSING",
)

add_check(
    "Official portal URL valid",
    official_domain_ok(current_portal_url),
    current_portal_url or "MISSING",
)

stage72_base_gate = (
    "READY"
    if all(c["PASS"] for c in checks)
    else "BLOCKED"
)


# ---------------------------------------------------------------------
# Existing Stage 72
# ---------------------------------------------------------------------

def load_existing_stage72():
    data = (
        supabase
        .table("stage72_final_submission_authorization_runs")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_lock_id", lock_id)
        .eq("stage71_run_id", stage71_run_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    ).data or []

    return data[0] if data else None


existing_stage72 = load_existing_stage72()


# ---------------------------------------------------------------------
# Gate UI
# ---------------------------------------------------------------------

st.divider()
st.subheader("Stage 71 → Stage 72 final authorization gate")

m1, m2, m3, m4 = st.columns(4)

m1.metric("Stage 71", normalize_text(stage71.get("readiness_outcome")))
m2.metric("Draft reference", application_reference or "—")
m3.metric("Deadline", str(deadline or "—")[:10])
m4.metric("Integrity", "VERIFIED" if stage72_base_gate == "READY" else "FAILED")

with st.expander("Stage 72 hard-gate checks", expanded=False):
    st.dataframe(
        checks,
        use_container_width=True,
        hide_index=True,
    )

if stage72_base_gate == "READY":
    st.success("Stage 72 base gate: READY")
else:
    st.error("Stage 72 base gate: BLOCKED")

st.write(f"**Bound application:** `{application_reference or '—'}`")
st.write(f"**Stage 71 run:** `{stage71_run_id}`")
st.write(f"**Stage 71 readiness SHA256:** `{stage71_readiness_sha}`")


# ---------------------------------------------------------------------
# Explicit Part A + final authorization confirmation
# ---------------------------------------------------------------------

if not existing_stage72:

    st.divider()
    st.subheader("Part A completion + explicit final submission authorization")

    st.warning(
        "Confirmă numai starea reală observată în Funding & Tenders Portal. "
        "Stage 72 NU apasă Submit."
    )

    portal_url = st.text_input(
        "Current official portal URL",
        value=current_portal_url,
        key="stage72_portal_url",
    )
    portal_url_ok = official_domain_ok(portal_url)

    if portal_url:
        if portal_url_ok:
            st.success("Current URL is on an accepted official EU domain.")
        else:
            st.error("Current URL must be HTTPS and on europa.eu / ec.europa.eu.")

    displayed_reference = st.text_input(
        "Draft reference currently visible in portal",
        value=application_reference,
        key="stage72_reference",
    )

    reference_matches = (
        normalize_text(displayed_reference) == application_reference
        and len(application_reference) >= 3
    )

    if displayed_reference:
        if reference_matches:
            st.success("Draft reference matches Stage 71.")
        else:
            st.error("Draft reference does not match Stage 71.")

    st.markdown("### Part A")

    part_a_general_info = st.checkbox(
        "General Info is completed and saved.",
        key="stage72_part_a_general_info",
    )

    part_a_declarations = st.checkbox(
        "Declarations are completed and saved.",
        key="stage72_part_a_declarations",
    )

    part_a_participants = st.checkbox(
        "Participants section is complete and correct.",
        key="stage72_part_a_participants",
    )

    part_a_work_packages = st.checkbox(
        "Work packages section is complete and correct.",
        key="stage72_part_a_work_packages",
    )

    part_a_budget = st.checkbox(
        "Budget calculation sheets are complete and consistent with the proposal.",
        key="stage72_part_a_budget",
    )

    part_a_depreciation = st.checkbox(
        "Depreciation costs section is resolved (completed or correctly not applicable).",
        key="stage72_part_a_depreciation",
    )

    part_a_lump_sum = st.checkbox(
        "Lump sum breakdown is complete and valid.",
        key="stage72_part_a_lump_sum",
    )

    part_a_person_months = st.checkbox(
        "Person months overview is complete and valid.",
        key="stage72_part_a_person_months",
    )

    part_a_ethics = st.checkbox(
        "Ethics section is completed and all required explanations/documents are provided.",
        key="stage72_part_a_ethics",
    )

    part_a_security = st.checkbox(
        "Security section is completed.",
        key="stage72_part_a_security",
    )

    part_a_no_blocking_errors = st.checkbox(
        "Funding & Tenders Portal shows no blocking validation errors for Part A.",
        key="stage72_part_a_no_blocking",
    )

    st.markdown("### Final portal state")

    part_b_attached = st.checkbox(
        "The uploaded Part B/package is still attached to this exact draft.",
        key="stage72_part_b_attached",
    )

    proposal_editable = st.checkbox(
        "The proposal is still editable and has NOT been finally submitted.",
        key="stage72_editable",
    )

    no_receipt = st.checkbox(
        "No final submission receipt has been issued.",
        key="stage72_no_receipt",
    )

    understand_no_submit = st.checkbox(
        "I understand Stage 72 only records authorization and does NOT press Submit.",
        key="stage72_understand_no_submit",
    )

    authorization_phrase = st.text_input(
        "Authorization phrase",
        placeholder="Type exactly: AUTHORIZE STAGE 72 FINAL SUBMISSION",
        key="stage72_phrase",
    )

    authorization_note = st.text_area(
        "Optional authorization note",
        placeholder="Optional non-sensitive note.",
        key="stage72_note",
    )

    part_a_complete = all([
        part_a_general_info,
        part_a_declarations,
        part_a_participants,
        part_a_work_packages,
        part_a_budget,
        part_a_depreciation,
        part_a_lump_sum,
        part_a_person_months,
        part_a_ethics,
        part_a_security,
        part_a_no_blocking_errors,
    ])

    all_confirmations = (
        stage72_base_gate == "READY"
        and portal_url_ok
        and reference_matches
        and part_a_complete
        and part_b_attached
        and proposal_editable
        and no_receipt
        and understand_no_submit
        and normalize_text(authorization_phrase)
        == "AUTHORIZE STAGE 72 FINAL SUBMISSION"
    )

    authorization_payload = {
        "authorization_version": "stage72-v1.0",
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,

        "stage71_run_id": stage71_run_id,
        "stage70_run_id": stage70_run_id,

        "application_reference": application_reference,
        "draft_title": draft_title or None,
        "current_portal_url": normalize_text(portal_url),
        "displayed_reference": normalize_text(displayed_reference),
        "reference_matches": reference_matches,

        "part_a_general_info_complete": bool(part_a_general_info),
        "part_a_declarations_complete": bool(part_a_declarations),
        "part_a_participants_complete": bool(part_a_participants),
        "part_a_work_packages_complete": bool(part_a_work_packages),
        "part_a_budget_complete": bool(part_a_budget),
        "part_a_depreciation_resolved": bool(part_a_depreciation),
        "part_a_lump_sum_complete": bool(part_a_lump_sum),
        "part_a_person_months_complete": bool(part_a_person_months),
        "part_a_ethics_complete": bool(part_a_ethics),
        "part_a_security_complete": bool(part_a_security),
        "part_a_no_blocking_errors": bool(part_a_no_blocking_errors),

        "part_b_attached": bool(part_b_attached),
        "proposal_editable": bool(proposal_editable),
        "no_final_receipt": bool(no_receipt),
        "understands_no_submit": bool(understand_no_submit),

        "authorization_note": normalize_text(authorization_note) or None,

        "external_submission_performed": False,
        "external_receipt_obtained": False,
    }

    authorization_evidence_sha256 = stable_sha256(authorization_payload)

    run_basis = {
        "stage": 72,
        "fingerprint_contract": "stage72-v1.0-final-submission-authorization",
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage71_run_id": stage71_run_id,
        "stage71_readiness_evidence_sha256": stage71_readiness_sha,
        "authorization_evidence_sha256": authorization_evidence_sha256,
        "stage72_base_gate": stage72_base_gate,
    }

    run_fingerprint = stable_sha256(run_basis)

    if st.button(
        "🔐 Authorize & persist Stage 72 final submission authorization",
        type="primary",
        use_container_width=True,
        key="stage72_authorize",
        disabled=not all_confirmations,
    ):
        try:
            payload = {
                "user_id": user_id,
                "project_id": project_id,
                "opportunity_lock_id": lock_id,

                "stage71_run_id": stage71_run_id,
                "stage70_run_id": stage70_run_id or None,

                "stage": 72,
                "authorization_version": "stage72-v1.0",

                "opportunity_identity": identity,
                "official_deadline": str(deadline or "")[:10] or None,

                "application_reference": application_reference,
                "draft_title": draft_title or None,
                "current_portal_url": normalize_text(portal_url),

                "run_status": "COMPLETED",
                "authorization_outcome": "FINAL_SUBMISSION_AUTHORIZED",

                "part_a_complete": True,
                "part_a_no_blocking_errors": True,
                "part_b_attached": True,
                "proposal_editable": True,

                "external_submission_performed": False,
                "external_receipt_obtained": False,

                "stage71_readiness_evidence_sha256": stage71_readiness_sha,
                "authorization_evidence_sha256": authorization_evidence_sha256,
                "run_fingerprint": run_fingerprint,

                "authorization_payload": authorization_payload,
                "run_payload": run_basis,

                "authorized_at": now_iso(),
                "completed_at": now_iso(),
                "created_at": now_iso(),
                "updated_at": now_iso(),
            }

            try:
                rpc_result = supabase.rpc(
                    "persist_stage72_final_submission_authorization",
                    {"p_payload": payload},
                ).execute()

                if not rpc_result.data:
                    (
                        supabase
                        .table("stage72_final_submission_authorization_runs")
                        .insert(payload)
                        .execute()
                    )

            except Exception:
                (
                    supabase
                    .table("stage72_final_submission_authorization_runs")
                    .insert(payload)
                    .execute()
                )

            st.success(
                "Stage 72 persisted — Outcome FINAL_SUBMISSION_AUTHORIZED."
            )
            st.rerun()

        except Exception as exc:
            st.error(
                "Stage 72 persistence failed. Rulează mai întâi SQL-ul Stage 72 în Supabase. "
                f"{type(exc).__name__}: {str(exc)[:1800]}"
            )


# ---------------------------------------------------------------------
# Outcome UI
# ---------------------------------------------------------------------

existing_stage72 = load_existing_stage72()

if existing_stage72:
    st.divider()
    st.subheader("Stage 72 outcome")

    st.success(
        f"Stage 72 este deja persistată. Run ID: {existing_stage72.get('id')} — "
        f"Outcome: {existing_stage72.get('authorization_outcome')}"
    )

    o1, o2, o3, o4 = st.columns(4)

    o1.metric(
        "Outcome",
        existing_stage72.get("authorization_outcome"),
    )

    o2.metric(
        "Part A complete?",
        "YES" if bool(existing_stage72.get("part_a_complete")) else "NO",
    )

    o3.metric(
        "Submitted?",
        "YES" if bool(existing_stage72.get("external_submission_performed")) else "NO",
    )

    o4.metric(
        "Receipt?",
        "YES" if bool(existing_stage72.get("external_receipt_obtained")) else "NO",
    )

    st.write(
        f"**Application reference:** `{existing_stage72.get('application_reference')}`"
    )

    st.write(
        f"**Stage 71 readiness SHA256:** "
        f"`{existing_stage72.get('stage71_readiness_evidence_sha256')}`"
    )

    st.write(
        f"**Authorization evidence SHA256:** "
        f"`{existing_stage72.get('authorization_evidence_sha256')}`"
    )

    st.write(
        f"**Run fingerprint:** `{existing_stage72.get('run_fingerprint')}`"
    )

    if normalize_text(
        existing_stage72.get("authorization_outcome")
    ).upper() == "FINAL_SUBMISSION_AUTHORIZED":
        st.success(
            "Stage 72 FINAL_SUBMISSION_AUTHORIZED. "
            "A future controlled execution stage may consume this authorization. "
            "No submission or receipt occurred at Stage 72."
        )


st.caption(
    "Invariantă Stage 72 v1.0: FINAL_SUBMISSION_AUTHORIZED is an authorization state only. "
    "It is not evidence of submission, signature, financial commitment or "
    "European Commission receipt."
)
