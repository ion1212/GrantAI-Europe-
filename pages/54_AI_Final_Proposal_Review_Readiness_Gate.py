import os
import json
import hashlib
from datetime import datetime, timezone, date
from typing import Any

import streamlit as st
from supabase import create_client


# =====================================================================
# STAGE 54 v1.0 — FINAL PROPOSAL REVIEW & READINESS GATE
#
# Purpose:
#   Consume ONLY a persisted Stage 53 READY_FOR_REVIEW handoff for the same
#   user/project/ACTIVE opportunity lock.
#
# Stage 54 verifies:
#   - Stage 53 persisted handoff exists and is READY_FOR_REVIEW/PASS;
#   - Stage 53 handoff_fingerprint recomputes exactly from persisted payload;
#   - Stage 53 -> Stage 52 -> Stage 51 bindings are exact;
#   - Stage 53 item inventory is complete and ACCEPTED;
#   - all draft/audit SHA256 bindings are preserved;
#   - UNSUPPORTED and CONTRADICTED remain zero;
#   - every TO_CONFIRM item is carried forward explicitly.
#
# Final readiness states:
#   READY_FOR_SUBMISSION_PREP
#       -> no unresolved TO_CONFIRM items remain.
#
#   NEEDS_CONFIRMATION
#       -> review chain is valid, but one or more TO_CONFIRM items remain.
#
#   BLOCKED
#       -> integrity, binding, deadline, or provenance requirements failed.
#
# Important:
#   Stage 54 DOES NOT submit externally.
#   Stage 54 DOES NOT infer that TO_CONFIRM has been resolved.
#   Stage 54 DOES NOT authorize legal signature or financial commitment.
#
# Persistence:
#   stage54_final_readiness_runs
#   stage54_open_confirmation_items
# =====================================================================

st.set_page_config(
    page_title="Stage 54 v1.0 — Final Proposal Review & Readiness Gate",
    page_icon="✅",
    layout="wide",
)

st.title("✅ Etapa 54 v1.0 — AI Final Proposal Review & Readiness Gate")
st.caption(
    "Etapa 54 verifică handoff-ul Stage 53 și separă integritatea review-ului de "
    "readiness-ul real. TO_CONFIRM rămâne deschis până la confirmare explicită."
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
    st.error("Stage 54 BLOCKED: utilizatorul autentificat nu poate fi identificat.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)
if not projects:
    st.warning("Nu există proiecte.")
    st.stop()

project_map = {project_label(p): p for p in projects}
selected_project = st.selectbox(
    "Project",
    list(project_map.keys()),
    key="stage54_v1_project",
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
    st.error("Stage 54 BLOCKED: nu există opportunity lock ACTIVE.")
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
# Load latest persisted Stage 53 handoff
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
)

stage53 = next(
    (
        r for r in stage53_candidates
        if normalize_text(r.get("review_status")).upper() == "READY_FOR_REVIEW"
        and normalize_text(r.get("consistency_verdict")).upper() == "PASS"
    ),
    stage53_candidates[0] if stage53_candidates else None,
)

if stage53:
    stage53_run_id = str(stage53.get("id") or "")
    stage53_status = normalize_text(stage53.get("review_status")).upper()
    stage53_consistency = normalize_text(stage53.get("consistency_verdict")).upper()
    stage53_fingerprint = normalize_text(stage53.get("handoff_fingerprint"))
    stage52_run_id = str(stage53.get("stage52_run_id") or "")
    stage51_run_id = str(stage53.get("stage51_run_id") or "")
    stage50_run_id = str(stage53.get("stage50_run_id") or "")
    stage49_run_id = str(stage53.get("stage49_run_id") or "")
else:
    stage53_run_id = ""
    stage53_status = "MISSING"
    stage53_consistency = "MISSING"
    stage53_fingerprint = ""
    stage52_run_id = ""
    stage51_run_id = ""
    stage50_run_id = ""
    stage49_run_id = ""

stage53_items = rows(
    "stage53_review_handoff_items",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage53_run_id": stage53_run_id,
    },
    "sequence_no",
    500,
) if stage53_run_id else []


# ---------------------------------------------------------------------
# Load exact Stage 52 / Stage 51 bound rows
# ---------------------------------------------------------------------

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
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    100,
) if stage51_run_id else []

stage51 = next(
    (r for r in stage51_candidates if str(r.get("id") or "") == stage51_run_id),
    None,
)

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


# ---------------------------------------------------------------------
# Recompute Stage 53 fingerprint
# ---------------------------------------------------------------------

stage53_payload = as_dict(stage53.get("handoff_payload")) if stage53 else {}
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
# Exact inventory / integrity checks
# ---------------------------------------------------------------------

stage53_by_key = {
    normalize_text(i.get("section_key")): i
    for i in stage53_items
}

stage52_by_key = {
    normalize_text(i.get("section_key")): i
    for i in stage52_items
}

stage51_by_key = {
    normalize_text(i.get("section_key")): i
    for i in stage51_items
    if normalize_text(i.get("draft_status")).upper() == "DRAFTED"
}

keys53 = set(stage53_by_key)
keys52 = set(stage52_by_key)
keys51 = set(stage51_by_key)

inventory_exact = bool(keys53) and keys53 == keys52 == keys51

sha_bindings_exact = (
    inventory_exact
    and all(
        normalize_text(stage53_by_key[k].get("draft_sha256"))
        == normalize_text(stage52_by_key[k].get("draft_sha256"))
        == normalize_text(stage51_by_key[k].get("draft_sha256"))
        and normalize_text(stage53_by_key[k].get("audit_sha256"))
        == normalize_text(stage52_by_key[k].get("audit_sha256"))
        for k in keys53
    )
)

all_handoff_accepted = bool(stage53_items) and all(
    normalize_text(i.get("handoff_status")).upper() == "ACCEPTED"
    and normalize_text(i.get("section_verdict")).upper() == "PASS"
    for i in stage53_items
)

unsupported_claims = [
    c for c in stage52_claims
    if normalize_text(c.get("claim_label")).upper() == "UNSUPPORTED"
]

contradicted_claims = [
    c for c in stage52_claims
    if normalize_text(c.get("claim_label")).upper() == "CONTRADICTED"
]

to_confirm_claims = [
    c for c in stage52_claims
    if normalize_text(c.get("claim_label")).upper() == "TO_CONFIRM"
]

supported_claims = [
    c for c in stage52_claims
    if normalize_text(c.get("claim_label")).upper() == "SUPPORTED"
]


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
    "Stage 53 persisted run exists",
    bool(stage53),
    stage53_run_id or "MISSING",
)

add_check(
    "Stage 53 READY_FOR_REVIEW",
    stage53_status == "READY_FOR_REVIEW",
    stage53_status,
)

add_check(
    "Stage 53 consistency PASS",
    stage53_consistency == "PASS",
    stage53_consistency,
)

add_check(
    "Stage 53 fingerprint stable",
    bool(stage53_fingerprint)
    and stage53_fingerprint == recomputed_stage53_fingerprint,
    (
        f"stored={stage53_fingerprint[:16]}..., "
        f"recomputed={recomputed_stage53_fingerprint[:16]}..."
    ),
)

add_check(
    "Stage 52 bound run exists",
    bool(stage52),
    stage52_run_id or "MISSING",
)

add_check(
    "Stage 52 COMPLETED/PASS",
    bool(stage52)
    and normalize_text(stage52.get("run_status")).upper() == "COMPLETED"
    and normalize_text(stage52.get("global_verdict")).upper() == "PASS",
    (
        f"{normalize_text(stage52.get('run_status')).upper()}/"
        f"{normalize_text(stage52.get('global_verdict')).upper()}"
        if stage52 else "MISSING"
    ),
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
    "Stage 51/52/53 section inventory exact",
    inventory_exact,
    f"stage51={len(keys51)}, stage52={len(keys52)}, stage53={len(keys53)}",
)

add_check(
    "Draft/audit SHA256 bindings exact",
    sha_bindings_exact,
    f"matched={sha_bindings_exact}",
)

add_check(
    "Every Stage 53 handoff item ACCEPTED/PASS",
    all_handoff_accepted,
    f"accepted={sum(1 for i in stage53_items if normalize_text(i.get('handoff_status')).upper() == 'ACCEPTED')}/{len(stage53_items)}",
)

add_check(
    "No UNSUPPORTED claims",
    len(unsupported_claims) == 0,
    f"unsupported={len(unsupported_claims)}",
)

add_check(
    "No CONTRADICTED claims",
    len(contradicted_claims) == 0,
    f"contradicted={len(contradicted_claims)}",
)

integrity_pass = all(c["PASS"] for c in checks)


# ---------------------------------------------------------------------
# Final readiness verdict
# ---------------------------------------------------------------------

if not integrity_pass:
    readiness_status = "BLOCKED"
    readiness_reason = (
        "Stage 54 fail-closed integrity gate failed: "
        + "; ".join(c["Check"] for c in checks if not c["PASS"])
    )
elif to_confirm_claims:
    readiness_status = "NEEDS_CONFIRMATION"
    readiness_reason = (
        f"Review chain is valid, but {len(to_confirm_claims)} TO_CONFIRM item(s) remain unresolved. "
        "They must be explicitly confirmed or removed before submission preparation."
    )
else:
    readiness_status = "READY_FOR_SUBMISSION_PREP"
    readiness_reason = (
        "Review chain is valid and no unresolved TO_CONFIRM, UNSUPPORTED, or CONTRADICTED claims remain."
    )


# ---------------------------------------------------------------------
# Open confirmation inventory
# ---------------------------------------------------------------------

open_confirmation_items = [
    {
        "stage52_claim_audit_id": str(c.get("id") or ""),
        "section_key": normalize_text(c.get("section_key")),
        "claim_no": int(c.get("claim_no") or 0),
        "claim_text": normalize_text(c.get("claim_text")),
        "reason": normalize_text(c.get("reason")),
        "source_ids": as_list(c.get("source_ids")),
        "confirmation_status": "OPEN",
    }
    for c in to_confirm_claims
]


# ---------------------------------------------------------------------
# Stable readiness fingerprint
# ---------------------------------------------------------------------

readiness_basis = {
    "stage": 54,
    "fingerprint_contract": "stage54-v1.0-stable",
    "user_id": user_id,
    "project_id": project_id,
    "opportunity_lock_id": lock_id,
    "opportunity_identity": identity,
    "official_deadline": str(deadline or "")[:10],

    "stage51_run_id": stage51_run_id,
    "stage52_run_id": stage52_run_id,
    "stage53_run_id": stage53_run_id,

    "stage52_run_fingerprint": stored_stage52_fingerprint,
    "stage53_handoff_fingerprint": stage53_fingerprint,

    "section_inventory": [
        {
            "section_key": normalize_text(i.get("section_key")),
            "draft_sha256": normalize_text(i.get("draft_sha256")),
            "audit_sha256": normalize_text(i.get("audit_sha256")),
            "handoff_status": normalize_text(i.get("handoff_status")).upper(),
            "section_verdict": normalize_text(i.get("section_verdict")).upper(),
        }
        for i in sorted(
            stage53_items,
            key=lambda x: int(x.get("sequence_no") or 0),
        )
    ],

    "supported_claim_count": len(supported_claims),
    "to_confirm_claim_count": len(to_confirm_claims),
    "unsupported_claim_count": len(unsupported_claims),
    "contradicted_claim_count": len(contradicted_claims),

    "open_confirmation_items": open_confirmation_items,
    "readiness_status": readiness_status,
}

readiness_fingerprint = stable_sha256(readiness_basis)


# ---------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------

def load_existing_stage54():
    if not stage53_run_id or not readiness_fingerprint:
        return None

    data = (
        supabase.table("stage54_final_readiness_runs")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_lock_id", lock_id)
        .eq("stage53_run_id", stage53_run_id)
        .eq("readiness_fingerprint", readiness_fingerprint)
        .limit(1)
        .execute()
    ).data or []

    return data[0] if data else None


def persist_stage54():
    existing = load_existing_stage54()
    if existing:
        return existing

    payload = {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,

        "stage51_run_id": stage51_run_id,
        "stage52_run_id": stage52_run_id,
        "stage53_run_id": stage53_run_id,

        "stage": 54,
        "readiness_version": "stage54-v1.0",

        "opportunity_identity": identity,
        "official_deadline": str(deadline or "")[:10] or None,

        "stage52_run_fingerprint": stored_stage52_fingerprint,
        "stage53_handoff_fingerprint": stage53_fingerprint,

        "integrity_status": "PASS" if integrity_pass else "BLOCKED",
        "readiness_status": readiness_status,
        "readiness_reason": readiness_reason,

        "section_count": len(stage53_items),
        "supported_claims": len(supported_claims),
        "to_confirm_claims": len(to_confirm_claims),
        "unsupported_claims": len(unsupported_claims),
        "contradicted_claims": len(contradicted_claims),

        "readiness_fingerprint": readiness_fingerprint,
        "readiness_payload": readiness_basis,

        "completed_at": now_iso(),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }

    data = (
        supabase.table("stage54_final_readiness_runs")
        .insert(payload)
        .execute()
    ).data or []

    if not data:
        raise RuntimeError("Could not persist Stage 54 readiness run.")

    saved = data[0]
    run_id = str(saved["id"])

    for item in open_confirmation_items:
        item_payload = {
            "stage54_run_id": run_id,
            "stage53_run_id": stage53_run_id,
            "stage52_run_id": stage52_run_id,
            "stage52_claim_audit_id": item["stage52_claim_audit_id"],

            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,

            "section_key": item["section_key"],
            "claim_no": item["claim_no"],
            "claim_text": item["claim_text"],
            "reason": item["reason"],
            "source_ids": item["source_ids"],

            "confirmation_status": "OPEN",
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }

        supabase.table("stage54_open_confirmation_items").insert(item_payload).execute()

    return saved


existing_stage54 = load_existing_stage54()

persisted_open_items = rows(
    "stage54_open_confirmation_items",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage54_run_id": str(existing_stage54.get("id") or ""),
    },
    "claim_no",
    1000,
) if existing_stage54 else []


# ---------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------

st.divider()
st.subheader("Stage 53 → Stage 54 final readiness binding")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Stage 53", stage53_status)
m2.metric("Sections", len(stage53_items))
m3.metric("TO_CONFIRM", len(to_confirm_claims))
m4.metric("Integrity", "PASS" if integrity_pass else "BLOCKED")

with st.expander("Stage 54 hard-gate checks", expanded=False):
    st.dataframe(
        checks,
        use_container_width=True,
        hide_index=True,
    )

st.divider()
st.subheader("Final Stage 54 Readiness Decision")

r1, r2, r3, r4 = st.columns(4)
r1.metric("Readiness", readiness_status)
r2.metric("Checks passed", f"{sum(1 for c in checks if c['PASS'])}/{len(checks)}")
r3.metric("Supported", len(supported_claims))
r4.metric("Open confirmations", len(to_confirm_claims))

if readiness_status == "READY_FOR_SUBMISSION_PREP":
    st.success(
        "Etapa 54: READY_FOR_SUBMISSION_PREP. Nu mai există TO_CONFIRM, "
        "UNSUPPORTED sau CONTRADICTED claims."
    )
elif readiness_status == "NEEDS_CONFIRMATION":
    st.warning(
        f"Etapa 54: NEEDS_CONFIRMATION. Lanțul este valid, dar "
        f"{len(to_confirm_claims)} element(e) TO_CONFIRM trebuie rezolvate explicit."
    )
else:
    st.error("Etapa 54: BLOCKED. Integritatea review-ului final nu este validă.")

st.write(f"**Reason:** {readiness_reason}")
st.code(readiness_fingerprint, language=None)

with st.expander("Stage 54 readiness payload", expanded=False):
    st.json({
        **readiness_basis,
        "readiness_fingerprint": readiness_fingerprint,
    })

if open_confirmation_items:
    st.divider()
    st.subheader("Open confirmation queue")
    st.warning(
        "Aceste elemente nu sunt confirmate. Stage 54 le păstrează explicit "
        "pentru o etapă de confirmation/resolution."
    )

    st.dataframe(
        [
            {
                "Section": i["section_key"],
                "Claim #": i["claim_no"],
                "Claim": i["claim_text"],
                "Reason": i["reason"],
                "Status": i["confirmation_status"],
            }
            for i in open_confirmation_items
        ],
        use_container_width=True,
        hide_index=True,
    )

st.divider()
st.subheader("Stage 54 persistence")

if existing_stage54:
    st.success(
        f"Stage 54 este deja persistată în Supabase. Run ID: {existing_stage54.get('id')} — "
        f"Status: {existing_stage54.get('readiness_status')}"
    )
else:
    st.info(
        "Persistă verdictul Stage 54 pentru a păstra readiness-ul și toate "
        "elementele TO_CONFIRM într-o coadă explicită."
    )

if st.button(
    "✅ Persist Stage 54 final readiness",
    type="primary",
    use_container_width=True,
    key="stage54_v1_persist",
):
    try:
        saved = persist_stage54()
        st.session_state["stage54_run_id"] = str(saved.get("id"))
        st.success(
            f"Stage 54 persisted: {saved.get('readiness_status')} — run {saved.get('id')}"
        )
        st.rerun()
    except Exception as exc:
        st.error(
            "Stage 54 persistence failed. Rulează mai întâi SQL-ul Stage 54 în Supabase. "
            f"{type(exc).__name__}: {str(exc)[:1800]}"
        )

if persisted_open_items:
    st.subheader("Persisted confirmation items")
    st.dataframe(
        [
            {
                "Section": i.get("section_key"),
                "Claim #": i.get("claim_no"),
                "Claim": i.get("claim_text"),
                "Status": i.get("confirmation_status"),
            }
            for i in persisted_open_items
        ],
        use_container_width=True,
        hide_index=True,
    )

if existing_stage54:
    if normalize_text(existing_stage54.get("readiness_status")).upper() == "READY_FOR_SUBMISSION_PREP":
        st.success(
            "Stage 54 poate preda controlul unei viitoare Etape 55 pentru submission-pack finalization. "
            "Stage 55 trebuie să verifice stage54_run_id + stage53_run_id + lock_id + readiness_fingerprint."
        )
    elif normalize_text(existing_stage54.get("readiness_status")).upper() == "NEEDS_CONFIRMATION":
        st.warning(
            "Stage 54 nu predă încă controlul către submission preparation. "
            "O viitoare Etapă 55 trebuie mai întâi să rezolve explicit open confirmation items."
        )

st.caption(
    "Invariantă Stage 54 v1.0: READY_FOR_SUBMISSION_PREP necesită zero TO_CONFIRM, "
    "zero UNSUPPORTED și zero CONTRADICTED. TO_CONFIRM nu este convertit automat în fapt confirmat."
)

# =====================================================================
# END STAGE 54 v1.0
# =====================================================================
