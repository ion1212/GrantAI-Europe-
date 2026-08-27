import os
import json
import hashlib
from datetime import datetime, timezone, date
from typing import Any

import streamlit as st
from supabase import create_client


# =====================================================================
# STAGE 53 v1.0 — FINAL PROPOSAL CONSISTENCY & REVIEW HANDOFF
#
# Purpose:
#   Consume ONLY a persisted Stage 52 COMPLETED/PASS run from the same
#   user/project/ACTIVE opportunity lock and produce a deterministic,
#   immutable final-review handoff.
#
# Core invariants:
#   - Stage 53 does NOT rewrite proposal content.
#   - Stage 52 must be COMPLETED + PASS.
#   - Every Stage 51 DRAFTED section must have exactly one Stage 52 PASS audit.
#   - Draft SHA256 values must still match between Stage 51 and Stage 52.
#   - Stage 52 claim inventory must contain zero UNSUPPORTED/CONTRADICTED.
#   - TO_CONFIRM is preserved explicitly as an open review item.
#   - Upstream Stage 49/50/51 fingerprints are re-verified.
#   - Stage 52 run fingerprint is recomputed from its persisted run_payload.
#   - No external submission, portal login, signature, or financial commitment.
#
# Persistence:
#   stage53_review_handoff_runs
#   stage53_review_handoff_items
# =====================================================================

st.set_page_config(
    page_title="Stage 53 v1.0 — Final Proposal Consistency & Review Handoff",
    page_icon="🧭",
    layout="wide",
)

st.title("🧭 Etapa 53 v1.0 — Final Proposal Consistency & Review Handoff")
st.caption(
    "Etapa 53 nu rescrie propunerea. Verifică integritatea Stage 51 → 52, "
    "consolidează secțiunile auditate și persistă un handoff determinist pentru review final."
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
    st.error("Stage 53 BLOCKED: utilizatorul autentificat nu poate fi identificat.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)
if not projects:
    st.warning("Nu există proiecte.")
    st.stop()

project_map = {project_label(p): p for p in projects}
selected_project = st.selectbox("Project", list(project_map.keys()), key="stage53_v1_project")
project = project_map[selected_project]
project_id = str(project["id"])

locks = rows(
    "selected_opportunity_locks",
    {"user_id": user_id, "project_id": project_id, "lock_status": "ACTIVE"},
    "created_at",
    10,
)
if not locks:
    st.error("Stage 53 BLOCKED: nu există opportunity lock ACTIVE.")
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
# Load Stage 52 and exact bound upstream runs
# ---------------------------------------------------------------------

stage52_candidates = rows(
    "stage52_validation_runs",
    {"user_id": user_id, "project_id": project_id, "opportunity_lock_id": lock_id},
    "created_at",
    100,
)
stage52 = next(
    (
        r for r in stage52_candidates
        if normalize_text(r.get("run_status")).upper() == "COMPLETED"
        and normalize_text(r.get("global_verdict")).upper() == "PASS"
    ),
    stage52_candidates[0] if stage52_candidates else None,
)

if stage52:
    stage52_run_id = str(stage52.get("id") or "")
    stage52_status = normalize_text(stage52.get("run_status")).upper()
    stage52_verdict = normalize_text(stage52.get("global_verdict")).upper()
    stage52_fingerprint = normalize_text(stage52.get("run_fingerprint"))
    stage51_run_id = str(stage52.get("stage51_run_id") or "")
    stage50_run_id = str(stage52.get("stage50_run_id") or "")
    stage49_run_id = str(stage52.get("stage49_run_id") or "")
else:
    stage52_run_id = ""
    stage52_status = "MISSING"
    stage52_verdict = "MISSING"
    stage52_fingerprint = ""
    stage51_run_id = ""
    stage50_run_id = ""
    stage49_run_id = ""

stage52_items = rows(
    "stage52_validation_items",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage52_run_id": stage52_run_id,
    },
    "created_at",
    500,
) if stage52_run_id else []

stage52_claims = rows(
    "stage52_claim_audits",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage52_run_id": stage52_run_id,
    },
    "created_at",
    5000,
) if stage52_run_id else []

stage51_candidates = rows(
    "stage51_drafting_runs",
    {"user_id": user_id, "project_id": project_id, "opportunity_lock_id": lock_id},
    "created_at",
    100,
) if stage51_run_id else []
stage51 = next((r for r in stage51_candidates if str(r.get("id") or "") == stage51_run_id), None)

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

stage50_candidates = rows(
    "stage50_proposal_build_runs",
    {"user_id": user_id, "project_id": project_id, "opportunity_lock_id": lock_id},
    "created_at",
    100,
) if stage50_run_id else []
stage50 = next((r for r in stage50_candidates if str(r.get("id") or "") == stage50_run_id), None)

stage49_candidates = rows(
    "stage49_application_authorization_runs",
    {"user_id": user_id, "project_id": project_id, "opportunity_lock_id": lock_id},
    "created_at",
    100,
) if stage49_run_id else []
stage49 = next((r for r in stage49_candidates if str(r.get("id") or "") == stage49_run_id), None)


# ---------------------------------------------------------------------
# Recompute fingerprints and inventories
# ---------------------------------------------------------------------

stage52_payload = as_dict(stage52.get("run_payload")) if stage52 else {}
recomputed_stage52_fingerprint = stable_sha256(stage52_payload) if stage52_payload else ""

stage51_payload = as_dict(stage51.get("run_payload")) if stage51 else {}
stored_stage51_fingerprint = normalize_text(stage51.get("run_fingerprint")) if stage51 else ""
recomputed_stage51_fingerprint = stable_sha256(stage51_payload) if stage51_payload else ""

stage50_payload = as_dict(stage50.get("build_payload")) if stage50 else {}
stored_stage50_fingerprint = normalize_text(stage50.get("build_fingerprint")) if stage50 else ""
recomputed_stage50_fingerprint = stable_sha256(stage50_payload) if stage50_payload else ""

stage49_payload = as_dict(stage49.get("authorization_payload")) if stage49 else {}
stored_stage49_fingerprint = normalize_text(stage49.get("authorization_fingerprint")) if stage49 else ""
recomputed_stage49_fingerprint = stable_sha256(stage49_payload) if stage49_payload else ""

drafted_items = [
    i for i in stage51_items
    if normalize_text(i.get("draft_status")).upper() == "DRAFTED"
]
draft_by_key = {normalize_text(i.get("section_key")): i for i in drafted_items}
audit_by_key = {normalize_text(i.get("section_key")): i for i in stage52_items}

draft_keys = set(draft_by_key)
audit_keys = set(audit_by_key)

draft_sha_match = (
    bool(draft_keys)
    and draft_keys == audit_keys
    and all(
        normalize_text(draft_by_key[k].get("draft_sha256"))
        == normalize_text(audit_by_key[k].get("draft_sha256"))
        for k in draft_keys
    )
)

all_section_pass = bool(stage52_items) and all(
    normalize_text(i.get("section_verdict")).upper() == "PASS"
    for i in stage52_items
)

unsupported_count = sum(
    1 for c in stage52_claims
    if normalize_text(c.get("claim_label")).upper() == "UNSUPPORTED"
)
contradicted_count = sum(
    1 for c in stage52_claims
    if normalize_text(c.get("claim_label")).upper() == "CONTRADICTED"
)
to_confirm_claims = [
    c for c in stage52_claims
    if normalize_text(c.get("claim_label")).upper() == "TO_CONFIRM"
]
supported_count = sum(
    1 for c in stage52_claims
    if normalize_text(c.get("claim_label")).upper() == "SUPPORTED"
)


# ---------------------------------------------------------------------
# Hard gate
# ---------------------------------------------------------------------

checks = []

def add_check(name: str, passed: bool, detail: str):
    checks.append({"Check": name, "PASS": bool(passed), "Detail": detail})


add_check("ACTIVE lock",
          normalize_text(lock.get("lock_status")).upper() == "ACTIVE",
          normalize_text(lock.get("lock_status")).upper())
add_check("Workflow allowed", workflow_allowed, f"workflow_allowed={workflow_allowed}")
add_check("Deadline valid", future_deadline(deadline), str(deadline or "")[:10])
add_check("Stage 52 run exists", bool(stage52), stage52_run_id or "MISSING")
add_check("Stage 52 COMPLETED", stage52_status == "COMPLETED", stage52_status)
add_check("Stage 52 global PASS", stage52_verdict == "PASS", stage52_verdict)
add_check("Stage 52 fingerprint stable",
          bool(stage52_fingerprint) and stage52_fingerprint == recomputed_stage52_fingerprint,
          f"stored={stage52_fingerprint[:16]}..., recomputed={recomputed_stage52_fingerprint[:16]}...")
add_check("Stage 51 bound run exists", bool(stage51), stage51_run_id or "MISSING")
add_check("Stage 51 fingerprint stable",
          bool(stored_stage51_fingerprint) and stored_stage51_fingerprint == recomputed_stage51_fingerprint,
          f"stored={stored_stage51_fingerprint[:16]}..., recomputed={recomputed_stage51_fingerprint[:16]}...")
add_check("Stage 50 bound run exists", bool(stage50), stage50_run_id or "MISSING")
add_check("Stage 50 fingerprint stable",
          bool(stored_stage50_fingerprint) and stored_stage50_fingerprint == recomputed_stage50_fingerprint,
          f"stored={stored_stage50_fingerprint[:16]}..., recomputed={recomputed_stage50_fingerprint[:16]}...")
add_check("Stage 49 bound run exists", bool(stage49), stage49_run_id or "MISSING")
add_check("Stage 49 fingerprint stable",
          bool(stored_stage49_fingerprint) and stored_stage49_fingerprint == recomputed_stage49_fingerprint,
          f"stored={stored_stage49_fingerprint[:16]}..., recomputed={recomputed_stage49_fingerprint[:16]}...")
add_check("Draft/audit section inventory exact",
          bool(draft_keys) and draft_keys == audit_keys,
          f"drafted={len(draft_keys)}, audited={len(audit_keys)}")
add_check("Draft SHA256 bindings exact", draft_sha_match, f"matched={draft_sha_match}")
add_check("All Stage 52 sections PASS", all_section_pass,
          f"pass={sum(1 for i in stage52_items if normalize_text(i.get('section_verdict')).upper() == 'PASS')}/{len(stage52_items)}")
add_check("No UNSUPPORTED claims", unsupported_count == 0, f"unsupported={unsupported_count}")
add_check("No CONTRADICTED claims", contradicted_count == 0, f"contradicted={contradicted_count}")
add_check("External submission still disabled",
          bool(stage49) and not bool_true(as_dict(stage49.get("authorization_scope")).get("external_submission")),
          "external_submission must remain false")

stage53_gate = "READY" if all(c["PASS"] for c in checks) else "BLOCKED"
gate_reason = (
    "Stage 52 PASS and the Stage 51/52 proposal evidence chain are stable."
    if stage53_gate == "READY"
    else "Stage 53 fail-closed gate failed: " + "; ".join(c["Check"] for c in checks if not c["PASS"])
)


# ---------------------------------------------------------------------
# Deterministic final review handoff
# ---------------------------------------------------------------------

section_inventory = []
for key in sorted(draft_keys):
    draft = draft_by_key[key]
    audit = audit_by_key.get(key, {})
    section_inventory.append({
        "section_key": key,
        "section_title": normalize_text(draft.get("section_title")),
        "stage51_draft_item_id": str(draft.get("id") or ""),
        "stage52_validation_item_id": str(audit.get("id") or ""),
        "draft_sha256": normalize_text(draft.get("draft_sha256")),
        "audit_sha256": normalize_text(audit.get("audit_sha256")),
        "section_verdict": normalize_text(audit.get("section_verdict")).upper(),
        "supported_count": int(audit.get("supported_count") or 0),
        "to_confirm_count": int(audit.get("to_confirm_count") or 0),
        "unsupported_count": int(audit.get("unsupported_count") or 0),
        "contradicted_count": int(audit.get("contradicted_count") or 0),
    })

open_review_items = [
    {
        "stage52_claim_audit_id": str(c.get("id") or ""),
        "section_key": normalize_text(c.get("section_key")),
        "claim_no": int(c.get("claim_no") or 0),
        "claim_text": normalize_text(c.get("claim_text")),
        "reason": normalize_text(c.get("reason")),
        "source_ids": as_list(c.get("source_ids")),
    }
    for c in to_confirm_claims
]

handoff_basis = {
    "stage": 53,
    "fingerprint_contract": "stage53-v1.0-stable",
    "user_id": user_id,
    "project_id": project_id,
    "opportunity_lock_id": lock_id,
    "opportunity_identity": identity,
    "official_deadline": str(deadline or "")[:10],
    "stage49_run_id": stage49_run_id,
    "stage50_run_id": stage50_run_id,
    "stage51_run_id": stage51_run_id,
    "stage52_run_id": stage52_run_id,
    "stage49_authorization_fingerprint": stored_stage49_fingerprint,
    "stage50_build_fingerprint": stored_stage50_fingerprint,
    "stage51_run_fingerprint": stored_stage51_fingerprint,
    "stage52_run_fingerprint": stage52_fingerprint,
    "section_inventory": section_inventory,
    "open_review_items": open_review_items,
    "stage53_gate": stage53_gate,
}

handoff_fingerprint = stable_sha256(handoff_basis)


# ---------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------

def load_existing_stage53_run():
    if not stage52_run_id or not handoff_fingerprint:
        return None
    data = (
        supabase.table("stage53_review_handoff_runs")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_lock_id", lock_id)
        .eq("stage52_run_id", stage52_run_id)
        .eq("handoff_fingerprint", handoff_fingerprint)
        .limit(1)
        .execute()
    ).data or []
    return data[0] if data else None


def persist_stage53():
    if stage53_gate != "READY":
        raise RuntimeError("Stage 53 is BLOCKED.")

    existing = load_existing_stage53_run()
    if existing:
        return existing

    run_payload = {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage49_run_id": stage49_run_id,
        "stage50_run_id": stage50_run_id,
        "stage51_run_id": stage51_run_id,
        "stage52_run_id": stage52_run_id,
        "stage": 53,
        "review_version": "stage53-v1.0",
        "opportunity_identity": identity,
        "official_deadline": str(deadline or "")[:10] or None,
        "stage49_authorization_fingerprint": stored_stage49_fingerprint,
        "stage50_build_fingerprint": stored_stage50_fingerprint,
        "stage51_run_fingerprint": stored_stage51_fingerprint,
        "stage52_run_fingerprint": stage52_fingerprint,
        "review_status": "READY_FOR_REVIEW",
        "consistency_verdict": "PASS",
        "supported_claims": supported_count,
        "to_confirm_claims": len(to_confirm_claims),
        "unsupported_claims": unsupported_count,
        "contradicted_claims": contradicted_count,
        "section_count": len(section_inventory),
        "handoff_fingerprint": handoff_fingerprint,
        "handoff_payload": handoff_basis,
        "completed_at": now_iso(),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }

    data = supabase.table("stage53_review_handoff_runs").insert(run_payload).execute().data or []
    if not data:
        raise RuntimeError("Could not create Stage 53 handoff run.")
    saved = data[0]
    run_id = str(saved["id"])

    for seq, item in enumerate(section_inventory, start=1):
        item_payload = {
            "stage53_run_id": run_id,
            "stage52_run_id": stage52_run_id,
            "stage51_run_id": stage51_run_id,
            "stage51_draft_item_id": item["stage51_draft_item_id"],
            "stage52_validation_item_id": item["stage52_validation_item_id"],
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "sequence_no": seq,
            "section_key": item["section_key"],
            "section_title": item["section_title"],
            "draft_sha256": item["draft_sha256"],
            "audit_sha256": item["audit_sha256"],
            "section_verdict": item["section_verdict"],
            "supported_count": item["supported_count"],
            "to_confirm_count": item["to_confirm_count"],
            "unsupported_count": item["unsupported_count"],
            "contradicted_count": item["contradicted_count"],
            "handoff_status": "ACCEPTED",
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        supabase.table("stage53_review_handoff_items").insert(item_payload).execute()

    return saved


existing_stage53 = load_existing_stage53_run()
persisted_handoff_items = rows(
    "stage53_review_handoff_items",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage53_run_id": str(existing_stage53.get("id") or ""),
    },
    "sequence_no",
    500,
) if existing_stage53 else []


# ---------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------

st.divider()
st.subheader("Stage 52 → Stage 53 binding")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Stage 52", stage52_verdict)
m2.metric("Audited sections", len(stage52_items))
m3.metric("TO_CONFIRM", len(to_confirm_claims))
m4.metric("Integrity", "VERIFIED" if stage53_gate == "READY" else "FAILED")

with st.expander("Stage 53 hard-gate checks", expanded=False):
    st.dataframe(checks, use_container_width=True, hide_index=True)

st.divider()
st.subheader("Final Stage 53 Decision")

g1, g2, g3, g4 = st.columns(4)
g1.metric("Gate", stage53_gate)
g2.metric("Checks passed", f"{sum(1 for c in checks if c['PASS'])}/{len(checks)}")
g3.metric("Sections", len(section_inventory))
g4.metric("Open review items", len(open_review_items))

if stage53_gate == "READY":
    st.success(
        "Etapa 53: READY_FOR_REVIEW. Toate secțiunile Stage 52 sunt PASS, "
        "iar lanțul de fingerprint-uri și SHA256 este consistent."
    )
else:
    st.error("Etapa 53: BLOCKED. Handoff-ul final nu poate fi persistat.")

st.write(f"**Reason:** {gate_reason}")
st.code(handoff_fingerprint, language=None)

with st.expander("Stage 53 handoff payload", expanded=False):
    st.json({**handoff_basis, "handoff_fingerprint": handoff_fingerprint})

if section_inventory:
    st.divider()
    st.subheader("Final audited section inventory")
    st.dataframe(
        [
            {
                "Section": i["section_title"],
                "Verdict": i["section_verdict"],
                "Supported": i["supported_count"],
                "TO_CONFIRM": i["to_confirm_count"],
                "Unsupported": i["unsupported_count"],
                "Contradicted": i["contradicted_count"],
                "Draft SHA256": i["draft_sha256"][:16] + "...",
                "Audit SHA256": i["audit_sha256"][:16] + "...",
            }
            for i in section_inventory
        ],
        use_container_width=True,
        hide_index=True,
    )

if open_review_items:
    st.divider()
    st.subheader("Open review items — TO_CONFIRM")
    st.warning(
        "Aceste elemente nu sunt tratate ca fapte confirmate. "
        "Ele trebuie păstrate explicit pentru confirmare în review-ul final."
    )
    st.dataframe(
        [
            {
                "Section": i["section_key"],
                "Claim": i["claim_text"],
                "Reason": i["reason"],
            }
            for i in open_review_items
        ],
        use_container_width=True,
        hide_index=True,
    )

st.divider()
st.subheader("Stage 53 persistence")

if existing_stage53:
    st.success(
        f"Stage 53 este deja persistată în Supabase. Run ID: {existing_stage53.get('id')} — "
        f"Status: {existing_stage53.get('review_status')}"
    )
else:
    st.info(
        "Stage 53 nu este încă persistată. Persistă handoff-ul numai după ce gate-ul este READY."
    )

if st.button(
    "🧭 Persist Stage 53 final review handoff",
    type="primary",
    use_container_width=True,
    key="stage53_v1_persist",
    disabled=(stage53_gate != "READY"),
):
    try:
        saved = persist_stage53()
        st.session_state["stage53_run_id"] = str(saved.get("id"))
        st.success(f"Stage 53 persisted — run {saved.get('id')}")
        st.rerun()
    except Exception as exc:
        st.error(
            "Stage 53 persistence failed. Rulează mai întâi SQL-ul Stage 53 în Supabase. "
            f"{type(exc).__name__}: {str(exc)[:1800]}"
        )

if existing_stage53 and persisted_handoff_items:
    st.dataframe(
        [
            {
                "Order": i.get("sequence_no"),
                "Section": i.get("section_title"),
                "Verdict": i.get("section_verdict"),
                "Handoff": i.get("handoff_status"),
                "TO_CONFIRM": i.get("to_confirm_count"),
            }
            for i in sorted(persisted_handoff_items, key=lambda x: int(x.get("sequence_no") or 0))
        ],
        use_container_width=True,
        hide_index=True,
    )

if existing_stage53 and stage53_gate == "READY":
    st.success(
        "Stage 53 poate preda controlul unei viitoare Etape 54 pentru final review/readiness. "
        "Stage 54 trebuie să verifice stage53_run_id + stage52_run_id + lock_id + "
        "handoff_fingerprint și să păstreze explicit toate elementele TO_CONFIRM."
    )

st.caption(
    "Invariantă Stage 53 v1.0: Stage 52 trebuie să fie COMPLETED/PASS; inventarul Stage 51/52 "
    "și SHA256-urile drafturilor trebuie să coincidă exact; UNSUPPORTED și CONTRADICTED trebuie "
    "să fie zero. TO_CONFIRM este permis numai ca open review item și nu devine fapt confirmat."
)

# =====================================================================
# END STAGE 53 v1.0
# =====================================================================
