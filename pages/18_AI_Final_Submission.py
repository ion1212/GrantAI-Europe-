import os
import re
import json
from datetime import datetime, timezone

import streamlit as st
from supabase import create_client


st.set_page_config(
    page_title="Etapa 18 — AI Final Submission",
    page_icon="🚦",
    layout="wide",
)

st.title("🚦 Etapa 18 — AI Final Submission Validator & Export Center")
st.caption(
    "Validează pachetul din Etapa 17 și blochează exportul final până când "
    "problemele critice și verificările obligatorii sunt rezolvate."
)


# ---------------------------------------------------------------------
# Supabase
# ---------------------------------------------------------------------
def get_secret(*names):
    for name in names:
        try:
            value = st.secrets.get(name)
            if value:
                return value
        except Exception:
            pass
        value = os.getenv(name)
        if value:
            return value
    return None


@st.cache_resource
def get_supabase():
    url = get_secret("SUPABASE_URL", "supabase_url")
    key = get_secret(
        "SUPABASE_KEY",
        "SUPABASE_ANON_KEY",
        "supabase_key",
        "supabase_anon_key",
    )
    if not url or not key:
        return None
    return create_client(url, key)


supabase = get_supabase()
if supabase is None:
    st.error("Conexiunea Supabase nu este disponibilă.")
    st.stop()


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def rows(response):
    return getattr(response, "data", None) or []


def safe_query(table, *, filters=None, order=None, limit=None):
    try:
        q = supabase.table(table).select("*")
        for key, value in (filters or {}).items():
            if value not in (None, ""):
                q = q.eq(key, value)
        if order:
            q = q.order(order[0], desc=order[1])
        if limit:
            q = q.limit(limit)
        return rows(q.execute())
    except Exception:
        return []


def latest(table, *, project_id=None, opportunity_identity=None):
    filters = {}
    if project_id:
        filters["project_id"] = project_id

    data = safe_query(
        table,
        filters=filters,
        order=("created_at", True),
        limit=50,
    )
    if not data:
        return None

    if opportunity_identity:
        exact = [
            r for r in data
            if str(r.get("opportunity_identity") or "") == str(opportunity_identity)
        ]
        if exact:
            return exact[0]

    return data[0]


def to_int(value, default=0):
    try:
        return int(float(value))
    except Exception:
        return default


def first_value(record, keys, default=None):
    if not isinstance(record, dict):
        return default
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return default


def json_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict):
                return [parsed]
        except Exception:
            return [text]
    return [value]


def item_text(item):
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        for key in (
            "task", "title", "issue", "message", "description",
            "name", "text", "requirement", "recommendation"
        ):
            if item.get(key):
                return str(item[key]).strip()
        return json.dumps(item, ensure_ascii=False)
    return str(item)


PLACEHOLDER_PATTERNS = [
    r"\[\s*TO\s+CONFIRM[^\]]*\]",
    r"\[\s*TBC[^\]]*\]",
    r"\[\s*TBD[^\]]*\]",
    r"\[\s*INSERT[^\]]*\]",
    r"\{\{[^{}]+\}\}",
    r"<\s*TO\s+CONFIRM[^>]*>",
    r"\bTO\s+CONFIRM\b",
    r"\bTBC\b",
    r"\bTBD\b",
]


def find_placeholders(text):
    text = text or ""
    found = []
    for pattern in PLACEHOLDER_PATTERNS:
        found.extend(re.findall(pattern, text, flags=re.IGNORECASE))
    unique = []
    seen = set()
    for value in found:
        cleaned = str(value).strip()
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            unique.append(cleaned)
    return unique


def get_user_id():
    # Compatible with common Streamlit/Supabase session patterns.
    candidates = [
        st.session_state.get("user_id"),
        st.session_state.get("auth_user_id"),
    ]
    user = st.session_state.get("user")
    if isinstance(user, dict):
        candidates.append(user.get("id"))
    elif user is not None:
        candidates.append(getattr(user, "id", None))

    for candidate in candidates:
        if candidate:
            return str(candidate)

    try:
        response = supabase.auth.get_user()
        user_obj = getattr(response, "user", None)
        if user_obj and getattr(user_obj, "id", None):
            return str(user_obj.id)
    except Exception:
        pass

    return None


def display_name(project):
    name = first_value(project, ["name", "project_name", "title"], "Project")
    pid = str(project.get("id") or "")
    return f"{name} — {pid[:8]}" if pid else str(name)


def opportunity_name(opportunity):
    return str(
        first_value(
            opportunity,
            ["title", "name", "opportunity_name", "call_title"],
            "Funding opportunity",
        )
    )


def opportunity_identity_from(row):
    if not isinstance(row, dict):
        return ""
    return str(
        first_value(
            row,
            [
                "opportunity_identity",
                "identity",
                "opportunity_id",
                "call_id",
                "id",
            ],
            "",
        )
        or ""
    )


# ---------------------------------------------------------------------
# Context: project + opportunity
# ---------------------------------------------------------------------
user_id = get_user_id()

project_filters = {"user_id": user_id} if user_id else {}
projects = safe_query(
    "projects",
    filters=project_filters,
    order=("created_at", True),
    limit=100,
)

if not projects:
    # Some older project schemas may not expose user_id in the same way.
    projects = safe_query("projects", order=("created_at", True), limit=100)

if not projects:
    st.error("Nu am găsit proiecte disponibile.")
    st.stop()

project_labels = [display_name(p) for p in projects]
selected_project_label = st.selectbox("Project", project_labels)
project = projects[project_labels.index(selected_project_label)]
project_id = str(project.get("id"))

# Prefer opportunity records linked to the project, but Stage 17 may use
# the most recent project data when identifiers differ between modules.
opportunities = []
for table in ("funding_opportunities", "opportunities"):
    opportunities = safe_query(
        table,
        filters={"project_id": project_id},
        order=("created_at", True),
        limit=100,
    )
    if opportunities:
        break

pack_latest = latest("submission_packs", project_id=project_id)
pack_opportunity_identity = opportunity_identity_from(pack_latest or {})

if opportunities:
    opp_labels = [opportunity_name(o) for o in opportunities]
    selected_opp_label = st.selectbox("Oportunitate", opp_labels)
    opportunity = opportunities[opp_labels.index(selected_opp_label)]
    opportunity_identity = opportunity_identity_from(opportunity)
else:
    opportunity = {}
    selected_opp_label = str(
        first_value(
            pack_latest or {},
            ["opportunity_name", "funding_opportunity"],
            "Funding opportunity",
        )
    )
    st.text_input("Oportunitate", value=selected_opp_label, disabled=True)
    opportunity_identity = pack_opportunity_identity

if not opportunity_identity:
    opportunity_identity = pack_opportunity_identity or selected_opp_label

if pack_opportunity_identity and opportunity_identity != pack_opportunity_identity:
    st.info(
        "Identificatorul oportunității diferă între module; pentru validarea finală "
        "se folosesc cele mai recente date disponibile pentru proiect."
    )


# ---------------------------------------------------------------------
# Load Stage 15–17 data
# ---------------------------------------------------------------------
submission_pack = latest(
    "submission_packs",
    project_id=project_id,
    opportunity_identity=opportunity_identity,
)
if not submission_pack:
    submission_pack = latest("submission_packs", project_id=project_id)

compliance = latest(
    "grant_compliance_checks",
    project_id=project_id,
    opportunity_identity=opportunity_identity,
)
if not compliance:
    compliance = latest("grant_compliance_checks", project_id=project_id)

readiness = latest(
    "submission_readiness_runs",
    project_id=project_id,
    opportunity_identity=opportunity_identity,
)
if not readiness:
    readiness = latest("submission_readiness_runs", project_id=project_id)

writer_sections = safe_query(
    "grant_writer_sections",
    filters={"project_id": project_id},
    order=("created_at", True),
    limit=200,
)

readiness_items = safe_query(
    "submission_readiness_items",
    filters={"project_id": project_id},
    order=("created_at", True),
    limit=500,
)

if not submission_pack:
    st.error(
        "Nu există încă un Submission Pack pentru proiect. "
        "Generează mai întâi Etapa 17."
    )
    st.stop()


# ---------------------------------------------------------------------
# Metrics / gate
# ---------------------------------------------------------------------
pack_status = str(
    first_value(submission_pack, ["pack_status", "status"], "BLOCKED")
).upper()

readiness_score = to_int(
    first_value(
        submission_pack,
        ["readiness_score", "readiness"],
        first_value(readiness or {}, ["score", "readiness_score", "overall_score"], 0),
    )
)

blocking_count = to_int(
    first_value(
        submission_pack,
        ["blocking_count", "blocker_count", "blocking_issues_count"],
        0,
    )
)

if readiness_items:
    unresolved_blockers = []
    for item in readiness_items:
        status = str(item.get("status") or "").strip().lower()
        is_blocking = bool(
            first_value(item, ["is_blocking", "blocking", "blocker"], False)
        )
        if not is_blocking:
            # Stage 16 also uses explicit "Blocked" status.
            is_blocking = status == "blocked"
        if is_blocking and status not in ("done", "completed", "resolved", "closed"):
            unresolved_blockers.append(item)
    blocking_count = max(blocking_count, len(unresolved_blockers))

compliance_score = to_int(
    first_value(compliance or {}, ["compliance_score", "score", "overall_score"], 0)
)

writer_texts = []
for section in writer_sections:
    for key in ("content", "section_content", "text", "body", "draft"):
        if section.get(key):
            writer_texts.append(str(section[key]))
            break

pack_texts = []
for key in (
    "proposal_text", "proposal", "content", "executive_summary",
    "summary", "final_text"
):
    if submission_pack.get(key):
        pack_texts.append(str(submission_pack[key]))

placeholder_values = find_placeholders("\n".join(writer_texts + pack_texts))
placeholder_count = len(placeholder_values)

checks = []

def add_check(name, passed, detail, blocking=True):
    checks.append(
        {
            "name": name,
            "passed": bool(passed),
            "detail": detail,
            "blocking": bool(blocking),
        }
    )

add_check(
    "Submission Pack există",
    bool(submission_pack),
    "Etapa 17 a generat și salvat pachetul.",
)

add_check(
    "Readiness fără blocante",
    blocking_count == 0,
    (
        "Nu există probleme blocante nerezolvate."
        if blocking_count == 0
        else f"Există {blocking_count} probleme blocante nerezolvate."
    ),
)

add_check(
    "Readiness score",
    readiness_score >= 80,
    f"Readiness curent: {readiness_score}/100. Prag intern pentru READY: 80/100.",
)

add_check(
    "Compliance",
    compliance_score >= 80,
    f"Compliance curent: {compliance_score}/100. Prag intern pentru READY: 80/100.",
)

add_check(
    "Placeholders",
    placeholder_count == 0,
    (
        "Nu au fost detectați placeholders."
        if placeholder_count == 0
        else f"Au fost detectate {placeholder_count} tipuri de placeholder."
    ),
)

# Five final checks, matching the 0/5 concept already shown in Stage 17.
final_check_defs = [
    ("Conținut final revizuit", "Am verificat versiunea finală a conținutului."),
    ("Eligibilitate confirmată", "Am validat eligibilitatea în documentația oficială a apelului."),
    ("Buget și cifre verificate", "Am verificat bugetul, sumele și coerența cifrelor."),
    ("Anexe/documente verificate", "Am verificat anexele și documentele obligatorii."),
    ("Date de depunere verificate", "Am verificat termenul, portalul și cerințele finale de depunere."),
]

st.subheader("Final Submission Gate")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Pack status", pack_status)
c2.metric("Readiness", f"{readiness_score}/100")
c3.metric("Compliance", f"{compliance_score}/100")
c4.metric("Blocante", blocking_count)

if blocking_count:
    st.error(f"DEPUNERE BLOCATĂ — există {blocking_count} probleme blocante nerezolvate.")
else:
    st.success("Nu există probleme blocante în checklistul de readiness.")

st.markdown("### Verificări automate")
for check in checks:
    icon = "✅" if check["passed"] else "❌"
    st.write(f"{icon} **{check['name']}** — {check['detail']}")

if placeholder_values:
    with st.expander("Placeholders detectați"):
        for placeholder in placeholder_values:
            st.write(f"- `{placeholder}`")

st.markdown("### 5 verificări finale")
manual_values = []
for idx, (title, help_text) in enumerate(final_check_defs, start=1):
    manual_values.append(
        st.checkbox(
            title,
            help=help_text,
            key=f"stage18_final_check_{project_id}_{idx}",
        )
    )

final_checks_done = sum(1 for value in manual_values if value)
st.progress(final_checks_done / 5)
st.caption(f"Final checks: {final_checks_done}/5")

automatic_blocking_failures = [
    check for check in checks if check["blocking"] and not check["passed"]
]
automatic_passed = len(checks) - len(automatic_blocking_failures)

# Score is intentionally conservative: automatic checks + five manual checks.
total_score_units = len(checks) + 5
passed_score_units = automatic_passed + final_checks_done
overall_score = round((passed_score_units / total_score_units) * 100)

if automatic_blocking_failures:
    validation_status = "blocked"
elif final_checks_done < 5:
    validation_status = "needs_review"
else:
    validation_status = "ready"

submission_blocked = validation_status != "ready"

warning_items = []
if final_checks_done < 5:
    warning_items.append(
        f"Mai sunt {5 - final_checks_done} verificări finale manuale neconfirmate."
    )

blocking_issues = [
    {
        "name": check["name"],
        "detail": check["detail"],
    }
    for check in automatic_blocking_failures
]

st.markdown("### Rezultat")
r1, r2, r3 = st.columns(3)
r1.metric("Final validation score", f"{overall_score}/100")
r2.metric("Status", validation_status.upper().replace("_", " "))
r3.metric("Final checks", f"{final_checks_done}/5")

if validation_status == "ready":
    st.success("READY — gate-ul final este trecut. Exportul final poate fi creat.")
elif validation_status == "needs_review":
    st.warning("NEEDS REVIEW — finalizează cele 5 verificări manuale.")
else:
    st.error("BLOCKED — exportul final rămâne blocat până la rezolvarea problemelor.")


# ---------------------------------------------------------------------
# Save validation
# ---------------------------------------------------------------------
if st.button("🚦 Rulează și salvează validarea finală", use_container_width=True):
    if not user_id:
        st.error(
            "Nu am putut determina utilizatorul autentificat. "
            "Reîncarcă aplicația după autentificare."
        )
    else:
        payload = {
            "user_id": user_id,
            "project_id": project_id,
            "opportunity_identity": str(opportunity_identity),
            "submission_pack_id": submission_pack.get("id"),
            "validation_status": validation_status,
            "overall_score": overall_score,
            "submission_blocked": submission_blocked,
            "blocking_count": len(blocking_issues),
            "warning_count": len(warning_items),
            "placeholder_count": placeholder_count,
            "checks": checks + [
                {
                    "name": title,
                    "passed": manual_values[i],
                    "detail": help_text,
                    "blocking": False,
                }
                for i, (title, help_text) in enumerate(final_check_defs)
            ],
            "blocking_issues": blocking_issues,
            "warnings": warning_items,
            "placeholders": placeholder_values,
            "summary": (
                f"Final validation: {validation_status.upper()}; "
                f"score {overall_score}/100; "
                f"{len(blocking_issues)} blocking checks; "
                f"{final_checks_done}/5 final checks."
            ),
        }
        try:
            response = (
                supabase.table("final_submission_validations")
                .insert(payload)
                .execute()
            )
            saved = rows(response)
            if saved:
                st.success(
                    f"Validarea finală a fost salvată. ID: {str(saved[0].get('id'))[:8]}"
                )
            else:
                st.success("Validarea finală a fost salvată.")
        except Exception as exc:
            st.error(f"Nu am putut salva validarea finală: {exc}")


# ---------------------------------------------------------------------
# Export center
# ---------------------------------------------------------------------
st.divider()
st.subheader("Export Center")

export_manifest = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "project_id": project_id,
    "project_name": first_value(project, ["name", "project_name", "title"], "Project"),
    "opportunity_identity": str(opportunity_identity),
    "submission_pack_id": submission_pack.get("id"),
    "validation_status": validation_status,
    "overall_score": overall_score,
    "readiness_score": readiness_score,
    "compliance_score": compliance_score,
    "blocking_count": len(blocking_issues),
    "placeholder_count": placeholder_count,
    "final_checks": {
        final_check_defs[i][0]: manual_values[i] for i in range(5)
    },
    "checks": checks,
}

project_name = re.sub(
    r"[^A-Za-z0-9_-]+",
    "_",
    str(first_value(project, ["name", "project_name", "title"], "project")),
).strip("_") or "project"

json_bytes = json.dumps(
    export_manifest,
    ensure_ascii=False,
    indent=2,
).encode("utf-8")

if validation_status == "ready":
    st.download_button(
        "⬇️ Descarcă manifestul final JSON",
        data=json_bytes,
        file_name=f"{project_name}_final_submission_manifest.json",
        mime="application/json",
        use_container_width=True,
    )

    if st.button("📦 Înregistrează exportul final", use_container_width=True):
        if not user_id:
            st.error("Nu am putut determina utilizatorul autentificat.")
        else:
            validation = latest(
                "final_submission_validations",
                project_id=project_id,
                opportunity_identity=opportunity_identity,
            )
            payload = {
                "user_id": user_id,
                "project_id": project_id,
                "opportunity_identity": str(opportunity_identity),
                "submission_pack_id": submission_pack.get("id"),
                "validation_id": validation.get("id") if validation else None,
                "export_status": "exported",
                "export_format": "json",
                "file_name": f"{project_name}_final_submission_manifest.json",
                "export_manifest": export_manifest,
            }
            try:
                response = (
                    supabase.table("submission_exports")
                    .insert(payload)
                    .execute()
                )
                saved = rows(response)
                if saved:
                    st.success(
                        f"Exportul a fost înregistrat. ID: {str(saved[0].get('id'))[:8]}"
                    )
                else:
                    st.success("Exportul a fost înregistrat.")
            except Exception as exc:
                st.error(f"Nu am putut înregistra exportul: {exc}")
else:
    st.info(
        "Exportul final este dezactivat. Devine disponibil numai când "
        "validarea ajunge la READY."
    )

st.caption(
    "Etapa 18 este un gate intern de calitate. Eligibilitatea, deadline-ul și "
    "cerințele oficiale trebuie validate în documentația oficială a apelului."
)
