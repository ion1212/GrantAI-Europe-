import os
import json
import hashlib
import re
from datetime import datetime, timezone, date
from typing import Any

import requests
import streamlit as st
from supabase import create_client


# =====================================================================
# STAGE 52 v1.0 — AI PROPOSAL DRAFT VALIDATION & EVIDENCE AUDIT
#
# Purpose:
#   Consume ONLY persisted Stage 51 DRAFTED items from the same user/project/
#   ACTIVE opportunity lock and independently audit their factual claims.
#
# Core invariants:
#   - Stage 52 never rewrites proposal content.
#   - Every factual claim must be classified as:
#       SUPPORTED / TO_CONFIRM / UNSUPPORTED / CONTRADICTED
#   - SUPPORTED requires an evidence reference that can be tied to persisted
#     Stage 51 context / Stage 46 verified evidence.
#   - Missing evidence cannot become SUPPORTED merely because the model says so.
#   - Section verdicts are fail-closed:
#       PASS / NEEDS_REVISION / BLOCKED
#   - Global PASS requires every drafted section to PASS.
#
# Persistence:
#   stage52_validation_runs
#   stage52_validation_items
#   stage52_claim_audits
# =====================================================================

st.set_page_config(
    page_title="Stage 52 v1.0 — Draft Validation & Evidence Audit",
    page_icon="🧪",
    layout="wide",
)

st.title("🧪 Etapa 52 v1.0 — AI Proposal Draft Validation & Evidence Audit")
st.caption(
    "Etapa 52 nu rescrie drafturile. Extrage afirmațiile verificabile, le compară cu "
    "evidența persistată și emite verdict fail-closed pe fiecare secțiune."
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


def bool_true(value: Any) -> bool:
    return value is True or normalize_text(value).lower() in {"true", "1", "yes"}


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

    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        return {}
    try:
        parsed = json.loads(m.group(0))
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
    st.error("Stage 52 BLOCKED: utilizatorul autentificat nu poate fi identificat.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)
if not projects:
    st.warning("Nu există proiecte.")
    st.stop()

project_map = {project_label(p): p for p in projects}
selected_project = st.selectbox("Project", list(project_map.keys()), key="stage52_v1_project")
project = project_map[selected_project]
project_id = str(project["id"])

locks = rows(
    "selected_opportunity_locks",
    {"user_id": user_id, "project_id": project_id, "lock_status": "ACTIVE"},
    "created_at",
    10,
)

if not locks:
    st.error("Stage 52 BLOCKED: nu există opportunity lock ACTIVE.")
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
# Load Stage 51 / Stage 50 / Stage 49
# ---------------------------------------------------------------------

stage51_runs = rows(
    "stage51_drafting_runs",
    {"user_id": user_id, "project_id": project_id, "opportunity_lock_id": lock_id},
    "created_at",
    100,
)
stage51 = stage51_runs[0] if stage51_runs else None

if stage51:
    stage51_run_id = str(stage51.get("id") or "")
    stage51_status = normalize_text(stage51.get("run_status")).upper()
    stage51_fingerprint = normalize_text(stage51.get("run_fingerprint"))
    stage50_run_id = str(stage51.get("stage50_run_id") or "")
    stage49_run_id = str(stage51.get("stage49_run_id") or "")
    stage46_run_id = str(stage51.get("stage46_run_id") or "")
    stage51_context = as_dict(stage51.get("context_payload"))
else:
    stage51_run_id = ""
    stage51_status = "MISSING"
    stage51_fingerprint = ""
    stage50_run_id = ""
    stage49_run_id = ""
    stage46_run_id = ""
    stage51_context = {}

stage51_items = rows(
    "stage51_drafting_items",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage51_run_id": stage51_run_id,
    },
    "created_at",
    500,
) if stage51_run_id else []

stage50_rows = rows(
    "stage50_proposal_build_runs",
    {"user_id": user_id, "project_id": project_id, "opportunity_lock_id": lock_id},
    "created_at",
    100,
) if stage50_run_id else []
stage50 = next((r for r in stage50_rows if str(r.get("id") or "") == stage50_run_id), None)

stage49_rows = rows(
    "stage49_application_authorization_runs",
    {"user_id": user_id, "project_id": project_id, "opportunity_lock_id": lock_id},
    "created_at",
    100,
) if stage49_run_id else []
stage49 = next((r for r in stage49_rows if str(r.get("id") or "") == stage49_run_id), None)


# ---------------------------------------------------------------------
# Recompute upstream fingerprints
# ---------------------------------------------------------------------

stage51_payload = as_dict(stage51.get("run_payload")) if stage51 else {}
recomputed_stage51_fingerprint = stable_sha256(stage51_payload) if stage51_payload else ""

stage50_payload = as_dict(stage50.get("build_payload")) if stage50 else {}
recomputed_stage50_fingerprint = stable_sha256(stage50_payload) if stage50_payload else ""
stored_stage50_fingerprint = normalize_text(stage50.get("build_fingerprint")) if stage50 else ""

stage49_payload = as_dict(stage49.get("authorization_payload")) if stage49 else {}
recomputed_stage49_fingerprint = stable_sha256(stage49_payload) if stage49_payload else ""
stored_stage49_fingerprint = normalize_text(stage49.get("authorization_fingerprint")) if stage49 else ""


# ---------------------------------------------------------------------
# Hard gate
# ---------------------------------------------------------------------

checks = []

def add_check(name: str, passed: bool, detail: str):
    checks.append({"Check": name, "PASS": bool(passed), "Detail": detail})


add_check("ACTIVE lock", normalize_text(lock.get("lock_status")).upper() == "ACTIVE",
          normalize_text(lock.get("lock_status")).upper())
add_check("Workflow allowed", workflow_allowed, f"workflow_allowed={workflow_allowed}")
add_check("Deadline valid", future_deadline(deadline), str(deadline or "")[:10])
add_check("Stage 51 run exists", bool(stage51), stage51_run_id or "MISSING")
add_check("Stage 51 initialized", stage51_status in {"INITIALIZED", "IN_PROGRESS", "DRAFTED", "COMPLETED"},
          stage51_status)
add_check("Stage 51 has drafted items",
          any(normalize_text(i.get("draft_status")).upper() == "DRAFTED" for i in stage51_items),
          f"drafted={sum(1 for i in stage51_items if normalize_text(i.get('draft_status')).upper() == 'DRAFTED')}")
add_check("Stage 51 fingerprint stable",
          bool(stage51_fingerprint) and stage51_fingerprint == recomputed_stage51_fingerprint,
          f"stored={stage51_fingerprint[:16]}..., recomputed={recomputed_stage51_fingerprint[:16]}...")
add_check("Stage 50 bound run exists", bool(stage50), stage50_run_id or "MISSING")
add_check("Stage 50 fingerprint stable",
          bool(stored_stage50_fingerprint) and stored_stage50_fingerprint == recomputed_stage50_fingerprint,
          f"stored={stored_stage50_fingerprint[:16]}..., recomputed={recomputed_stage50_fingerprint[:16]}...")
add_check("Stage 49 bound run exists", bool(stage49), stage49_run_id or "MISSING")
add_check("Stage 49 fingerprint stable",
          bool(stored_stage49_fingerprint) and stored_stage49_fingerprint == recomputed_stage49_fingerprint,
          f"stored={stored_stage49_fingerprint[:16]}..., recomputed={recomputed_stage49_fingerprint[:16]}...")
add_check("External submission still disabled",
          bool(stage49) and not bool_true(as_dict(stage49.get("authorization_scope")).get("external_submission")),
          "external_submission must remain false")

all_checks_pass = all(c["PASS"] for c in checks)
stage52_gate = "READY" if all_checks_pass else "BLOCKED"

if stage52_gate == "READY":
    gate_reason = "Stage 51 drafts and upstream fingerprints are persisted and stable."
else:
    gate_reason = "Stage 52 fail-closed gate failed: " + "; ".join(c["Check"] for c in checks if not c["PASS"])


# ---------------------------------------------------------------------
# Source registry from Stage 51 context
# ---------------------------------------------------------------------

verified_official = stage51_context.get("verified_official_evidence")
if not isinstance(verified_official, list):
    verified_official = []

source_registry = {
    f"OFFICIAL_{idx+1}": {
        "source_type": "OFFICIAL_EVIDENCE",
        "requirement": item.get("requirement"),
        "url": item.get("evidence_url"),
        "excerpt": item.get("evidence_excerpt"),
        "validation_reason": item.get("validation_reason"),
    }
    for idx, item in enumerate(verified_official)
}

source_registry["PROJECT_FACTS"] = {
    "source_type": "PROJECT_SNAPSHOT",
    "payload": stage51_context.get("project") or {},
}

source_registry["LOCKED_OPPORTUNITY"] = {
    "source_type": "LOCK",
    "payload": stage51_context.get("locked_opportunity") or {},
}


# ---------------------------------------------------------------------
# AI claim audit
# ---------------------------------------------------------------------

ALLOWED_LABELS = {"SUPPORTED", "TO_CONFIRM", "UNSUPPORTED", "CONTRADICTED"}

def audit_with_openai(section_title: str, draft_text: str) -> dict:
    api_key = secret("OPENAI_API_KEY")
    model = secret("OPENAI_MODEL")

    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing.")
    if not model:
        raise RuntimeError("OPENAI_MODEL is missing.")

    system_rules = (
        "You are an evidence auditor for a European funding proposal. "
        "Do not rewrite the proposal. Extract factual/verifiable claims only. "
        "For each claim choose exactly one label: SUPPORTED, TO_CONFIRM, UNSUPPORTED, CONTRADICTED. "
        "SUPPORTED is allowed only when at least one source_id from the provided registry directly supports it. "
        "TO_CONFIRM is for explicitly marked unknowns or claims requiring applicant confirmation. "
        "UNSUPPORTED is for factual claims not supported by any provided source. "
        "CONTRADICTED is for claims that conflict with a provided source. "
        "Return strict JSON only with keys section_verdict, claims, summary. "
        "section_verdict must be PASS, NEEDS_REVISION, or BLOCKED. "
        "PASS is allowed only when there are zero UNSUPPORTED and zero CONTRADICTED claims. "
        "BLOCKED is reserved for contradictions or structurally unauditable content."
    )

    user_payload = {
        "section_title": section_title,
        "draft_text": draft_text,
        "source_registry": source_registry,
        "required_schema": {
            "section_verdict": "PASS|NEEDS_REVISION|BLOCKED",
            "claims": [
                {
                    "claim_text": "string",
                    "label": "SUPPORTED|TO_CONFIRM|UNSUPPORTED|CONTRADICTED",
                    "source_ids": ["PROJECT_FACTS|LOCKED_OPPORTUNITY|OFFICIAL_1|..."],
                    "reason": "string"
                }
            ],
            "summary": "string"
        }
    }

    body = {
        "model": model,
        "input": [
            {"role": "system", "content": system_rules},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, default=str)},
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
        raise RuntimeError(f"OpenAI API HTTP {response.status_code}: {normalize_text(payload)[:1800]}")

    output_text = extract_response_text(payload)
    result = extract_json_object(output_text)
    if not result:
        raise RuntimeError("Audit model did not return valid JSON.")

    claims = result.get("claims")
    if not isinstance(claims, list):
        raise RuntimeError("Audit JSON has no claims list.")

    normalized_claims = []
    for claim in claims:
        if not isinstance(claim, dict):
            continue

        label = normalize_text(claim.get("label")).upper()
        if label not in ALLOWED_LABELS:
            label = "UNSUPPORTED"

        source_ids = claim.get("source_ids")
        if not isinstance(source_ids, list):
            source_ids = []

        valid_source_ids = [sid for sid in source_ids if sid in source_registry]

        # Deterministic post-validation:
        # SUPPORTED without a valid persisted source is downgraded.
        if label == "SUPPORTED" and not valid_source_ids:
            label = "UNSUPPORTED"

        normalized_claims.append({
            "claim_text": normalize_text(claim.get("claim_text")),
            "label": label,
            "source_ids": valid_source_ids,
            "reason": normalize_text(claim.get("reason")),
        })

    contradicted = sum(1 for c in normalized_claims if c["label"] == "CONTRADICTED")
    unsupported = sum(1 for c in normalized_claims if c["label"] == "UNSUPPORTED")

    if contradicted > 0:
        deterministic_verdict = "BLOCKED"
    elif unsupported > 0:
        deterministic_verdict = "NEEDS_REVISION"
    else:
        deterministic_verdict = "PASS"

    return {
        "section_verdict": deterministic_verdict,
        "claims": normalized_claims,
        "summary": normalize_text(result.get("summary")),
        "model": model,
        "response_id": payload.get("id"),
        "response_payload": payload,
        "raw_output": output_text,
    }


# ---------------------------------------------------------------------
# Run fingerprint
# ---------------------------------------------------------------------

draft_inventory = [
    {
        "id": str(i.get("id") or ""),
        "section_key": i.get("section_key"),
        "draft_sha256": i.get("draft_sha256"),
        "draft_status": i.get("draft_status"),
    }
    for i in stage51_items
    if normalize_text(i.get("draft_status")).upper() == "DRAFTED"
]

run_basis = {
    "stage": 52,
    "fingerprint_contract": "stage52-v1.0-stable",
    "user_id": user_id,
    "project_id": project_id,
    "opportunity_lock_id": lock_id,
    "opportunity_identity": identity,
    "official_deadline": str(deadline or "")[:10],
    "stage49_run_id": stage49_run_id,
    "stage50_run_id": stage50_run_id,
    "stage51_run_id": stage51_run_id,
    "stage49_authorization_fingerprint": stored_stage49_fingerprint,
    "stage50_build_fingerprint": stored_stage50_fingerprint,
    "stage51_run_fingerprint": stage51_fingerprint,
    "draft_inventory": draft_inventory,
    "source_registry_fingerprint": stable_sha256(source_registry),
    "stage52_gate": stage52_gate,
}

stage52_run_fingerprint = stable_sha256(run_basis)


# ---------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------

def load_existing_stage52_run():
    if not stage52_run_fingerprint:
        return None
    data = (
        supabase.table("stage52_validation_runs")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_lock_id", lock_id)
        .eq("stage51_run_id", stage51_run_id)
        .eq("run_fingerprint", stage52_run_fingerprint)
        .limit(1)
        .execute()
    ).data or []
    return data[0] if data else None


def initialize_stage52_run():
    if stage52_gate != "READY":
        raise RuntimeError("Stage 52 is BLOCKED.")

    existing = load_existing_stage52_run()
    if existing:
        return existing

    payload = {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage49_run_id": stage49_run_id,
        "stage50_run_id": stage50_run_id,
        "stage51_run_id": stage51_run_id,
        "stage": 52,
        "validator_version": "stage52-v1.0",
        "opportunity_identity": identity,
        "official_deadline": str(deadline or "")[:10] or None,
        "stage49_authorization_fingerprint": stored_stage49_fingerprint,
        "stage50_build_fingerprint": stored_stage50_fingerprint,
        "stage51_run_fingerprint": stage51_fingerprint,
        "run_status": "INITIALIZED",
        "global_verdict": "PENDING",
        "run_fingerprint": stage52_run_fingerprint,
        "source_registry": source_registry,
        "run_payload": run_basis,
        "initialized_at": now_iso(),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }

    data = supabase.table("stage52_validation_runs").insert(payload).execute().data or []
    if not data:
        raise RuntimeError("Could not create Stage 52 validation run.")
    return data[0]


def persist_section_audit(run_id: str, draft_item: dict, audit: dict):
    section_key = normalize_text(draft_item.get("section_key"))
    draft_sha = normalize_text(draft_item.get("draft_sha256"))
    claims = audit["claims"]

    counts = {
        label: sum(1 for c in claims if c["label"] == label)
        for label in sorted(ALLOWED_LABELS)
    }

    audit_basis = {
        "stage52_run_id": run_id,
        "stage51_draft_item_id": str(draft_item.get("id") or ""),
        "section_key": section_key,
        "draft_sha256": draft_sha,
        "section_verdict": audit["section_verdict"],
        "claims": claims,
        "source_registry_fingerprint": stable_sha256(source_registry),
    }
    audit_sha = stable_sha256(audit_basis)

    item_payload = {
        "stage52_run_id": run_id,
        "stage51_run_id": stage51_run_id,
        "stage51_draft_item_id": draft_item.get("id"),
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "section_key": section_key,
        "section_title": draft_item.get("section_title"),
        "draft_sha256": draft_sha,
        "section_verdict": audit["section_verdict"],
        "supported_count": counts.get("SUPPORTED", 0),
        "to_confirm_count": counts.get("TO_CONFIRM", 0),
        "unsupported_count": counts.get("UNSUPPORTED", 0),
        "contradicted_count": counts.get("CONTRADICTED", 0),
        "audit_summary": audit.get("summary") or "",
        "audit_sha256": audit_sha,
        "model_name": audit.get("model"),
        "response_id": audit.get("response_id"),
        "response_payload": audit.get("response_payload") or {},
        "updated_at": now_iso(),
    }

    existing = (
        supabase.table("stage52_validation_items")
        .select("id")
        .eq("user_id", user_id)
        .eq("stage52_run_id", run_id)
        .eq("section_key", section_key)
        .limit(1)
        .execute()
    ).data or []

    if existing:
        data = (
            supabase.table("stage52_validation_items")
            .update(item_payload)
            .eq("id", existing[0]["id"])
            .eq("user_id", user_id)
            .execute()
        ).data or []
        item_row = data[0] if data else {**item_payload, "id": existing[0]["id"]}
        validation_item_id = existing[0]["id"]

        # Replace claims deterministically for this validation item.
        (
            supabase.table("stage52_claim_audits")
            .delete()
            .eq("user_id", user_id)
            .eq("stage52_validation_item_id", validation_item_id)
            .execute()
        )
    else:
        item_payload["created_at"] = now_iso()
        data = supabase.table("stage52_validation_items").insert(item_payload).execute().data or []
        if not data:
            raise RuntimeError(f"Could not persist validation item {section_key}.")
        item_row = data[0]
        validation_item_id = item_row["id"]

    for idx, claim in enumerate(claims, 1):
        claim_payload = {
            "stage52_validation_item_id": validation_item_id,
            "stage52_run_id": run_id,
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "section_key": section_key,
            "claim_no": idx,
            "claim_text": claim["claim_text"],
            "claim_label": claim["label"],
            "source_ids": claim["source_ids"],
            "reason": claim["reason"],
            "claim_sha256": stable_sha256(claim),
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        supabase.table("stage52_claim_audits").insert(claim_payload).execute()

    return item_row


def recompute_global_verdict(run_id: str):
    items = rows(
        "stage52_validation_items",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "stage52_run_id": run_id,
        },
        "created_at",
        500,
    )

    drafted_sections = {
        normalize_text(i.get("section_key"))
        for i in stage51_items
        if normalize_text(i.get("draft_status")).upper() == "DRAFTED"
    }
    audited_sections = {normalize_text(i.get("section_key")) for i in items}

    if drafted_sections != audited_sections:
        global_verdict = "NEEDS_REVISION"
        run_status = "IN_PROGRESS"
    elif any(normalize_text(i.get("section_verdict")).upper() == "BLOCKED" for i in items):
        global_verdict = "BLOCKED"
        run_status = "COMPLETED"
    elif any(normalize_text(i.get("section_verdict")).upper() == "NEEDS_REVISION" for i in items):
        global_verdict = "NEEDS_REVISION"
        run_status = "COMPLETED"
    elif items and all(normalize_text(i.get("section_verdict")).upper() == "PASS" for i in items):
        global_verdict = "PASS"
        run_status = "COMPLETED"
    else:
        global_verdict = "NEEDS_REVISION"
        run_status = "IN_PROGRESS"

    (
        supabase.table("stage52_validation_runs")
        .update({
            "run_status": run_status,
            "global_verdict": global_verdict,
            "completed_at": now_iso() if run_status == "COMPLETED" else None,
            "updated_at": now_iso(),
        })
        .eq("id", run_id)
        .eq("user_id", user_id)
        .execute()
    )

    return global_verdict


existing_run = load_existing_stage52_run()
validation_items = []
if existing_run:
    validation_items = rows(
        "stage52_validation_items",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "stage52_run_id": existing_run.get("id"),
        },
        "created_at",
        500,
    )


# ---------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------

st.divider()
st.subheader("Stage 51 → Stage 52 validation binding")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Stage 51", stage51_status)
m2.metric("Drafted items", sum(1 for i in stage51_items if normalize_text(i.get("draft_status")).upper() == "DRAFTED"))
m3.metric("Official evidence", len(verified_official))
m4.metric(
    "Fingerprints",
    "VERIFIED"
    if (
        stage51_fingerprint == recomputed_stage51_fingerprint
        and stored_stage50_fingerprint == recomputed_stage50_fingerprint
        and stored_stage49_fingerprint == recomputed_stage49_fingerprint
        and bool(stage51_fingerprint)
        and bool(stored_stage50_fingerprint)
        and bool(stored_stage49_fingerprint)
    )
    else "FAILED",
)

with st.expander("Stage 52 hard-gate checks", expanded=False):
    st.dataframe(checks, use_container_width=True, hide_index=True)

st.divider()
st.subheader("Final Stage 52 Gate")

g1, g2, g3 = st.columns(3)
g1.metric("Gate", stage52_gate)
g2.metric("Checks passed", f"{sum(1 for c in checks if c['PASS'])}/{len(checks)}")
g3.metric("Source registry", len(source_registry))

if stage52_gate == "READY":
    st.success("Etapa 52: READY. Draft validation poate începe.")
else:
    st.error("Etapa 52: BLOCKED.")

st.write(f"**Reason:** {gate_reason}")
st.code(stage52_run_fingerprint, language=None)

st.divider()
st.subheader("Stage 52 run")

if existing_run:
    st.success(
        f"Stage 52 este inițializată. Run ID: {existing_run.get('id')} — "
        f"Global verdict: {existing_run.get('global_verdict')}"
    )
else:
    st.info("Inițializează Stage 52 înainte de auditarea secțiunilor.")

if st.button(
    "🧪 Initialize Stage 52 validation run",
    type="primary",
    use_container_width=True,
    key="stage52_v1_initialize",
    disabled=(stage52_gate != "READY"),
):
    try:
        saved = initialize_stage52_run()
        st.session_state["stage52_run_id"] = str(saved.get("id"))
        st.success(f"Stage 52 initialized — run {saved.get('id')}")
        st.rerun()
    except Exception as exc:
        st.error(
            "Stage 52 initialization failed. Rulează mai întâi SQL-ul Stage 52 în Supabase. "
            f"{type(exc).__name__}: {str(exc)[:1600]}"
        )

if existing_run:
    st.divider()
    st.subheader("Section evidence audit")

    drafted = [
        i for i in stage51_items
        if normalize_text(i.get("draft_status")).upper() == "DRAFTED"
    ]

    if not drafted:
        st.warning("Nu există secțiuni DRAFTED în Stage 51.")
    else:
        draft_map = {
            f"{i.get('section_title')} — {normalize_text(i.get('draft_sha256'))[:10]}": i
            for i in drafted
        }

        selected = st.selectbox(
            "Draft to audit",
            list(draft_map.keys()),
            key="stage52_draft_select",
        )
        draft_item = draft_map[selected]

        with st.expander("Draft under audit", expanded=False):
            st.write(draft_item.get("draft_text") or "")

        ai_ready = bool(secret("OPENAI_API_KEY")) and bool(secret("OPENAI_MODEL"))
        if not ai_ready:
            st.warning("OPENAI_API_KEY și OPENAI_MODEL trebuie configurate în Streamlit Secrets.")

        if st.button(
            "🔎 Audit factual claims against persisted evidence",
            type="primary",
            use_container_width=True,
            key="stage52_audit",
            disabled=(not ai_ready),
        ):
            try:
                with st.spinner("Auditing factual claims..."):
                    audit = audit_with_openai(
                        normalize_text(draft_item.get("section_title")),
                        normalize_text(draft_item.get("draft_text")),
                    )
                    saved_item = persist_section_audit(
                        str(existing_run["id"]),
                        draft_item,
                        audit,
                    )
                    global_verdict = recompute_global_verdict(str(existing_run["id"]))

                    st.success(
                        f"Audit persisted: {saved_item.get('section_verdict')} — "
                        f"global {global_verdict}"
                    )
                    st.rerun()
            except Exception as exc:
                st.error(f"Audit failed: {type(exc).__name__}: {str(exc)[:1800]}")

    if validation_items:
        st.divider()
        st.subheader("Persisted Stage 52 section verdicts")

        st.dataframe(
            [
                {
                    "Section": i.get("section_title"),
                    "Verdict": i.get("section_verdict"),
                    "Supported": i.get("supported_count"),
                    "TO_CONFIRM": i.get("to_confirm_count"),
                    "Unsupported": i.get("unsupported_count"),
                    "Contradicted": i.get("contradicted_count"),
                    "Audit SHA256": normalize_text(i.get("audit_sha256"))[:18] + "...",
                }
                for i in validation_items
            ],
            use_container_width=True,
            hide_index=True,
        )

        current = rows(
            "stage52_validation_runs",
            {"user_id": user_id},
            "updated_at",
            200,
        )
        current = next(
            (r for r in current if str(r.get("id") or "") == str(existing_run.get("id"))),
            existing_run,
        )
        global_verdict = normalize_text(current.get("global_verdict")).upper()

        if global_verdict == "PASS":
            st.success(
                "Stage 52 global verdict: PASS. O viitoare Stage 53 poate face final proposal consistency/review handoff."
            )
        elif global_verdict == "BLOCKED":
            st.error(
                "Stage 52 global verdict: BLOCKED. Există contradicții care trebuie rezolvate înainte de downstream."
            )
        else:
            st.warning(
                f"Stage 52 global verdict: {global_verdict or 'PENDING'}. "
                "Secțiunile cu UNSUPPORTED claims trebuie revizuite în Stage 51 și re-auditate."
            )

st.caption(
    "Invariantă Stage 52 v1.0: SUPPORTED fără source_id valid este retrogradat automat la UNSUPPORTED. "
    "PASS global necesită audit PASS pentru fiecare draft persistat."
)

# =====================================================================
# END STAGE 52 v1.0
# =====================================================================
