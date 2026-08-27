import os
import json
import hashlib
from datetime import datetime, timezone, date
from typing import Any

import requests
import streamlit as st
from supabase import create_client


# =====================================================================
# STAGE 55 v1.0 — OPEN CONFIRMATION RESOLUTION WORKFLOW
#
# Purpose:
#   Consume ONLY a persisted Stage 54 NEEDS_CONFIRMATION run for the same
#   user/project/ACTIVE opportunity lock and resolve its OPEN confirmation
#   queue without inventing missing facts.
#
# Resolution channels:
#   1) PERSISTED_EVIDENCE
#      AI may propose CONFIRMED_BY_EVIDENCE only when it cites one or more
#      valid source_ids from the persisted Stage 52 source_registry.
#
#   2) USER_CONFIRMATION
#      The authenticated user may explicitly confirm/reject/remove an item.
#      The user-entered value/note is persisted as provenance.
#
# Deterministic rules:
#   - AI cannot confirm without valid source_ids.
#   - Missing evidence remains OPEN.
#   - User confirmation requires a non-empty confirmation note/value.
#   - Stage 54 is treated as an immutable upstream snapshot; Stage 55 does
#     not rewrite Stage 54 rows.
#
# Final statuses:
#   NEEDS_CONFIRMATION
#       -> one or more OPEN items remain.
#
#   READY_FOR_SUBMISSION_PREP
#       -> every open item has been explicitly CONFIRMED and there are no
#          rejected/removed items requiring draft changes.
#
#   REQUIRES_DRAFT_UPDATE
#       -> all items are resolved, but at least one was REJECTED/REMOVED,
#          so the proposal must be patched before submission preparation.
#
#   BLOCKED
#       -> upstream integrity or binding failed.
#
# Persistence:
#   stage55_confirmation_resolution_runs
#   stage55_confirmation_resolution_items
# =====================================================================

st.set_page_config(
    page_title="Stage 55 v1.0 — Open Confirmation Resolution",
    page_icon="🧩",
    layout="wide",
)

st.title("🧩 Etapa 55 v1.0 — AI Open Confirmation Resolution Workflow")
st.caption(
    "Etapa 55 rezolvă explicit elementele TO_CONFIRM persistate în Stage 54. "
    "Nicio lipsă de informație nu este convertită automat în fapt confirmat."
)


# ---------------------------------------------------------------------
# Generic helpers
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


def future_deadline(value: Any) -> bool:
    if not value:
        return False
    try:
        return date.fromisoformat(str(value)[:10]) >= datetime.now(timezone.utc).date()
    except Exception:
        return False


def stable_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def extract_response_text(payload: dict) -> str:
    if not isinstance(payload, dict):
        return ""
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"].strip()

    texts = []
    for item in payload.get("output") or []:
        for content in item.get("content") or []:
            if content.get("type") in {"output_text", "text"}:
                value = content.get("text")
                if isinstance(value, str):
                    texts.append(value)
                elif isinstance(value, dict) and isinstance(value.get("value"), str):
                    texts.append(value["value"])
    return "\n".join(texts).strip()


def extract_json_object(text: str) -> dict:
    text = normalize_text(text)
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return {}

    try:
        parsed = json.loads(text[start:end + 1])
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


# ---------------------------------------------------------------------
# Auth / project / ACTIVE lock
# ---------------------------------------------------------------------

try:
    supabase = get_supabase()
except Exception as exc:
    st.error(f"Supabase initialization failed: {type(exc).__name__}: {exc}")
    st.stop()

restore_auth_session(supabase)
user_id = current_user_id(supabase)

if not user_id:
    st.error("Stage 55 BLOCKED: utilizatorul autentificat nu poate fi identificat.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)

if not projects:
    st.warning("Nu există proiecte.")
    st.stop()

project_map = {project_label(p): p for p in projects}
selected_project = st.selectbox(
    "Project",
    list(project_map.keys()),
    key="stage55_v1_project",
)

project = project_map[selected_project]
project_id = str(project["id"])

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
    st.error("Stage 55 BLOCKED: nu există opportunity lock ACTIVE.")
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
# Load latest Stage 54 NEEDS_CONFIRMATION
# ---------------------------------------------------------------------

stage54_candidates = rows(
    "stage54_final_readiness_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    100,
)

stage54 = next(
    (
        r for r in stage54_candidates
        if normalize_text(r.get("readiness_status")).upper() == "NEEDS_CONFIRMATION"
        and normalize_text(r.get("integrity_status")).upper() == "PASS"
    ),
    stage54_candidates[0] if stage54_candidates else None,
)

if stage54:
    stage54_run_id = str(stage54.get("id") or "")
    stage54_status = normalize_text(stage54.get("readiness_status")).upper()
    stage54_integrity = normalize_text(stage54.get("integrity_status")).upper()
    stage54_fingerprint = normalize_text(stage54.get("readiness_fingerprint"))
    stage53_run_id = str(stage54.get("stage53_run_id") or "")
    stage52_run_id = str(stage54.get("stage52_run_id") or "")
    stage51_run_id = str(stage54.get("stage51_run_id") or "")
else:
    stage54_run_id = ""
    stage54_status = "MISSING"
    stage54_integrity = "MISSING"
    stage54_fingerprint = ""
    stage53_run_id = ""
    stage52_run_id = ""
    stage51_run_id = ""

stage54_open_items = rows(
    "stage54_open_confirmation_items",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage54_run_id": stage54_run_id,
    },
    "claim_no",
    1000,
) if stage54_run_id else []


# ---------------------------------------------------------------------
# Load Stage 53 / Stage 52 source registry
# ---------------------------------------------------------------------

stage53_candidates = rows(
    "stage53_review_handoff_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    100,
) if stage53_run_id else []

stage53 = next(
    (r for r in stage53_candidates if str(r.get("id") or "") == stage53_run_id),
    None,
)

stage52_candidates = rows(
    "stage52_validation_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    100,
) if stage52_run_id else []

stage52 = next(
    (r for r in stage52_candidates if str(r.get("id") or "") == stage52_run_id),
    None,
)

source_registry = as_dict(stage52.get("source_registry")) if stage52 else {}


# ---------------------------------------------------------------------
# Recompute upstream fingerprints
# ---------------------------------------------------------------------

stage54_payload = as_dict(stage54.get("readiness_payload")) if stage54 else {}
recomputed_stage54_fingerprint = (
    stable_sha256(stage54_payload)
    if stage54_payload
    else ""
)

stage53_payload = as_dict(stage53.get("handoff_payload")) if stage53 else {}
stored_stage53_fingerprint = normalize_text(stage53.get("handoff_fingerprint")) if stage53 else ""
recomputed_stage53_fingerprint = (
    stable_sha256(stage53_payload)
    if stage53_payload
    else ""
)

stage52_payload = as_dict(stage52.get("run_payload")) if stage52 else {}
stored_stage52_fingerprint = normalize_text(stage52.get("run_fingerprint")) if stage52 else ""
recomputed_stage52_fingerprint = (
    stable_sha256(stage52_payload)
    if stage52_payload
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
    "Stage 54 persisted run exists",
    bool(stage54),
    stage54_run_id or "MISSING",
)

add_check(
    "Stage 54 integrity PASS",
    stage54_integrity == "PASS",
    stage54_integrity,
)

add_check(
    "Stage 54 NEEDS_CONFIRMATION",
    stage54_status == "NEEDS_CONFIRMATION",
    stage54_status,
)

add_check(
    "Stage 54 fingerprint stable",
    bool(stage54_fingerprint)
    and stage54_fingerprint == recomputed_stage54_fingerprint,
    (
        f"stored={stage54_fingerprint[:16]}..., "
        f"recomputed={recomputed_stage54_fingerprint[:16]}..."
    ),
)

add_check(
    "Stage 53 bound run exists",
    bool(stage53),
    stage53_run_id or "MISSING",
)

add_check(
    "Stage 53 fingerprint stable",
    bool(stored_stage53_fingerprint)
    and stored_stage53_fingerprint == recomputed_stage53_fingerprint,
    (
        f"stored={stored_stage53_fingerprint[:16]}..., "
        f"recomputed={recomputed_stage53_fingerprint[:16]}..."
    ),
)

add_check(
    "Stage 52 bound run exists",
    bool(stage52),
    stage52_run_id or "MISSING",
)

add_check(
    "Stage 52 fingerprint stable",
    bool(stored_stage52_fingerprint)
    and stored_stage52_fingerprint == recomputed_stage52_fingerprint,
    (
        f"stored={stored_stage52_fingerprint[:16]}..., "
        f"recomputed={recomputed_stage52_fingerprint[:16]}..."
    ),
)

add_check(
    "Open confirmation queue exists",
    len(stage54_open_items) > 0,
    f"open_items={len(stage54_open_items)}",
)

stage55_gate = "READY" if all(c["PASS"] for c in checks) else "BLOCKED"

if stage55_gate == "READY":
    gate_reason = "Stage 54 confirmation queue and upstream fingerprints are persisted and stable."
else:
    gate_reason = (
        "Stage 55 fail-closed gate failed: "
        + "; ".join(c["Check"] for c in checks if not c["PASS"])
    )


# ---------------------------------------------------------------------
# Stable Stage 55 run fingerprint
# ---------------------------------------------------------------------

queue_snapshot = [
    {
        "stage54_confirmation_item_id": str(i.get("id") or ""),
        "stage52_claim_audit_id": str(i.get("stage52_claim_audit_id") or ""),
        "section_key": normalize_text(i.get("section_key")),
        "claim_no": int(i.get("claim_no") or 0),
        "claim_text": normalize_text(i.get("claim_text")),
        "reason": normalize_text(i.get("reason")),
        "source_ids": as_list(i.get("source_ids")),
        "upstream_status": normalize_text(i.get("confirmation_status")).upper(),
    }
    for i in stage54_open_items
]

run_basis = {
    "stage": 55,
    "fingerprint_contract": "stage55-v1.0-stable",
    "user_id": user_id,
    "project_id": project_id,
    "opportunity_lock_id": lock_id,
    "opportunity_identity": identity,
    "official_deadline": str(deadline or "")[:10],

    "stage52_run_id": stage52_run_id,
    "stage53_run_id": stage53_run_id,
    "stage54_run_id": stage54_run_id,

    "stage52_run_fingerprint": stored_stage52_fingerprint,
    "stage53_handoff_fingerprint": stored_stage53_fingerprint,
    "stage54_readiness_fingerprint": stage54_fingerprint,

    "queue_snapshot": queue_snapshot,
    "source_registry_fingerprint": stable_sha256(source_registry),
    "stage55_gate": stage55_gate,
}

stage55_run_fingerprint = stable_sha256(run_basis)


# ---------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------

def load_existing_stage55():
    if not stage55_run_fingerprint:
        return None

    data = (
        supabase.table("stage55_confirmation_resolution_runs")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_lock_id", lock_id)
        .eq("stage54_run_id", stage54_run_id)
        .eq("run_fingerprint", stage55_run_fingerprint)
        .limit(1)
        .execute()
    ).data or []

    return data[0] if data else None


def initialize_stage55():
    if stage55_gate != "READY":
        raise RuntimeError("Stage 55 is BLOCKED.")

    existing = load_existing_stage55()
    if existing:
        return existing

    payload = {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,

        "stage52_run_id": stage52_run_id,
        "stage53_run_id": stage53_run_id,
        "stage54_run_id": stage54_run_id,

        "stage": 55,
        "resolver_version": "stage55-v1.0",

        "opportunity_identity": identity,
        "official_deadline": str(deadline or "")[:10] or None,

        "stage52_run_fingerprint": stored_stage52_fingerprint,
        "stage53_handoff_fingerprint": stored_stage53_fingerprint,
        "stage54_readiness_fingerprint": stage54_fingerprint,

        "run_status": "INITIALIZED",
        "resolution_outcome": "NEEDS_CONFIRMATION",

        "total_items": len(queue_snapshot),
        "resolved_items": 0,
        "open_items": len(queue_snapshot),

        "run_fingerprint": stage55_run_fingerprint,
        "source_registry": source_registry,
        "run_payload": run_basis,

        "initialized_at": now_iso(),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }

    data = (
        supabase.table("stage55_confirmation_resolution_runs")
        .insert(payload)
        .execute()
    ).data or []

    if not data:
        raise RuntimeError("Could not create Stage 55 resolution run.")

    saved = data[0]
    run_id = str(saved["id"])

    for item in queue_snapshot:
        item_payload = {
            "stage55_run_id": run_id,
            "stage54_run_id": stage54_run_id,
            "stage54_confirmation_item_id": item["stage54_confirmation_item_id"],
            "stage52_claim_audit_id": item["stage52_claim_audit_id"],

            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,

            "section_key": item["section_key"],
            "claim_no": item["claim_no"],
            "claim_text": item["claim_text"],
            "reason": item["reason"],
            "upstream_source_ids": item["source_ids"],

            "resolution_status": "OPEN",
            "resolution_basis": "NONE",
            "resolved_source_ids": [],
            "resolution_value": None,
            "resolution_note": None,

            "created_at": now_iso(),
            "updated_at": now_iso(),
        }

        supabase.table("stage55_confirmation_resolution_items").insert(item_payload).execute()

    return saved


def recompute_stage55_outcome(run_id: str):
    items = rows(
        "stage55_confirmation_resolution_items",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "stage55_run_id": run_id,
        },
        "claim_no",
        1000,
    )

    open_count = sum(
        1 for i in items
        if normalize_text(i.get("resolution_status")).upper() == "OPEN"
    )

    resolved_count = len(items) - open_count

    rejected_or_removed = any(
        normalize_text(i.get("resolution_status")).upper() in {"REJECTED", "REMOVED"}
        for i in items
    )

    if open_count > 0:
        outcome = "NEEDS_CONFIRMATION"
        run_status = "IN_PROGRESS"
    elif rejected_or_removed:
        outcome = "REQUIRES_DRAFT_UPDATE"
        run_status = "COMPLETED"
    else:
        outcome = "READY_FOR_SUBMISSION_PREP"
        run_status = "COMPLETED"

    outcome_basis = {
        "stage55_run_id": run_id,
        "stage54_run_id": stage54_run_id,
        "stage54_readiness_fingerprint": stage54_fingerprint,
        "items": [
            {
                "id": str(i.get("id") or ""),
                "claim_no": int(i.get("claim_no") or 0),
                "resolution_status": normalize_text(i.get("resolution_status")).upper(),
                "resolution_basis": normalize_text(i.get("resolution_basis")).upper(),
                "resolved_source_ids": as_list(i.get("resolved_source_ids")),
                "resolution_value": normalize_text(i.get("resolution_value")),
                "resolution_note": normalize_text(i.get("resolution_note")),
            }
            for i in items
        ],
        "resolution_outcome": outcome,
    }

    resolution_fingerprint = stable_sha256(outcome_basis)

    (
        supabase.table("stage55_confirmation_resolution_runs")
        .update({
            "run_status": run_status,
            "resolution_outcome": outcome,
            "resolved_items": resolved_count,
            "open_items": open_count,
            "resolution_fingerprint": resolution_fingerprint,
            "resolution_payload": outcome_basis,
            "completed_at": now_iso() if run_status == "COMPLETED" else None,
            "updated_at": now_iso(),
        })
        .eq("id", run_id)
        .eq("user_id", user_id)
        .execute()
    )

    return outcome, resolution_fingerprint


# ---------------------------------------------------------------------
# AI persisted-evidence resolver
# ---------------------------------------------------------------------

def resolve_against_persisted_evidence(item: dict) -> dict:
    api_key = secret("OPENAI_API_KEY")
    model = secret("OPENAI_MODEL")

    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing.")
    if not model:
        raise RuntimeError("OPENAI_MODEL is missing.")

    system_rules = (
        "You are an evidence resolver for a funding-application workflow. "
        "You may not invent facts. Evaluate ONLY the claim against the provided persisted source registry. "
        "Return strict JSON with resolution, source_ids, value, reason. "
        "resolution must be exactly EVIDENCE_CONFIRMED, EVIDENCE_REJECTED, or NEEDS_USER_CONFIRMATION. "
        "EVIDENCE_CONFIRMED requires direct support from at least one provided source_id. "
        "EVIDENCE_REJECTED requires direct contradictory evidence. "
        "If evidence is incomplete, ambiguous, general, or merely about eligibility rules rather than the applicant/project fact, "
        "return NEEDS_USER_CONFIRMATION."
    )

    prompt = {
        "claim": normalize_text(item.get("claim_text")),
        "reason": normalize_text(item.get("reason")),
        "source_registry": source_registry,
        "required_output": {
            "resolution": "EVIDENCE_CONFIRMED|EVIDENCE_REJECTED|NEEDS_USER_CONFIRMATION",
            "source_ids": ["valid source registry IDs only"],
            "value": "supported/contradicting value if explicit",
            "reason": "short evidence-based explanation",
        },
    }

    body = {
        "model": model,
        "input": [
            {"role": "system", "content": system_rules},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False, default=str)},
        ],
    }

    response = requests.post(
        "https://api.openai.com/v1/responses",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=120,
    )

    try:
        payload = response.json()
    except Exception:
        payload = {"raw_text": response.text}

    if response.status_code >= 400:
        raise RuntimeError(
            f"OpenAI API HTTP {response.status_code}: "
            + normalize_text(payload.get("error") or payload)[:1800]
        )

    result = extract_json_object(extract_response_text(payload))
    if not result:
        raise RuntimeError("Resolver model did not return valid JSON.")

    resolution = normalize_text(result.get("resolution")).upper()

    if resolution not in {
        "EVIDENCE_CONFIRMED",
        "EVIDENCE_REJECTED",
        "NEEDS_USER_CONFIRMATION",
    }:
        resolution = "NEEDS_USER_CONFIRMATION"

    source_ids = [
        sid for sid in as_list(result.get("source_ids"))
        if sid in source_registry
    ]

    # Deterministic fail-closed enforcement.
    if resolution in {"EVIDENCE_CONFIRMED", "EVIDENCE_REJECTED"} and not source_ids:
        resolution = "NEEDS_USER_CONFIRMATION"

    return {
        "resolution": resolution,
        "source_ids": source_ids,
        "value": normalize_text(result.get("value")),
        "reason": normalize_text(result.get("reason")),
        "model": model,
        "response_id": payload.get("id"),
        "response_payload": payload,
    }


def apply_ai_resolution(run_id: str, item: dict, result: dict):
    resolution = result["resolution"]

    if resolution == "EVIDENCE_CONFIRMED":
        status = "CONFIRMED"
        basis = "PERSISTED_EVIDENCE"
    elif resolution == "EVIDENCE_REJECTED":
        status = "REJECTED"
        basis = "PERSISTED_EVIDENCE"
    else:
        status = "OPEN"
        basis = "NONE"

    payload = {
        "resolution_status": status,
        "resolution_basis": basis,
        "resolved_source_ids": result.get("source_ids") or [],
        "resolution_value": result.get("value") or None,
        "resolution_note": result.get("reason") or None,
        "resolver_model": result.get("model"),
        "resolver_response_id": result.get("response_id"),
        "resolver_response_payload": result.get("response_payload") or {},
        "resolved_at": now_iso() if status != "OPEN" else None,
        "updated_at": now_iso(),
    }

    (
        supabase.table("stage55_confirmation_resolution_items")
        .update(payload)
        .eq("id", item["id"])
        .eq("user_id", user_id)
        .eq("stage55_run_id", run_id)
        .execute()
    )

    return status


def apply_user_resolution(
    run_id: str,
    item: dict,
    status: str,
    value: str,
    note: str,
):
    status = normalize_text(status).upper()
    value = normalize_text(value)
    note = normalize_text(note)

    if status not in {"CONFIRMED", "REJECTED", "REMOVED"}:
        raise RuntimeError("Invalid user resolution status.")

    if not value and not note:
        raise RuntimeError(
            "Pentru o rezoluție manuală trebuie introdusă o valoare sau o notă explicită."
        )

    payload = {
        "resolution_status": status,
        "resolution_basis": "USER_CONFIRMATION",
        "resolved_source_ids": [],
        "resolution_value": value or None,
        "resolution_note": note or None,
        "resolver_model": None,
        "resolver_response_id": None,
        "resolver_response_payload": {},
        "resolved_at": now_iso(),
        "updated_at": now_iso(),
    }

    (
        supabase.table("stage55_confirmation_resolution_items")
        .update(payload)
        .eq("id", item["id"])
        .eq("user_id", user_id)
        .eq("stage55_run_id", run_id)
        .execute()
    )


# ---------------------------------------------------------------------
# Current persisted Stage 55 state
# ---------------------------------------------------------------------

existing_stage55 = load_existing_stage55()

resolution_items = rows(
    "stage55_confirmation_resolution_items",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage55_run_id": str(existing_stage55.get("id") or ""),
    },
    "claim_no",
    1000,
) if existing_stage55 else []


# ---------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------

st.divider()
st.subheader("Stage 54 → Stage 55 confirmation binding")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Stage 54", stage54_status)
m2.metric("Open queue", len(stage54_open_items))
m3.metric("Source registry", len(source_registry))
m4.metric("Integrity", "VERIFIED" if stage55_gate == "READY" else "FAILED")

with st.expander("Stage 55 hard-gate checks", expanded=False):
    st.dataframe(
        checks,
        use_container_width=True,
        hide_index=True,
    )

st.divider()
st.subheader("Stage 55 resolution run")

if stage55_gate == "READY":
    st.success("Etapa 55: READY. Confirmation resolution poate începe.")
else:
    st.error("Etapa 55: BLOCKED.")

st.write(f"**Reason:** {gate_reason}")
st.code(stage55_run_fingerprint, language=None)

if existing_stage55:
    st.success(
        f"Stage 55 este inițializată. Run ID: {existing_stage55.get('id')} — "
        f"Outcome: {existing_stage55.get('resolution_outcome')}"
    )
else:
    st.info("Inițializează Stage 55 pentru a copia coada Stage 54 într-un workflow de rezoluție.")

if st.button(
    "🧩 Initialize Stage 55 confirmation resolution",
    type="primary",
    use_container_width=True,
    key="stage55_v1_initialize",
    disabled=(stage55_gate != "READY"),
):
    try:
        saved = initialize_stage55()
        st.session_state["stage55_run_id"] = str(saved.get("id"))
        st.success(f"Stage 55 initialized — run {saved.get('id')}")
        st.rerun()
    except Exception as exc:
        st.error(
            "Stage 55 initialization failed. Rulează mai întâi SQL-ul Stage 55 în Supabase. "
            f"{type(exc).__name__}: {str(exc)[:1800]}"
        )


if existing_stage55:
    st.divider()
    st.subheader("Confirmation resolution queue")

    resolution_items = rows(
        "stage55_confirmation_resolution_items",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "stage55_run_id": str(existing_stage55["id"]),
        },
        "claim_no",
        1000,
    )

    if resolution_items:
        st.dataframe(
            [
                {
                    "Section": i.get("section_key"),
                    "Claim #": i.get("claim_no"),
                    "Claim": i.get("claim_text"),
                    "Status": i.get("resolution_status"),
                    "Basis": i.get("resolution_basis"),
                }
                for i in resolution_items
            ],
            use_container_width=True,
            hide_index=True,
        )

        item_map = {
            f"#{i.get('claim_no')} — {normalize_text(i.get('claim_text'))[:100]}": i
            for i in resolution_items
        }

        selected_item_label = st.selectbox(
            "Confirmation item",
            list(item_map.keys()),
            key="stage55_item_select",
        )

        selected_item = item_map[selected_item_label]

        st.write(f"**Claim:** {selected_item.get('claim_text')}")
        st.write(f"**Reason:** {selected_item.get('reason') or '—'}")
        st.write(
            f"**Current resolution:** "
            f"{selected_item.get('resolution_status')} / {selected_item.get('resolution_basis')}"
        )

        with st.expander("Upstream source IDs", expanded=False):
            st.json(as_list(selected_item.get("upstream_source_ids")))

        ai_ready = bool(secret("OPENAI_API_KEY")) and bool(secret("OPENAI_MODEL"))

        if not ai_ready:
            st.warning(
                "OPENAI_API_KEY și OPENAI_MODEL trebuie configurate pentru analiza automată a evidenței."
            )

        if st.button(
            "🔎 Try resolution from persisted evidence",
            type="primary",
            use_container_width=True,
            key="stage55_ai_resolve",
            disabled=(not ai_ready),
        ):
            try:
                with st.spinner("Checking persisted evidence..."):
                    result = resolve_against_persisted_evidence(selected_item)
                    status = apply_ai_resolution(
                        str(existing_stage55["id"]),
                        selected_item,
                        result,
                    )
                    outcome, _ = recompute_stage55_outcome(
                        str(existing_stage55["id"])
                    )

                    st.success(
                        f"Evidence resolution: {status} — global outcome {outcome}"
                    )
                    st.rerun()
            except Exception as exc:
                st.error(
                    f"Evidence resolution failed: "
                    f"{type(exc).__name__}: {str(exc)[:1800]}"
                )

        st.markdown("### Explicit user resolution")

        user_status = st.selectbox(
            "Resolution",
            ["CONFIRMED", "REJECTED", "REMOVED"],
            key="stage55_user_status",
        )

        user_value = st.text_area(
            "Confirmed/corrected value",
            key="stage55_user_value",
            help=(
                "Introduce valoarea factuală confirmată sau corecția. "
                "Pentru REMOVED poate rămâne gol dacă nota explică eliminarea."
            ),
        )

        user_note = st.text_area(
            "Confirmation note / provenance",
            key="stage55_user_note",
            help=(
                "Explică de unde provine confirmarea: document, contract, date proprii, "
                "decizie de proiect etc."
            ),
        )

        if st.button(
            "👤 Save explicit user resolution",
            use_container_width=True,
            key="stage55_user_resolve",
        ):
            try:
                apply_user_resolution(
                    str(existing_stage55["id"]),
                    selected_item,
                    user_status,
                    user_value,
                    user_note,
                )

                outcome, _ = recompute_stage55_outcome(
                    str(existing_stage55["id"])
                )

                st.success(
                    f"User resolution persisted: {user_status} — global outcome {outcome}"
                )
                st.rerun()
            except Exception as exc:
                st.error(
                    f"User resolution failed: "
                    f"{type(exc).__name__}: {str(exc)[:1800]}"
                )

    # Refresh run state after potential reruns.
    latest_run = (
        supabase.table("stage55_confirmation_resolution_runs")
        .select("*")
        .eq("id", existing_stage55["id"])
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    ).data or [existing_stage55]

    latest_run = latest_run[0]

    st.divider()
    st.subheader("Stage 55 outcome")

    o1, o2, o3, o4 = st.columns(4)
    o1.metric("Outcome", latest_run.get("resolution_outcome"))
    o2.metric("Total", latest_run.get("total_items"))
    o3.metric("Resolved", latest_run.get("resolved_items"))
    o4.metric("Open", latest_run.get("open_items"))

    outcome = normalize_text(latest_run.get("resolution_outcome")).upper()

    if outcome == "READY_FOR_SUBMISSION_PREP":
        st.success(
            "Stage 55: READY_FOR_SUBMISSION_PREP. Toate elementele au fost confirmate explicit."
        )
    elif outcome == "REQUIRES_DRAFT_UPDATE":
        st.warning(
            "Stage 55: REQUIRES_DRAFT_UPDATE. Toate elementele sunt rezolvate, "
            "dar cel puțin unul a fost REJECTED/REMOVED și draftul trebuie corectat înainte de submission preparation."
        )
    else:
        st.warning(
            "Stage 55: NEEDS_CONFIRMATION. Mai există elemente OPEN."
        )

    if outcome in {"READY_FOR_SUBMISSION_PREP", "REQUIRES_DRAFT_UPDATE"}:
        st.success(
            "Stage 55 poate preda controlul unei viitoare Etape 56. "
            "Stage 56 trebuie să verifice stage55_run_id + stage54_run_id + lock_id + "
            "run_fingerprint + resolution_fingerprint și să aplice outcome-ul fără a inventa fapte."
        )

st.caption(
    "Invariantă Stage 55 v1.0: AI-ul nu poate confirma fără source_id valid din registrul persistat. "
    "Rezoluțiile manuale sunt tratate ca USER_CONFIRMATION și trebuie să conțină o valoare sau o notă explicită. "
    "Stage 54 rămâne snapshot upstream nemodificat."
)

# =====================================================================
# END STAGE 55 v1.0
# =====================================================================
