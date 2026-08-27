import os
import json
import hashlib
from datetime import datetime, timezone, date
from typing import Any

import streamlit as st
from supabase import create_client


# =====================================================================
# STAGE 47 v1.2 — FINAL OPPORTUNITY ELIGIBILITY DECISION GATE
#
# Purpose:
#   Consume ONLY a PASS from Stage 46 for the same user/project/ACTIVE lock.
#   Re-bind the Stage 46 provenance run to the current opportunity identity,
#   deadline and canonical requirement set, then issue a fail-closed decision:
#
#       ELIGIBLE   -> all mandatory official eligibility requirements VERIFIED
#       INELIGIBLE -> a mandatory requirement is explicitly REJECTED
#       BLOCKED    -> missing/stale/mismatched/incomplete Stage 46 evidence
#
# Important:
#   Stage 47 does NOT re-fetch EC documents and does NOT rewrite Stage 46.
#   Stage 46 owns provenance verification. Stage 47 owns the final eligibility
#   decision over that verified evidence and prevents cross-lock/cross-project
#   or stale-run reuse.
#
# Persistence:
#   v1.1 persists the final gate in Supabase tables:
#     final_opportunity_eligibility_runs
#     final_opportunity_eligibility_items
#   The decision remains deterministic and is bound by a SHA256 fingerprint.
# =====================================================================

st.set_page_config(
    page_title="Stage 47 v1.2 — Final Eligibility Decision",
    page_icon="⚖️",
    layout="wide",
)

st.title("⚖️ Etapa 47 v1.2 — AI Final Opportunity Eligibility Decision Gate")
st.caption(
    "Etapa 47 consumă exclusiv un PASS valid din Stage 46 pentru același proiect și același "
    "opportunity lock. Decizia este fail-closed: orice lipsă, nepotrivire sau verdict neconfirmat "
    "produce BLOCKED, nu ELIGIBLE."
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


def canonical_requirement(value: Any) -> str:
    text = normalize_text(value).lower()
    text = " ".join(text.split())

    aliases = {
        "applicant eligibility": "applicant eligibility",
        "applicant": "applicant eligibility",
        "eligibility": "applicant eligibility",

        "consortium requirements": "consortium requirements",
        "consortium requirement": "consortium requirements",
        "consortium": "consortium requirements",

        "trl requirements": "trl requirements",
        "trl requirement": "trl requirements",
        "trl": "trl requirements",
        "technology readiness level": "trl requirements",
        "technology readiness level requirements": "trl requirements",

        "geographic eligibility": "geographic eligibility",
        "geographical eligibility": "geographic eligibility",
        "geographic": "geographic eligibility",
        "geographical": "geographic eligibility",
    }
    return aliases.get(text, text)


def sha256_json(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def bool_true(value: Any) -> bool:
    return value is True or normalize_text(value).lower() in {"true", "1", "yes"}


# ---------------------------------------------------------------------
# Auth / project / ACTIVE lock
# ---------------------------------------------------------------------

try:
    supabase = get_supabase()
except Exception as exc:
    st.error(f"Supabase nu poate fi inițializat: {type(exc).__name__}: {exc}")
    st.stop()

restore_auth_session(supabase)
user_id = current_user_id(supabase)

if not user_id:
    st.error("Autentificarea nu este disponibilă. Intră în cont înainte de Stage 47.")
    st.stop()

projects = rows("projects", {"user_id": user_id}, "updated_at", 200)

if not projects:
    st.warning("Nu există proiecte.")
    st.stop()

project_map = {project_label(p): p for p in projects}
selected_project = st.selectbox(
    "Project",
    list(project_map.keys()),
    key="stage47_v1_project",
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
    st.error("Stage 47 BLOCKED: nu există opportunity lock ACTIVE pentru proiect.")
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
# Stage 46 binding
# ---------------------------------------------------------------------

provenance_runs = rows(
    "locked_evidence_provenance_runs",
    {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
    },
    "created_at",
    100,
)

latest46 = provenance_runs[0] if provenance_runs else None

if latest46:
    stage46_run_id = str(latest46.get("id") or "")
    stage46_status = normalize_text(latest46.get("run_status")).upper()
    stage46_summary = as_dict(latest46.get("summary"))
else:
    stage46_run_id = ""
    stage46_status = "MISSING"
    stage46_summary = {}

items46 = []
sources46 = []

if stage46_run_id:
    items46 = rows(
        "locked_evidence_provenance_items",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "provenance_run_id": stage46_run_id,
        },
        "created_at",
        500,
    )

    sources46 = rows(
        "locked_evidence_provenance_sources",
        {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "provenance_run_id": stage46_run_id,
        },
        "created_at",
        500,
    )


# ---------------------------------------------------------------------
# Canonical requirement reconstruction
# ---------------------------------------------------------------------

MANDATORY_REQUIREMENTS = (
    "applicant eligibility",
    "consortium requirements",
    "trl requirements",
    "geographic eligibility",
)

latest_by_requirement = {}

for item in items46:
    req = canonical_requirement(
        item.get("requirement_label")
        or item.get("requirement_category")
        or item.get("requirement")
        or item.get("requirement_key")
    )
    if req in MANDATORY_REQUIREMENTS and req not in latest_by_requirement:
        latest_by_requirement[req] = item


def requirement_verdict(req: str) -> dict:
    item = latest_by_requirement.get(req)
    if not item:
        return {
            "requirement": req,
            "status": "MISSING",
            "verified": False,
            "rejected": False,
            "reason": "Stage 46 item missing for this canonical requirement.",
            "item_id": None,
            "final_url": None,
        }

    status = normalize_text(item.get("validation_status")).upper()

    verified = (
        status == "VERIFIED"
        and bool_true(item.get("official_final_host_verified"))
        and bool_true(item.get("excerpt_present_in_source"))
        and bool_true(item.get("exact_topic_in_source"))
        and bool_true(item.get("explicit_evidence_verified"))
        and not bool_true(item.get("auth_or_error_url_detected"))
        and not bool_true(item.get("auth_or_error_content_detected"))
    )

    # Provenance is mandatory. Stage 46 v2.10 can satisfy it either through
    # the ordinary chain or the TRL same-document provenance rule.
    provenance_ok = (
        bool_true(item.get("provenance_chain_verified"))
        or (
            req == "trl requirements"
            and bool_true(item.get("trl_same_document_provenance"))
        )
    )

    verified = bool(verified and provenance_ok)

    return {
        "requirement": req,
        "status": status,
        "verified": verified,
        "rejected": status == "REJECTED",
        "reason": (
            item.get("validation_reason")
            or item.get("rejection_reason")
            or ""
        ),
        "item_id": item.get("id"),
        "final_url": item.get("final_url"),
        "provenance_ok": provenance_ok,
    }


verdicts = [requirement_verdict(req) for req in MANDATORY_REQUIREMENTS]


# ---------------------------------------------------------------------
# Independent Stage 47 consistency checks
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
    f"lock_status={normalize_text(lock.get('lock_status')).upper() or '—'}",
)

add_check(
    "Workflow allowed",
    workflow_allowed,
    f"workflow_allowed={workflow_allowed}",
)

add_check(
    "Opportunity identity present",
    bool(identity),
    identity or "Missing opportunity_identity",
)

add_check(
    "Deadline still valid",
    future_deadline(deadline),
    str(deadline or "Missing deadline")[:10],
)

add_check(
    "Stage 46 run exists",
    bool(latest46),
    stage46_run_id or "No Stage 46 provenance run",
)

add_check(
    "Stage 46 is PASS",
    stage46_status == "PASS",
    f"run_status={stage46_status}",
)

summary_stage = stage46_summary.get("stage")
summary_gate = normalize_text(stage46_summary.get("gate")).upper()

add_check(
    "Stage 46 summary binding",
    (
        bool(latest46)
        and str(summary_stage) == "46"
        and summary_gate == "PASS"
    ),
    f"summary.stage={summary_stage!r}, summary.gate={summary_gate or '—'}",
)

add_check(
    "Canonical requirement set complete",
    set(latest_by_requirement.keys()) == set(MANDATORY_REQUIREMENTS),
    f"found={len(latest_by_requirement)}/4",
)

for verdict in verdicts:
    add_check(
        f"{verdict['requirement']} VERIFIED",
        verdict["verified"],
        (
            f"status={verdict['status']}; provenance_ok="
            f"{verdict.get('provenance_ok', False)}"
        ),
    )

verified_source_count = sum(
    1
    for source in sources46
    if normalize_text(source.get("fetch_status")).upper() == "VERIFIED"
    and not bool_true(source.get("auth_or_error_url"))
)

add_check(
    "Stage 46 verified source audit",
    verified_source_count >= len(MANDATORY_REQUIREMENTS),
    f"verified_sources={verified_source_count}, required>=4",
)


# ---------------------------------------------------------------------
# Final fail-closed decision
# ---------------------------------------------------------------------

explicit_rejections = [
    v["requirement"] for v in verdicts if v["rejected"]
]

all_checks_pass = all(row["PASS"] for row in checks)

if explicit_rejections:
    final_decision = "INELIGIBLE"
    decision_reason = (
        "Cel puțin o cerință obligatorie are verdict Stage 46 REJECTED: "
        + ", ".join(explicit_rejections)
    )
elif all_checks_pass:
    final_decision = "ELIGIBLE"
    decision_reason = (
        "Stage 46 PASS este legat de același ACTIVE lock, toate cele patru "
        "cerințe canonice obligatorii sunt VERIFIED, deadline-ul este valid, "
        "iar auditul de surse este complet."
    )
else:
    final_decision = "BLOCKED"
    failed_checks = [row["Check"] for row in checks if not row["PASS"]]
    decision_reason = (
        "Stage 47 nu poate emite ELIGIBLE deoarece una sau mai multe condiții "
        "fail-closed nu sunt satisfăcute: " + "; ".join(failed_checks)
    )


# v1.2: separate volatile display metadata from the immutable decision input.
# The v1.1 fingerprint included generated_at, so every Streamlit rerun generated
# a different fingerprint. That made a successfully persisted decision look
# "not persisted" immediately after st.rerun().
decision_basis = {
    "stage": 47,
    "fingerprint_contract": "stage47-v1.2-stable",
    "user_id": user_id,
    "project_id": project_id,
    "opportunity_lock_id": lock_id,
    "opportunity_identity": identity,
    "official_deadline": str(deadline or "")[:10],
    "stage46_run_id": stage46_run_id,
    "stage46_status": stage46_status,
    "decision": final_decision,
    "mandatory_requirements": {
        v["requirement"]: {
            "status": v["status"],
            "verified": v["verified"],
            "item_id": v["item_id"],
            "final_url": v["final_url"],
        }
        for v in verdicts
    },
    "checks": checks,
}

decision_fingerprint = sha256_json(decision_basis)

decision_payload = {
    **decision_basis,
    "version": "v1.2",
    "generated_at": now_iso(),
}

# Keep a deterministic in-session handoff for a future Stage 48.
st.session_state["stage47_final_decision"] = {
    **decision_payload,
    "decision_fingerprint": decision_fingerprint,
}



# ---------------------------------------------------------------------
# Stage 47 Supabase persistence
# ---------------------------------------------------------------------

def load_existing_stage47_run(fingerprint: str):
    if not fingerprint:
        return None
    data = (
        supabase.table("final_opportunity_eligibility_runs")
        .select("*")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .eq("opportunity_lock_id", lock_id)
        .eq("stage46_run_id", stage46_run_id)
        .eq("decision_fingerprint", fingerprint)
        .limit(1)
        .execute()
    ).data or []
    return data[0] if data else None


def persist_stage47_decision():
    existing = load_existing_stage47_run(decision_fingerprint)

    run_payload = {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage46_run_id": stage46_run_id,
        "stage": 47,
        "validator_version": "stage47-v1.2",
        "opportunity_identity": identity,
        "official_deadline": str(deadline or "")[:10] or None,
        "stage46_status": stage46_status,
        "decision": final_decision,
        "decision_reason": decision_reason,
        "decision_fingerprint": decision_fingerprint,
        "mandatory_requirements": decision_payload.get("mandatory_requirements") or {},
        "checks": checks,
        "decision_payload": decision_payload,
        "completed_at": now_iso(),
        "updated_at": now_iso(),
    }

    if existing:
        run_id = str(existing["id"])
        saved = (
            supabase.table("final_opportunity_eligibility_runs")
            .update(run_payload)
            .eq("id", run_id)
            .eq("user_id", user_id)
            .execute()
        ).data or []
        run_row = saved[0] if saved else {**existing, **run_payload}
    else:
        run_payload["created_at"] = now_iso()
        saved = (
            supabase.table("final_opportunity_eligibility_runs")
            .insert(run_payload)
            .execute()
        ).data or []
        if not saved:
            raise RuntimeError("Could not persist Stage 47 run.")
        run_row = saved[0]
        run_id = str(run_row["id"])

    # Upsert one immutable-audit-style item per canonical requirement within this run.
    for verdict in verdicts:
        req = verdict["requirement"]
        item_payload = {
            "stage47_run_id": run_id,
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_lock_id": lock_id,
            "stage46_run_id": stage46_run_id,
            "stage46_provenance_item_id": verdict.get("item_id"),
            "requirement_key": req,
            "stage46_verdict": verdict.get("status"),
            "stage47_accepted": bool(verdict.get("verified")),
            "provenance_ok": bool(verdict.get("provenance_ok")),
            "final_url": verdict.get("final_url"),
            "reason": verdict.get("reason") or "",
            "updated_at": now_iso(),
        }

        existing_item = (
            supabase.table("final_opportunity_eligibility_items")
            .select("id")
            .eq("user_id", user_id)
            .eq("stage47_run_id", run_id)
            .eq("requirement_key", req)
            .limit(1)
            .execute()
        ).data or []

        if existing_item:
            (
                supabase.table("final_opportunity_eligibility_items")
                .update(item_payload)
                .eq("id", existing_item[0]["id"])
                .eq("user_id", user_id)
                .execute()
            )
        else:
            item_payload["created_at"] = now_iso()
            (
                supabase.table("final_opportunity_eligibility_items")
                .insert(item_payload)
                .execute()
            )

    return run_row


existing_stage47 = load_existing_stage47_run(decision_fingerprint)


# ---------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------

st.divider()
st.subheader("Stage 46 → Stage 47 binding")

b1, b2, b3, b4 = st.columns(4)
b1.metric("Stage 46", stage46_status)
b2.metric("Stage 46 items", len(items46))
b3.metric("Verified sources", verified_source_count)
b4.metric("Mandatory requirements", f"{len(latest_by_requirement)}/4")

st.dataframe(
    [
        {
            "Requirement": v["requirement"],
            "Stage 46 verdict": v["status"],
            "Stage 47 accepted": v["verified"],
            "Provenance": v.get("provenance_ok", False),
            "Final URL": v["final_url"],
            "Reason": v["reason"],
        }
        for v in verdicts
    ],
    use_container_width=True,
    hide_index=True,
)

with st.expander("Stage 47 hard-gate checks", expanded=False):
    st.dataframe(
        checks,
        use_container_width=True,
        hide_index=True,
    )

st.divider()
st.subheader("Final Stage 47 Decision")

d1, d2, d3 = st.columns(3)
d1.metric("Decision", final_decision)
d2.metric(
    "Checks passed",
    f"{sum(1 for x in checks if x['PASS'])}/{len(checks)}",
)
d3.metric(
    "Stage 46 verified",
    f"{sum(1 for v in verdicts if v['verified'])}/4",
)

if final_decision == "ELIGIBLE":
    st.success(
        "Etapa 47: ELIGIBLE. Oportunitatea a trecut gate-ul final de eligibilitate "
        "pe baza celor patru cerințe oficiale validate în Stage 46."
    )
elif final_decision == "INELIGIBLE":
    st.error(
        "Etapa 47: INELIGIBLE. Există cel puțin un verdict oficial REJECTED."
    )
else:
    st.warning(
        "Etapa 47: BLOCKED. Nu se emite o decizie pozitivă până când toate "
        "condițiile fail-closed sunt satisfăcute."
    )

st.write(f"**Reason:** {decision_reason}")
st.code(decision_fingerprint, language=None)
st.caption(
    "Decision fingerprint SHA256 leagă decizia de proiect, ACTIVE lock, opportunity identity, "
    "deadline, Stage 46 run și cele patru verdicturi canonice. Orice schimbare produce un fingerprint nou."
)

with st.expander("Stage 47 decision payload", expanded=False):
    st.json({
        **decision_payload,
        "decision_fingerprint": decision_fingerprint,
    })

st.divider()
st.subheader("Stage 47 persistence")

if existing_stage47:
    st.success(
        f"Decizia Stage 47 este deja salvată în Supabase. Run ID: {existing_stage47.get('id')}"
    )
else:
    st.info(
        "Decizia curentă nu este încă persistată. Salveaz-o pentru ca o etapă viitoare "
        "să poată verifica Stage 47 prin run_id + fingerprint."
    )

if st.button(
    "💾 Persist Stage 47 decision in Supabase",
    type="primary",
    use_container_width=True,
    key="stage47_v12_persist",
):
    try:
        saved_run = persist_stage47_decision()
        st.session_state["stage47_persisted_run_id"] = str(saved_run.get("id"))
        st.success(
            f"Stage 47 persisted: {saved_run.get('decision')} — run {saved_run.get('id')}"
        )
        st.rerun()
    except Exception as exc:
        st.error(
            "Stage 47 persistence failed. Verifică dacă SQL-ul Stage 47 a fost rulat în Supabase. "
            f"{type(exc).__name__}: {str(exc)[:1200]}"
        )

if final_decision == "ELIGIBLE":
    st.success(
        "Stage 47 poate preda controlul unei viitoare Etape 48 numai după persistență. "
        "Stage 48 trebuie să verifice stage47_run_id + lock_id + Stage 46 run_id + decision_fingerprint."
    )
else:
    st.info(
        "Nu există handoff pozitiv către etapa următoare. Stage 47 rămâne fail-closed."
    )

st.caption(
    "Invariantă Stage 47 v1.2: un PASS Stage 46 nu poate fi reutilizat între proiecte sau lock-uri. "
    "ELIGIBLE necesită același ACTIVE lock, deadline valid, Stage 46 PASS și toate cele patru "
    "cerințe canonice VERIFIED cu provenance acceptat."
)

# =====================================================================
# END STAGE 47 v1.2
# =====================================================================
