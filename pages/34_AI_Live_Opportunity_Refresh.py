import os
import json
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone, date
from typing import Any

import streamlit as st
from supabase import create_client

st.set_page_config(
    page_title="AI Live Opportunity Refresh",
    page_icon="🔄",
    layout="wide",
)

st.title("🔄 Etapa 34 — Live Opportunity Refresh & Clean Rebuild")
st.caption(
    "Caută oportunități noi, elimină rezultatele expirate înainte de salvare "
    "și reconstruiește lista de oportunități cu metadate suficiente pentru Etapa 33."
)


# ---------------------------------------------------------------------
# Supabase / auth
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


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def parse_date(value):
    if not value:
        return None
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except Exception:
        pass
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except Exception:
            pass
    return None


def normalize_text(value):
    return str(value or "").strip()


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
# Project
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
# Query controls
# ---------------------------------------------------------------------
default_query = ""
if selected_project:
    default_query = " ".join(
        x for x in [
            str(selected_project.get("name") or selected_project.get("title") or ""),
            str(selected_project.get("description") or ""),
        ] if x
    )[:500]

query_text = st.text_input(
    "Căutare oportunități",
    value=default_query,
    placeholder="Ex.: agriculture renewable energy batteries AI greenhouse Europe",
)

max_results = st.slider("Număr maxim de rezultate brute", 10, 100, 50, 10)

st.info(
    "Etapa 34 filtrează înainte de salvare. O oportunitate fără identitate sau fără deadline "
    "nu intră în lista curată. Un deadline în trecut este respins automat."
)


# ---------------------------------------------------------------------
# EU Funding & Tenders search
# ---------------------------------------------------------------------
def _multipart_form_data(fields: dict):
    """
    Build multipart/form-data body without adding an extra dependency.
    EU Funding & Tenders SEARCH API expects POST with query JSON as form data.
    """
    boundary = "----GrantAIEuropeBoundary7MA4YWxkTrZu0gW"
    parts = []

    for name, value in fields.items():
        if value is None:
            continue

        parts.append(f"--{boundary}\r\n")
        parts.append(
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
        )
        parts.append(str(value))
        parts.append("\r\n")

    parts.append(f"--{boundary}--\r\n")
    body = "".join(parts).encode("utf-8")
    content_type = f"multipart/form-data; boundary={boundary}"
    return body, content_type


def fetch_eu_opportunities(search_text: str, limit: int):
    """
    Official EU Funding & Tenders SEARCH API.

    Important:
    - endpoint requires HTTPS POST, not GET;
    - search criteria are sent as JSON in multipart/form-data field `query`;
    - request is restricted to grant/topic types and non-closed statuses;
    - free-text keywords are sent through the documented `text` parameter.
    """
    query_text_clean = normalize_text(search_text) or "agriculture energy innovation"

    endpoint = "https://api.tech.ec.europa.eu/search-api/prod/rest/search"

    # Official API reference codes:
    # type 1,2,8 = grant/topic related records
    # status 31094501 / 31094502 = non-closed states used by the Portal API examples.
    query_data = {
        "bool": {
            "must": [
                {
                    "terms": {
                        "type": ["1", "2", "8"]
                    }
                },
                {
                    "terms": {
                        "status": ["31094501", "31094502"]
                    }
                }
            ]
        }
    }

    url = (
        endpoint
        + "?apiKey=SEDIA"
        + "&text=" + urllib.parse.quote(query_text_clean)
        + "&pageSize=" + str(int(limit))
        + "&pageNumber=1"
        + "&language=en"
    )

    body, content_type = _multipart_form_data({
        "query": json.dumps(query_data, ensure_ascii=False),
    })

    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "User-Agent": "GrantAI-Europe/1.0",
            "Accept": "application/json",
            "Content-Type": content_type,
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=35) as resp:
            response_body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"EU Funding & Tenders API HTTP {exc.code}: "
            f"{details[:1000] or exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Nu s-a putut conecta la EU Funding & Tenders API: {exc.reason}"
        ) from exc

    try:
        data = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "EU Funding & Tenders API nu a returnat JSON valid. "
            f"Răspuns: {response_body[:1000]}"
        ) from exc

    return data, url


# ---------------------------------------------------------------------
# Recursive extraction
# ---------------------------------------------------------------------
def walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)


def pick(d: dict, names):
    lowered = {str(k).lower(): v for k, v in d.items()}
    for name in names:
        if name.lower() in lowered:
            value = lowered[name.lower()]
            if value not in (None, "", [], {}):
                return value
    return None


def looks_like_opportunity(d: dict):
    keys = {str(k).lower() for k in d.keys()}
    useful = {
        "title", "deadline", "identifier", "reference", "id",
        "status", "openingdate", "opening_date", "programme",
        "topicid", "topic_id", "callidentifier"
    }
    return len(keys & useful) >= 2


def extract_candidates(raw):
    candidates = []

    for d in walk(raw):
        if not isinstance(d, dict) or not looks_like_opportunity(d):
            continue

        title = pick(d, ["title", "name", "topicTitle", "callTitle"])
        identity = pick(d, [
            "identifier", "reference", "topicId", "topic_id",
            "callIdentifier", "id"
        ])
        deadline = pick(d, [
            "deadline", "deadlineDate", "submissionDeadline",
            "closingDate", "endDate"
        ])
        opening = pick(d, [
            "openingDate", "opening_date", "startDate", "publicationDate"
        ])
        status = pick(d, ["status", "callStatus", "topicStatus"])
        programme = pick(d, ["programme", "program", "programmeName"])
        region = pick(d, ["country", "region", "countryOrRegion"])
        description = pick(d, ["description", "summary", "abstract"])
        source_url = pick(d, ["url", "officialUrl", "sourceUrl"])

        if not identity and title:
            identity = str(title)[:180]

        candidate = {
            "identity": normalize_text(identity),
            "title": normalize_text(title),
            "deadline": normalize_text(deadline),
            "opening_date": normalize_text(opening),
            "status": normalize_text(status),
            "programme": normalize_text(programme),
            "country_or_region": normalize_text(region),
            "description": normalize_text(description),
            "official_url": normalize_text(source_url),
            "raw": d,
        }

        if candidate["identity"] or candidate["title"]:
            candidates.append(candidate)

    # Deduplicate.
    seen = set()
    unique = []
    for c in candidates:
        key = c["identity"] or c["title"]
        if key in seen:
            continue
        seen.add(key)
        unique.append(c)

    return unique


# ---------------------------------------------------------------------
# Validation / clean rebuild
# ---------------------------------------------------------------------
def evaluate_candidate(c):
    deadline_date = parse_date(c.get("deadline"))
    identity = normalize_text(c.get("identity"))
    title = normalize_text(c.get("title"))
    source_url = normalize_text(c.get("official_url"))

    if not identity:
        return "Rejected missing identity", "Lipsește identitatea oportunității.", deadline_date

    if not deadline_date:
        return "Rejected missing deadline", "Deadline-ul nu poate fi determinat.", None

    if deadline_date < date.today():
        return (
            "Rejected expired",
            f"Deadline {deadline_date.isoformat()} este în trecut.",
            deadline_date,
        )

    metadata_points = 0
    metadata_points += 25 if identity else 0
    metadata_points += 20 if title else 0
    metadata_points += 25 if deadline_date else 0
    metadata_points += 10 if source_url else 0
    metadata_points += 10 if c.get("programme") else 0
    metadata_points += 10 if c.get("status") else 0

    if metadata_points < 60:
        return (
            "Rejected invalid metadata",
            f"Calitatea metadatelor este insuficientă ({metadata_points}/100).",
            deadline_date,
        )

    return "Candidate", "", deadline_date


def build_opportunities_payload(c, deadline_date):
    # Store in the exact schema used by public.opportunities:
    # identity + data JSONB.
    deadline_iso = (
        datetime.combine(
            deadline_date,
            datetime.min.time(),
            tzinfo=timezone.utc,
        ).isoformat()
        if deadline_date else ""
    )

    days_left = (deadline_date - date.today()).days if deadline_date else None

    data = {
        "id": c["identity"],
        "title": c["title"],
        "deadline": deadline_iso,
        "days_left": days_left,
        "programme": c.get("programme") or "",
        "description": c.get("description") or c["title"],
        "official_url": c.get("official_url") or "",
        "opening_date": c.get("opening_date") or "",
        "status": c.get("status") or "",
        "country_or_region": c.get("country_or_region") or "",
        "deadline_label": "Deschis" if days_left is not None and days_left >= 0 else "Închis",
        "metadata_quality": 100,
        "refresh_source": "Etapa 34",
        "refreshed_at": now_iso(),
    }

    return {
        "user_id": user_id,
        "identity": c["identity"],
        "data": data,
        "updated_at": now_iso(),
    }


# ---------------------------------------------------------------------
# Run refresh
# ---------------------------------------------------------------------
if st.button(
    "🔄 Rulează Live Opportunity Refresh",
    type="primary",
    use_container_width=True,
):
    if not query_text.strip():
        st.warning("Introdu o căutare.")
    else:
        with st.spinner("Caut oportunități actuale și filtrez rezultatele..."):
            try:
                run_insert = (
                    supabase.table("opportunity_refresh_runs")
                    .insert({
                        "user_id": user_id,
                        "project_id": project_id,
                        "query_text": query_text.strip(),
                        "run_status": "Running",
                        "started_at": now_iso(),
                        "updated_at": now_iso(),
                    })
                    .execute()
                ).data or []

                if not run_insert:
                    raise RuntimeError("Nu am putut crea refresh run.")

                run_id = str(run_insert[0]["id"])

                raw, api_url = fetch_eu_opportunities(query_text, max_results)
                candidates = extract_candidates(raw)

                stats = {
                    "total_fetched": len(candidates),
                    "valid_metadata": 0,
                    "rejected_expired": 0,
                    "rejected_missing_deadline": 0,
                    "rejected_missing_identity": 0,
                    "saved_opportunities": 0,
                }

                clean_candidates = []

                for c in candidates:
                    status, reason, deadline_date = evaluate_candidate(c)

                    if status == "Candidate":
                        stats["valid_metadata"] += 1
                        clean_candidates.append((c, deadline_date))
                    elif status == "Rejected expired":
                        stats["rejected_expired"] += 1
                    elif status == "Rejected missing deadline":
                        stats["rejected_missing_deadline"] += 1
                    elif status == "Rejected missing identity":
                        stats["rejected_missing_identity"] += 1

                    supabase.table("opportunity_refresh_items").insert({
                        "user_id": user_id,
                        "project_id": project_id,
                        "refresh_run_id": run_id,
                        "opportunity_identity": c.get("identity") or None,
                        "opportunity_title": c.get("title") or None,
                        "programme": c.get("programme") or None,
                        "country_or_region": c.get("country_or_region") or None,
                        "opening_date": c.get("opening_date") or None,
                        "deadline_date": (
                            datetime.combine(
                                deadline_date,
                                datetime.min.time(),
                                tzinfo=timezone.utc,
                            ).isoformat()
                            if deadline_date else None
                        ),
                        "source_status": c.get("status") or None,
                        "source_url": c.get("official_url") or None,
                        "metadata_quality": 100 if status == "Candidate" else 0,
                        "refresh_status": status,
                        "rejection_reason": reason or None,
                        "raw_data": c.get("raw") or {},
                        "updated_at": now_iso(),
                    }).execute()

                # Upsert only clean, non-expired opportunities.
                for c, deadline_date in clean_candidates:
                    payload = build_opportunities_payload(c, deadline_date)

                    existing = (
                        supabase.table("opportunities")
                        .select("id")
                        .eq("user_id", user_id)
                        .eq("identity", c["identity"])
                        .limit(1)
                        .execute()
                    ).data or []

                    if existing:
                        (
                            supabase.table("opportunities")
                            .update({
                                "data": payload["data"],
                                "updated_at": now_iso(),
                            })
                            .eq("id", existing[0]["id"])
                            .eq("user_id", user_id)
                            .execute()
                        )
                    else:
                        supabase.table("opportunities").insert(payload).execute()

                    stats["saved_opportunities"] += 1

                run_status = (
                    "Completed"
                    if stats["saved_opportunities"] > 0
                    else "Needs attention"
                )

                (
                    supabase.table("opportunity_refresh_runs")
                    .update({
                        **stats,
                        "run_status": run_status,
                        "summary": {
                            "stage": 34,
                            "api_url": api_url,
                            "rule": (
                                "Only future-deadline opportunities with sufficient metadata "
                                "are saved to public.opportunities."
                            ),
                        },
                        "completed_at": now_iso(),
                        "updated_at": now_iso(),
                    })
                    .eq("id", run_id)
                    .eq("user_id", user_id)
                    .execute()
                )

                st.success(
                    f"Refresh finalizat: {stats['saved_opportunities']} oportunități curate salvate."
                )
                st.rerun()

            except Exception as exc:
                st.error(f"Etapa 34 nu a putut finaliza refresh-ul: {exc}")


# ---------------------------------------------------------------------
# Latest run
# ---------------------------------------------------------------------
filters = {"user_id": user_id}
if project_id:
    filters["project_id"] = project_id

history = rows(
    "opportunity_refresh_runs",
    filters,
    "created_at",
    100,
)

latest = history[0] if history else None

st.divider()
st.subheader("Refresh Result")

if latest:
    a, b, c, d = st.columns(4)
    a.metric("Fetched", int(latest.get("total_fetched") or 0))
    b.metric("Valid metadata", int(latest.get("valid_metadata") or 0))
    c.metric("Expired rejected", int(latest.get("rejected_expired") or 0))
    d.metric("Saved", int(latest.get("saved_opportunities") or 0))

    st.write(f"**Run status:** {latest.get('run_status')}")

    items = rows(
        "opportunity_refresh_items",
        {
            "user_id": user_id,
            "refresh_run_id": latest["id"],
        },
        "created_at",
        500,
    )

    if items:
        st.dataframe(
            [
                {
                    "Title": i.get("opportunity_title"),
                    "Identity": i.get("opportunity_identity"),
                    "Deadline": i.get("deadline_date"),
                    "Status": i.get("refresh_status"),
                    "Reason": i.get("rejection_reason"),
                    "Source status": i.get("source_status"),
                }
                for i in items
            ],
            use_container_width=True,
            hide_index=True,
        )

        saved_items = [
            i for i in items
            if i.get("refresh_status") == "Candidate"
        ]

        if saved_items:
            st.success(
                "Aceste oportunități au trecut filtrul Etapei 34. "
                "Rulează apoi Etapa 33 pentru validarea finală înainte de scoring."
            )
        else:
            st.warning(
                "Nicio oportunitate nu a trecut filtrul. "
                "Schimbă termenii de căutare sau verifică sursa/API-ul."
            )
else:
    st.caption("Nu există încă un refresh Etapa 34.")


with st.expander("Istoric Etapa 34"):
    if history:
        st.dataframe(
            [
                {
                    "created_at": r.get("created_at"),
                    "query": r.get("query_text"),
                    "fetched": r.get("total_fetched"),
                    "valid": r.get("valid_metadata"),
                    "expired": r.get("rejected_expired"),
                    "missing_deadline": r.get("rejected_missing_deadline"),
                    "saved": r.get("saved_opportunities"),
                    "status": r.get("run_status"),
                }
                for r in history
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("Nu există rulări.")


st.caption(
    "Etapa 34 nu șterge istoricul oportunităților expirate. "
    "Ea adaugă/actualizează numai candidații curați; Etapa 33 decide ulterior dacă aceștia sunt VALID."
)
