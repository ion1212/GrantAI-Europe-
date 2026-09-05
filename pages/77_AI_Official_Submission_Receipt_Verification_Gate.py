
import io
import json
import hashlib
from datetime import datetime, timezone
from urllib.parse import urlparse

import streamlit as st
from supabase import create_client

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None


# =====================================================================
# STAGE 77 v1.0 — OFFICIAL SUBMISSION RECEIPT VERIFICATION GATE
#
# Consumes ONLY Stage 76 FINAL_SUBMISSION_EXECUTED_CONFIRMED.
# Captures a real EC portal receipt, verifies its PDF structure and binds
# its identifiers/hash to Stage 76. It never invents or downloads a receipt.
# =====================================================================

st.set_page_config(page_title="Stage 77 — Submission Evidence Verification", page_icon="🧾", layout="wide")
st.title("🧾 Etapa 77 v1.1 — AI Submission Evidence Verification Gate")
st.caption("Acceptă confirmarea vizibilă din portal acum și permite verificarea receipt-ului PDF ulterior.")


def secret(name, default=""):
    try:
        return str(st.secrets.get(name, default))
    except Exception:
        return os.getenv(name, default)


@st.cache_resource
def get_supabase():
    return create_client(secret("SUPABASE_URL"), secret("SUPABASE_KEY") or secret("SUPABASE_ANON_KEY"))


def norm(v):
    return str(v or "").strip()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def sha_json(value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def rows(table, filters=None, order="created_at", limit=100):
    q = supabase.table(table).select("*")
    for key, value in (filters or {}).items():
        if value not in (None, ""):
            q = q.eq(key, value)
    if order:
        q = q.order(order, desc=True)
    return q.limit(limit).execute().data or []


def restore_auth_session(sb):
    session = st.session_state.get("auth_session")
    if not session:
        return
    access = session.get("access_token") if isinstance(session, dict) else getattr(session, "access_token", None)
    refresh = session.get("refresh_token") if isinstance(session, dict) else getattr(session, "refresh_token", None)
    if access and refresh:
        try:
            sb.auth.set_session(access, refresh)
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
        if st.session_state.get(key):
            return str(st.session_state[key])
    try:
        user = sb.auth.get_user().user
        return str(user.id) if user and getattr(user, "id", None) else None
    except Exception:
        return None


def project_label(project):
    return f"{project.get('name') or 'Project'} — {str(project.get('id') or '')[:8]}"


def official_domain_ok(url):
    try:
        parsed = urlparse(norm(url))
        host = (parsed.hostname or "").lower().strip(".")
        return parsed.scheme.lower() == "https" and (host == "europa.eu" or host.endswith(".europa.eu"))
    except Exception:
        return False


def extract_pdf_text(raw):
    if PdfReader is None:
        return "", "pypdf is not installed"
    try:
        reader = PdfReader(io.BytesIO(raw))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
        return text, None
    except Exception as exc:
        return "", f"{type(exc).__name__}: {exc}"


try:
    supabase = get_supabase()
except Exception as exc:
    st.error(f"Supabase initialization failed: {type(exc).__name__}: {exc}")
    st.stop()

restore_auth_session(supabase)
user_id = current_user_id(supabase)
if not user_id:
    st.error("Stage 77 BLOCKED: user not identified.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)
if not projects:
    st.error("No projects.")
    st.stop()

project_map = {project_label(p): p for p in projects}
project = project_map[st.selectbox("Project", list(project_map.keys()), key="stage77_project")]
project_id = str(project["id"])

locks = rows("selected_opportunity_locks", {"user_id": user_id, "project_id": project_id, "lock_status": "ACTIVE"}, "created_at", 10)
if not locks:
    st.error("Stage 77 BLOCKED: no ACTIVE opportunity lock.")
    st.stop()

lock = locks[0]
lock_id = str(lock["id"])
identity = norm(lock.get("opportunity_identity"))

stage76_runs = rows(
    "stage76_final_submission_execution_confirmations",
    {"user_id": user_id, "project_id": project_id, "opportunity_lock_id": lock_id},
    "created_at",
    100,
)
stage76 = next((r for r in stage76_runs if norm(r.get("run_status")).upper() == "COMPLETED" and norm(r.get("execution_outcome")).upper() == "FINAL_SUBMISSION_EXECUTED_CONFIRMED" and bool(r.get("external_submission_performed")) and not bool(r.get("external_receipt_obtained"))), None)
if not stage76:
    st.error("Stage 77 BLOCKED: no valid Stage 76 FINAL_SUBMISSION_EXECUTED_CONFIRMED run.")
    st.stop()

stage76_run_id = str(stage76["id"])
application_reference = norm(stage76.get("application_reference"))
portal_url = norm(stage76.get("current_portal_url"))
stage76_fingerprint = norm(stage76.get("run_fingerprint"))
stage76_execution_sha = norm(stage76.get("execution_evidence_sha256"))

existing = rows("stage77_official_submission_receipt_verifications", {"stage76_run_id": stage76_run_id}, "created_at", 1)
existing = existing[0] if existing else None

st.subheader("Stage 76 → Stage 77 receipt binding")
c1, c2, c3 = st.columns(3)
c1.metric("Stage 76", stage76.get("execution_outcome"))
c2.metric("Application", application_reference)
c3.metric("Receipt", "VERIFIED" if existing else "PENDING")

if not existing:
    evidence_mode = st.radio(
        "Submission evidence available",
        ["Portal confirmation (no PDF yet)", "Official digitally signed receipt PDF"],
        key="stage77_evidence_mode",
    )
    provisional_mode = evidence_mode.startswith("Portal confirmation")

    if provisional_mode:
        st.info(
            "Folosește pagina oficială care arată Submitted, Final ID și data/ora. "
            "Rezultatul este provizoriu; receipt-ul poate fi verificat ulterior."
        )
        portal_image = st.file_uploader(
            "Optional portal confirmation screenshot (PNG/JPG)",
            type=["png", "jpg", "jpeg"],
            key="stage77_portal_image",
        )
        receipt = None
    else:
        st.info("Încarcă PDF-ul semnat digital descărcat din Funding & Tenders Portal.")
        receipt = st.file_uploader("Official EC submission receipt (PDF)", type=["pdf"], key="stage77_receipt")
        portal_image = None
    final_proposal_id = st.text_input("Final proposal ID shown by the portal", placeholder="Example: 101363053", key="stage77_final_id")
    submitted_at_text = st.text_input("Official submitted timestamp", placeholder="Example: 30 August 2026 00:05:46 Brussels Local Time", key="stage77_submitted_at")
    receipt_source_url = st.text_input("Official portal URL where the receipt was obtained", value=portal_url, key="stage77_url")

    raw = receipt.getvalue() if receipt else b""
    portal_image_raw = portal_image.getvalue() if portal_image else b""
    pdf_header_ok = raw.startswith(b"%PDF-")
    receipt_sha = sha_bytes(raw) if raw else ""
    extracted_text, extraction_error = extract_pdf_text(raw) if raw else ("", None)
    searchable = extracted_text.lower()
    reference_in_pdf = application_reference.lower() in searchable if searchable else False
    final_id_in_pdf = norm(final_proposal_id).lower() in searchable if searchable and norm(final_proposal_id) else False
    ec_marker = any(marker in searchable for marker in ("european commission", "funding & tenders", "submission receipt", "proposal")) if searchable else False

    common_checks = [
        ("Stage 76 completed", norm(stage76.get("run_status")).upper() == "COMPLETED"),
        ("Stage 76 fingerprint present", len(stage76_fingerprint) == 64),
        ("Stage 76 execution SHA present", len(stage76_execution_sha) == 64),
        ("Official portal URL", official_domain_ok(receipt_source_url)),
        ("Final proposal ID present", len(norm(final_proposal_id)) >= 5),
        ("Official submitted timestamp present", len(norm(submitted_at_text)) >= 8),
    ]
    pdf_checks = [
        ("Receipt is a PDF", pdf_header_ok),
        ("Application reference found in PDF text", reference_in_pdf),
        ("Final proposal ID found in PDF text", final_id_in_pdf),
        ("EC/portal marker found in PDF text", ec_marker),
    ]
    checks = common_checks if provisional_mode else common_checks + pdf_checks

    with st.expander("Receipt verification checks", expanded=True):
        st.dataframe([{"Check": name, "PASS": passed} for name, passed in checks], use_container_width=True, hide_index=True)
        if extraction_error:
            st.warning(f"PDF text extraction: {extraction_error}")
        if receipt_sha:
            st.code(receipt_sha, language=None)

    if provisional_mode:
        portal_submitted_confirmed = st.checkbox("I confirm the official portal shows this exact proposal as SUBMITTED.", key="stage77_portal_submitted")
        final_id_confirmed = st.checkbox("I confirm the Final ID and submitted timestamp above exactly match the portal.", key="stage77_final_id_confirmed")
        phrase_target = "RECORD STAGE 77 PROVISIONAL PORTAL EVIDENCE"
        phrase = st.text_input("Confirmation phrase", placeholder=f"Type exactly: {phrase_target}", key="stage77_phrase_provisional")
        ready = all(value for _, value in checks) and portal_submitted_confirmed and final_id_confirmed and norm(phrase) == phrase_target
    else:
        exact_receipt_confirmed = st.checkbox("I confirm this is the official digitally signed receipt for this exact proposal.", key="stage77_exact")
        no_manual_edit = st.checkbox("I confirm the receipt file was downloaded from the official portal and was not edited.", key="stage77_unedited")
        phrase_target = "VERIFY STAGE 77 OFFICIAL RECEIPT"
        phrase = st.text_input("Verification phrase", placeholder=f"Type exactly: {phrase_target}", key="stage77_phrase")
        ready = all(value for _, value in checks) and exact_receipt_confirmed and no_manual_edit and norm(phrase) == phrase_target

    button_label = "Record provisional portal evidence" if provisional_mode else "Persist verified official receipt"
    if st.button(f"🧾 {button_label}", type="primary", use_container_width=True, disabled=not ready, key="stage77_persist"):
        outcome = "PROVISIONAL_PORTAL_SUBMISSION_EVIDENCE_RECORDED" if provisional_mode else "OFFICIAL_SUBMISSION_RECEIPT_VERIFIED"
        source_name = portal_image.name if portal_image else (receipt.name if receipt else None)
        source_mime = portal_image.type if portal_image else ((receipt.type or "application/pdf") if receipt else None)
        source_raw = portal_image_raw if provisional_mode else raw
        evidence = {
            "verification_version": "stage77-v1.1",
            "evidence_mode": "PORTAL_CONFIRMATION" if provisional_mode else "OFFICIAL_RECEIPT_PDF",
            "stage76_run_id": stage76_run_id,
            "application_reference": application_reference,
            "final_proposal_id": norm(final_proposal_id),
            "official_submitted_at_text": norm(submitted_at_text),
            "receipt_source_url": norm(receipt_source_url),
            "evidence_file_name": source_name,
            "evidence_mime_type": source_mime,
            "evidence_size_bytes": len(source_raw),
            "evidence_file_sha256": sha_bytes(source_raw) if source_raw else None,
            "receipt_file_name": receipt.name if receipt else None,
            "receipt_mime_type": (receipt.type or "application/pdf") if receipt else None,
            "receipt_size_bytes": len(raw) if receipt else None,
            "receipt_sha256": receipt_sha or None,
            "stage76_execution_evidence_sha256": stage76_execution_sha,
            "stage76_run_fingerprint": stage76_fingerprint,
            "reference_in_pdf": reference_in_pdf,
            "final_id_in_pdf": final_id_in_pdf,
            "ec_marker_in_pdf": ec_marker,
        }
        evidence_sha = sha_json(evidence)
        run_basis = {"stage": 77, "contract": "stage77-v1.1-submission-evidence-verification", "stage76_run_id": stage76_run_id, "outcome": outcome, "evidence_file_sha256": evidence.get("evidence_file_sha256"), "verification_evidence_sha256": evidence_sha}
        run_fingerprint = sha_json(run_basis)
        payload = {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "stage76_run_id": stage76_run_id,
            "stage": 77,
            "verification_version": "stage77-v1.1",
            "evidence_mode": evidence["evidence_mode"],
            "opportunity_identity": identity,
            "application_reference": application_reference,
            "final_proposal_id": norm(final_proposal_id),
            "current_portal_url": norm(receipt_source_url),
            "run_status": "COMPLETED",
            "verification_outcome": outcome,
            "external_submission_performed": True,
            "external_receipt_obtained": not provisional_mode,
            "evidence_file_name": source_name,
            "evidence_mime_type": source_mime,
            "evidence_size_bytes": len(source_raw),
            "evidence_file_sha256": evidence.get("evidence_file_sha256"),
            "receipt_file_name": receipt.name if receipt else None,
            "receipt_mime_type": (receipt.type or "application/pdf") if receipt else None,
            "receipt_size_bytes": len(raw) if receipt else None,
            "receipt_sha256": receipt_sha or None,
            "stage76_execution_evidence_sha256": stage76_execution_sha,
            "stage76_run_fingerprint": stage76_fingerprint,
            "verification_evidence_sha256": evidence_sha,
            "run_fingerprint": run_fingerprint,
            "verification_payload": evidence,
            "run_payload": run_basis,
            "official_submitted_at_text": norm(submitted_at_text),
            "verified_at": now_iso(),
            "completed_at": now_iso(),
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        try:
            supabase.table("stage77_official_submission_receipt_verifications").insert(payload).execute()
            st.success(f"Stage 77 persisted — {outcome}.")
            st.rerun()
        except Exception as exc:
            st.error(f"Stage 77 persistence failed. Run Stage 77 SQL first. {type(exc).__name__}: {str(exc)[:1600]}")

existing_rows = rows("stage77_official_submission_receipt_verifications", {"stage76_run_id": stage76_run_id}, "created_at", 1)
existing = existing_rows[0] if existing_rows else None
if existing:
    st.divider()
    st.subheader("Stage 77 outcome")
    st.success(f"Run ID: {existing.get('id')} — Outcome: {existing.get('verification_outcome')}")
    a, b, c = st.columns(3)
    a.metric("Evidence", "OFFICIAL RECEIPT" if existing.get("external_receipt_obtained") else "PORTAL CONFIRMATION")
    b.metric("Final ID", existing.get("final_proposal_id"))
    c.metric("Size", f"{existing.get('receipt_size_bytes') or 0} bytes")
    st.write(f"**Receipt SHA256:** `{existing.get('receipt_sha256')}`")
    st.write(f"**Run fingerprint:** `{existing.get('run_fingerprint')}`")

st.caption("Invariant Stage 77 v1.1: portal confirmation is provisional. Only a real, unedited EC PDF may produce OFFICIAL_SUBMISSION_RECEIPT_VERIFIED.")
