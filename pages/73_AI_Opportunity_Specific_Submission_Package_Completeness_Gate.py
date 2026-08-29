import os
import json
import hashlib
from datetime import datetime, timezone, date
from typing import Any
from urllib.parse import urlparse

import streamlit as st
from supabase import create_client


# =====================================================================
# STAGE 73 v1.0 — OPPORTUNITY-SPECIFIC SUBMISSION PACKAGE COMPLETENESS GATE
#
# Purpose:
#   Consume ONLY Stage 72 FINAL_SUBMISSION_AUTHORIZED and verify that the
#   exact application draft contains ALL mandatory submission components
#   required by the specific opportunity before any future controlled
#   submission-execution stage.
#
# Stage 73 DOES NOT:
#   - press Submit
#   - upload files
#   - edit the proposal
#   - sign declarations
#   - create financial commitments
#   - claim European Commission receipt
#   - collect credentials, MFA, cookies or tokens
#
# Core rule:
#   Part A + Part B + budget are NOT assumed to be sufficient for every call.
#   The operator must confirm the full requirement set visible for the exact
#   opportunity/draft. Unknown or unresolved mandatory requirements block Stage 73.
#
# Outcome:
#   OPPORTUNITY_PACKAGE_COMPLETE
#
# Handoff:
#   A future Stage 74 may consume ONLY OPPORTUNITY_PACKAGE_COMPLETE.
# =====================================================================


st.set_page_config(
    page_title="Stage 73 v1.0 — Opportunity Package Completeness",
    page_icon="📦",
    layout="wide",
)

st.title("📦 Etapa 73 v1.0 — AI Opportunity-Specific Submission Package Completeness Gate")

st.caption(
    "Verifică dosarul complet pentru apelul/topic-ul exact. "
    "Stage 73 NU apasă Submit și NU modifică portalul."
)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

OFFICIAL_HOST_SUFFIXES = ("ec.europa.eu", "europa.eu")

BASELINE_COMPONENT_KEYS = [
    "PART_A",
    "PART_B",
    "BUDGET",
    "DECLARATIONS",
    "PARTICIPANTS",
    "WORK_PACKAGES",
    "ETHICS",
    "SECURITY",
]


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


def as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


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


def normalize_component_name(name: str) -> str:
    return " ".join(normalize_text(name).split())


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
    st.error("Stage 73 BLOCKED: utilizatorul autentificat nu poate fi identificat.")
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
        key="stage73_project",
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
    st.error("Stage 73 BLOCKED: nu există opportunity lock ACTIVE.")
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
# Stage 72
# ---------------------------------------------------------------------

stage72_candidates = rows(
    "stage72_final_submission_authorization_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    100,
)

stage72 = next(
    (
        r for r in stage72_candidates
        if normalize_text(r.get("run_status")).upper() == "COMPLETED"
        and normalize_text(r.get("authorization_outcome")).upper()
        == "FINAL_SUBMISSION_AUTHORIZED"
        and bool(r.get("part_a_complete"))
        and bool(r.get("part_a_no_blocking_errors"))
        and bool(r.get("part_b_attached"))
        and bool(r.get("proposal_editable"))
        and not bool(r.get("external_submission_performed"))
        and not bool(r.get("external_receipt_obtained"))
    ),
    None,
)

if not stage72:
    st.error(
        "Stage 73 BLOCKED: nu există un Stage 72 COMPLETED cu "
        "FINAL_SUBMISSION_AUTHORIZED pentru acest proiect/lock."
    )
    st.stop()

stage72_run_id = str(stage72.get("id") or "")
stage71_run_id = str(stage72.get("stage71_run_id") or "")
application_reference = normalize_text(stage72.get("application_reference"))
draft_title = normalize_text(stage72.get("draft_title"))
current_portal_url = normalize_text(stage72.get("current_portal_url"))

stage72_authorization_sha = normalize_text(stage72.get("authorization_evidence_sha256"))
stage72_run_fingerprint = normalize_text(stage72.get("run_fingerprint"))

stage72_authorization_payload = as_dict(stage72.get("authorization_payload"))
stage72_run_payload = as_dict(stage72.get("run_payload"))

recomputed_stage72_authorization_sha = (
    stable_sha256(stage72_authorization_payload)
    if stage72_authorization_payload
    else ""
)

recomputed_stage72_run_fingerprint = (
    stable_sha256(stage72_run_payload)
    if stage72_run_payload
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
    "Stage 72 exists",
    bool(stage72),
    stage72_run_id or "MISSING",
)

add_check(
    "Stage 72 COMPLETED",
    normalize_text(stage72.get("run_status")).upper() == "COMPLETED",
    normalize_text(stage72.get("run_status")).upper(),
)

add_check(
    "Stage 72 FINAL_SUBMISSION_AUTHORIZED",
    normalize_text(stage72.get("authorization_outcome")).upper()
    == "FINAL_SUBMISSION_AUTHORIZED",
    normalize_text(stage72.get("authorization_outcome")).upper(),
)

add_check(
    "Stage 72 authorization SHA256 stable",
    bool(stage72_authorization_sha)
    and stage72_authorization_sha == recomputed_stage72_authorization_sha,
    (
        f"stored={stage72_authorization_sha[:16]}..., "
        f"recomputed={recomputed_stage72_authorization_sha[:16]}..."
    ),
)

add_check(
    "Stage 72 run fingerprint stable",
    bool(stage72_run_fingerprint)
    and stage72_run_fingerprint == recomputed_stage72_run_fingerprint,
    (
        f"stored={stage72_run_fingerprint[:16]}..., "
        f"recomputed={recomputed_stage72_run_fingerprint[:16]}..."
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

add_check(
    "Not already submitted",
    not bool(stage72.get("external_submission_performed")),
    f"external_submission_performed={stage72.get('external_submission_performed')}",
)

add_check(
    "No final receipt",
    not bool(stage72.get("external_receipt_obtained")),
    f"external_receipt_obtained={stage72.get('external_receipt_obtained')}",
)

stage73_base_gate = (
    "READY"
    if all(c["PASS"] for c in checks)
    else "BLOCKED"
)


# ---------------------------------------------------------------------
# Existing Stage 73
# ---------------------------------------------------------------------

def load_existing_stage73():
    data = (
        supabase
        .table("stage73_submission_package_completeness_runs")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_lock_id", lock_id)
        .eq("stage72_run_id", stage72_run_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    ).data or []

    return data[0] if data else None


existing_stage73 = load_existing_stage73()


# ---------------------------------------------------------------------
# Gate UI
# ---------------------------------------------------------------------

st.divider()
st.subheader("Stage 72 → Stage 73 opportunity-specific completeness gate")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Stage 72", normalize_text(stage72.get("authorization_outcome")))
m2.metric("Draft reference", application_reference or "—")
m3.metric("Deadline", str(deadline or "—")[:10])
m4.metric("Integrity", "VERIFIED" if stage73_base_gate == "READY" else "FAILED")

with st.expander("Stage 73 hard-gate checks", expanded=False):
    st.dataframe(checks, use_container_width=True, hide_index=True)

if stage73_base_gate == "READY":
    st.success("Stage 73 base gate: READY")
else:
    st.error("Stage 73 base gate: BLOCKED")

st.write(f"**Bound application:** `{application_reference or '—'}`")
st.write(f"**Stage 72 run:** `{stage72_run_id}`")


# ---------------------------------------------------------------------
# Opportunity-specific package completeness
# ---------------------------------------------------------------------

if not existing_stage73:

    st.divider()
    st.subheader("Exact opportunity submission-package requirements")

    st.warning(
        "Nu presupune că Part A + Part B + Budget sunt suficiente pentru orice apel. "
        "Confirmă lista completă de componente obligatorii pentru acest topic/draft. "
        "Orice cerință obligatorie necunoscută sau nerezolvată trebuie să blocheze Stage 73."
    )

    portal_url = st.text_input(
        "Current official portal URL",
        value=current_portal_url,
        key="stage73_portal_url",
    )
    portal_url_ok = official_domain_ok(portal_url)

    displayed_reference = st.text_input(
        "Draft reference currently visible in portal",
        value=application_reference,
        key="stage73_reference",
    )
    reference_matches = (
        normalize_text(displayed_reference) == application_reference
        and len(application_reference) >= 3
    )

    if portal_url_ok:
        st.success("Current URL is on an accepted official EU domain.")
    elif portal_url:
        st.error("Current URL must be HTTPS and on europa.eu / ec.europa.eu.")

    if reference_matches:
        st.success("Draft reference matches Stage 72.")
    elif displayed_reference:
        st.error("Draft reference does not match Stage 72.")

    st.markdown("### Baseline components")

    baseline = {}

    baseline["PART_A"] = st.checkbox(
        "Part A is complete and saved.",
        key="stage73_part_a",
    )

    baseline["PART_B"] = st.checkbox(
        "Part B is attached and valid for this exact draft.",
        key="stage73_part_b",
    )

    baseline["BUDGET"] = st.checkbox(
        "Budget / lump-sum financial data required by this call is complete and consistent.",
        key="stage73_budget",
    )

    baseline["DECLARATIONS"] = st.checkbox(
        "All required declarations are complete.",
        key="stage73_declarations",
    )

    baseline["PARTICIPANTS"] = st.checkbox(
        "All required participant/beneficiary/partner data is complete.",
        key="stage73_participants",
    )

    baseline["WORK_PACKAGES"] = st.checkbox(
        "All required work package data is complete.",
        key="stage73_work_packages",
    )

    baseline["ETHICS"] = st.checkbox(
        "Ethics requirements are complete, including any required explanations/documents.",
        key="stage73_ethics",
    )

    baseline["SECURITY"] = st.checkbox(
        "Security requirements are complete.",
        key="stage73_security",
    )

    st.markdown("### Additional opportunity-specific components")

    st.caption(
        "Adaugă aici orice anexă/formular/document obligatoriu suplimentar cerut de topic, "
        "de exemplu: ownership/control declaration, clinical studies annex, detailed budget annex, "
        "ethics document, security document, financial support annex, specific call template etc."
    )

    additional_required_raw = st.text_area(
        "Additional mandatory components — one per line",
        placeholder=(
            "Example:\n"
            "Ownership and Control Declaration\n"
            "Detailed Budget Annex\n"
            "Clinical Studies Annex"
        ),
        key="stage73_additional_required",
    )

    additional_required = [
        normalize_component_name(x)
        for x in additional_required_raw.splitlines()
        if normalize_component_name(x)
    ]

    additional_status = {}

    for idx, component in enumerate(additional_required):
        additional_status[component] = st.checkbox(
            f"{component} — present, complete and valid.",
            key=f"stage73_additional_{idx}",
        )

    no_other_mandatory_components = st.checkbox(
        "I have reviewed the exact portal/call requirements and there are NO other mandatory submission components not listed above.",
        key="stage73_no_unknown_components",
    )

    portal_validation_clean = st.checkbox(
        "The portal shows no blocking validation errors for the complete submission package.",
        key="stage73_portal_validation_clean",
    )

    proposal_editable = st.checkbox(
        "The proposal is still editable and has NOT been finally submitted.",
        key="stage73_editable",
    )

    no_receipt = st.checkbox(
        "No final submission receipt has been issued.",
        key="stage73_no_receipt",
    )

    understand_no_submit = st.checkbox(
        "I understand Stage 73 only validates package completeness and does NOT press Submit.",
        key="stage73_understand",
    )

    confirmation_phrase = st.text_input(
        "Confirmation phrase",
        placeholder="Type exactly: CONFIRM STAGE 73 COMPLETE PACKAGE",
        key="stage73_phrase",
    )

    note = st.text_area(
        "Optional package note",
        placeholder="Optional non-sensitive note about opportunity-specific requirements.",
        key="stage73_note",
    )

    baseline_complete = all(baseline.values())
    additional_complete = all(additional_status.values()) if additional_status else True

    package_complete = (
        baseline_complete
        and additional_complete
        and no_other_mandatory_components
        and portal_validation_clean
    )

    all_confirmations = (
        stage73_base_gate == "READY"
        and portal_url_ok
        and reference_matches
        and package_complete
        and proposal_editable
        and no_receipt
        and understand_no_submit
        and normalize_text(confirmation_phrase)
        == "CONFIRM STAGE 73 COMPLETE PACKAGE"
    )

    required_components = [
        {
            "component_key": key,
            "component_name": key.replace("_", " ").title(),
            "required": True,
            "complete": bool(value),
            "source": "BASELINE",
        }
        for key, value in baseline.items()
    ]

    required_components += [
        {
            "component_key": f"ADDITIONAL_{idx + 1}",
            "component_name": component,
            "required": True,
            "complete": bool(additional_status.get(component)),
            "source": "OPPORTUNITY_SPECIFIC",
        }
        for idx, component in enumerate(additional_required)
    ]

    package_payload = {
        "completeness_version": "stage73-v1.0",
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage72_run_id": stage72_run_id,
        "stage71_run_id": stage71_run_id,

        "application_reference": application_reference,
        "draft_title": draft_title or None,
        "current_portal_url": normalize_text(portal_url),
        "displayed_reference": normalize_text(displayed_reference),
        "reference_matches": reference_matches,

        "required_components": required_components,
        "baseline_complete": baseline_complete,
        "additional_complete": additional_complete,
        "no_unknown_mandatory_components": bool(no_other_mandatory_components),
        "portal_validation_clean": bool(portal_validation_clean),
        "proposal_editable": bool(proposal_editable),
        "no_final_receipt": bool(no_receipt),
        "understands_no_submit": bool(understand_no_submit),

        "package_note": normalize_text(note) or None,

        "external_submission_performed": False,
        "external_receipt_obtained": False,
    }

    package_manifest_sha256 = stable_sha256(required_components)
    completeness_evidence_sha256 = stable_sha256(package_payload)

    run_basis = {
        "stage": 73,
        "fingerprint_contract": "stage73-v1.0-opportunity-package-completeness",
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage72_run_id": stage72_run_id,
        "stage72_authorization_evidence_sha256": stage72_authorization_sha,
        "package_manifest_sha256": package_manifest_sha256,
        "completeness_evidence_sha256": completeness_evidence_sha256,
        "stage73_base_gate": stage73_base_gate,
    }

    run_fingerprint = stable_sha256(run_basis)

    if st.button(
        "📦 Confirm & persist Stage 73 complete opportunity package",
        type="primary",
        use_container_width=True,
        key="stage73_confirm",
        disabled=not all_confirmations,
    ):
        try:
            payload = {
                "user_id": user_id,
                "project_id": project_id,
                "opportunity_lock_id": lock_id,

                "stage72_run_id": stage72_run_id,
                "stage71_run_id": stage71_run_id or None,

                "stage": 73,
                "completeness_version": "stage73-v1.0",

                "opportunity_identity": identity,
                "official_deadline": str(deadline or "")[:10] or None,

                "application_reference": application_reference,
                "draft_title": draft_title or None,
                "current_portal_url": normalize_text(portal_url),

                "run_status": "COMPLETED",
                "completeness_outcome": "OPPORTUNITY_PACKAGE_COMPLETE",

                "baseline_complete": True,
                "additional_complete": True,
                "no_unknown_mandatory_components": True,
                "portal_validation_clean": True,
                "proposal_editable": True,

                "external_submission_performed": False,
                "external_receipt_obtained": False,

                "stage72_authorization_evidence_sha256": stage72_authorization_sha,
                "package_manifest_sha256": package_manifest_sha256,
                "completeness_evidence_sha256": completeness_evidence_sha256,
                "run_fingerprint": run_fingerprint,

                "required_components": required_components,
                "completeness_payload": package_payload,
                "run_payload": run_basis,

                "confirmed_at": now_iso(),
                "completed_at": now_iso(),
                "created_at": now_iso(),
                "updated_at": now_iso(),
            }

            try:
                rpc_result = supabase.rpc(
                    "persist_stage73_submission_package_completeness",
                    {"p_payload": payload},
                ).execute()

                if not rpc_result.data:
                    (
                        supabase
                        .table("stage73_submission_package_completeness_runs")
                        .insert(payload)
                        .execute()
                    )

            except Exception:
                (
                    supabase
                    .table("stage73_submission_package_completeness_runs")
                    .insert(payload)
                    .execute()
                )

            st.success(
                "Stage 73 persisted — Outcome OPPORTUNITY_PACKAGE_COMPLETE."
            )
            st.rerun()

        except Exception as exc:
            st.error(
                "Stage 73 persistence failed. Rulează mai întâi SQL-ul Stage 73 în Supabase. "
                f"{type(exc).__name__}: {str(exc)[:1800]}"
            )


# ---------------------------------------------------------------------
# Outcome UI
# ---------------------------------------------------------------------

existing_stage73 = load_existing_stage73()

if existing_stage73:
    st.divider()
    st.subheader("Stage 73 outcome")

    st.success(
        f"Stage 73 este deja persistată. Run ID: {existing_stage73.get('id')} — "
        f"Outcome: {existing_stage73.get('completeness_outcome')}"
    )

    o1, o2, o3, o4 = st.columns(4)

    o1.metric(
        "Outcome",
        existing_stage73.get("completeness_outcome"),
    )

    o2.metric(
        "Package complete?",
        "YES" if bool(existing_stage73.get("no_unknown_mandatory_components")) else "NO",
    )

    o3.metric(
        "Submitted?",
        "YES" if bool(existing_stage73.get("external_submission_performed")) else "NO",
    )

    o4.metric(
        "Receipt?",
        "YES" if bool(existing_stage73.get("external_receipt_obtained")) else "NO",
    )

    st.write(
        f"**Application reference:** `{existing_stage73.get('application_reference')}`"
    )

    st.write(
        f"**Package manifest SHA256:** `{existing_stage73.get('package_manifest_sha256')}`"
    )

    st.write(
        f"**Completeness evidence SHA256:** "
        f"`{existing_stage73.get('completeness_evidence_sha256')}`"
    )

    st.write(
        f"**Run fingerprint:** `{existing_stage73.get('run_fingerprint')}`"
    )

    with st.expander("Persisted required components", expanded=False):
        st.json(existing_stage73.get("required_components") or [])

    if normalize_text(
        existing_stage73.get("completeness_outcome")
    ).upper() == "OPPORTUNITY_PACKAGE_COMPLETE":
        st.success(
            "Stage 73 OPPORTUNITY_PACKAGE_COMPLETE. "
            "The exact opportunity package is recorded as complete. "
            "No submission or receipt occurred at Stage 73."
        )


st.caption(
    "Invariantă Stage 73 v1.0: OPPORTUNITY_PACKAGE_COMPLETE means the exact "
    "mandatory package set has been confirmed complete for this opportunity. "
    "It is not evidence of submission or European Commission receipt."
)
