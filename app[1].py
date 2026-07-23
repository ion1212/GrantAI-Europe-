import io
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import pandas as pd
import requests
import streamlit as st
from dateutil import parser as date_parser
from docx import Document
from openai import OpenAI

st.set_page_config(page_title="GrantAI Europe", page_icon="🇪🇺", layout="wide")

DB_PATH = Path("grantai.db")
EC_SEARCH_API = "https://api.tech.ec.europa.eu/search-api/prod/rest/search"
EC_API_KEY = "SEDIA"
PORTAL_BASE = "https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/opportunities/topic-search"

DEFAULT_ORG = {
    "legal_name": "II Ciobotaru Viorel Razvan Ionut",
    "country": "Romania",
    "organisation_type": "SME / întreprindere individuală",
    "pic": "",
    "caen": "0111",
    "turnover_eur": 0,
    "staff": 0,
    "capabilities": [
        "agriculture",
        "smart greenhouse",
        "renewable energy",
        "battery storage",
        "AI automation",
    ],
    "past_projects": [],
}

DEFAULT_PROJECTS = [
    {
        "name": "GreenRise",
        "summary": "Seră inteligentă cu energie regenerabilă, baterii și automatizare AI.",
        "keywords": ["agriculture", "greenhouse", "energy", "battery", "AI", "rural", "agrifood"],
        "target_budget_eur": 0,
        "preferred_role": "beneficiary",
    }
]

def get_secret(name: str, default: str = "") -> str:
    try:
        return str(st.secrets.get(name, default))
    except Exception:
        return os.getenv(name, default)

def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> None:
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS organisation (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            data TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            identity TEXT UNIQUE NOT NULL,
            data TEXT NOT NULL,
            saved_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            opportunity_identity TEXT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            opportunity_identity TEXT,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sync_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL,
            result_count INTEGER NOT NULL,
            run_at TEXT NOT NULL
        );
        """)

        now = datetime.now(timezone.utc).isoformat()

        if conn.execute("SELECT COUNT(*) FROM organisation").fetchone()[0] == 0:
            conn.execute(
                "INSERT INTO organisation (id, data, updated_at) VALUES (1, ?, ?)",
                (json.dumps(DEFAULT_ORG, ensure_ascii=False), now),
            )

        if conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0:
            for project in DEFAULT_PROJECTS:
                conn.execute(
                    "INSERT INTO projects (name, data, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (project["name"], json.dumps(project, ensure_ascii=False), now, now),
                )

def load_org() -> dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT data FROM organisation WHERE id = 1").fetchone()
    return json.loads(row["data"])

def save_org(org: dict[str, Any]) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE organisation SET data = ?, updated_at = ? WHERE id = 1",
            (json.dumps(org, ensure_ascii=False), datetime.now(timezone.utc).isoformat()),
        )

def list_projects() -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("SELECT id, name, data FROM projects ORDER BY updated_at DESC").fetchall()
    return [{"id": row["id"], **json.loads(row["data"])} for row in rows]

def save_project(project_id: int | None, project: dict[str, Any]) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with db() as conn:
        if project_id is None:
            cursor = conn.execute(
                "INSERT INTO projects (name, data, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (project["name"], json.dumps(project, ensure_ascii=False), now, now),
            )
            return int(cursor.lastrowid)

        conn.execute(
            "UPDATE projects SET name = ?, data = ?, updated_at = ? WHERE id = ?",
            (project["name"], json.dumps(project, ensure_ascii=False), now, project_id),
        )
        return project_id

def delete_project(project_id: int) -> None:
    with db() as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))

def save_opportunity(item: dict[str, Any]) -> None:
    identity = item.get("id") or item.get("reference") or item.get("title")
    with db() as conn:
        conn.execute(
            """
            INSERT INTO opportunities (identity, data, saved_at)
            VALUES (?, ?, ?)
            ON CONFLICT(identity) DO UPDATE SET data = excluded.data, saved_at = excluded.saved_at
            """,
            (
                identity,
                json.dumps(item, ensure_ascii=False),
                datetime.now(timezone.utc).isoformat(),
            ),
        )

def list_opportunities() -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("SELECT data FROM opportunities ORDER BY saved_at DESC").fetchall()
    return [json.loads(row["data"]) for row in rows]

def save_analysis(project_id: int, opportunity: dict[str, Any], content: str) -> None:
    identity = opportunity.get("id") or opportunity.get("reference") or opportunity.get("title")
    with db() as conn:
        conn.execute(
            """
            INSERT INTO analyses (project_id, opportunity_identity, content, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (project_id, identity, content, datetime.now(timezone.utc).isoformat()),
        )

def list_analyses() -> list[sqlite3.Row]:
    with db() as conn:
        return conn.execute(
            "SELECT * FROM analyses ORDER BY created_at DESC"
        ).fetchall()

def save_document(project_id: int, opportunity: dict[str, Any], title: str, content: str) -> None:
    identity = opportunity.get("id") or opportunity.get("reference") or opportunity.get("title")
    with db() as conn:
        conn.execute(
            """
            INSERT INTO documents (project_id, opportunity_identity, title, content, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (project_id, identity, title, content, datetime.now(timezone.utc).isoformat()),
        )

def list_documents() -> list[sqlite3.Row]:
    with db() as conn:
        return conn.execute(
            "SELECT * FROM documents ORDER BY created_at DESC"
        ).fetchall()

def record_sync(query: str, count: int) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO sync_runs (query, result_count, run_at) VALUES (?, ?, ?)",
            (query, count, datetime.now(timezone.utc).isoformat()),
        )

def last_sync() -> sqlite3.Row | None:
    with db() as conn:
        return conn.execute(
            "SELECT * FROM sync_runs ORDER BY run_at DESC LIMIT 1"
        ).fetchone()

def clean_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(x) for x in value)
    if isinstance(value, dict):
        return ", ".join(str(x) for x in value.values())
    return str(value)

def official_search_url(reference: str, title: str) -> str:
    query = reference.strip() or title.strip()
    return f"{PORTAL_BASE}?keywords={quote_plus(query)}"

def normalize_result(item: dict[str, Any]) -> dict[str, Any]:
    source = item.get("_source", item)
    reference = clean_value(source.get("reference"))
    title = clean_value(source.get("title"))
    return {
        "id": clean_value(source.get("identifier") or source.get("callccm2Id") or reference),
        "reference": reference,
        "title": title,
        "status": clean_value(source.get("status")),
        "programme": clean_value(source.get("frameworkProgramme") or source.get("programme") or source.get("caName")),
        "action_type": clean_value(source.get("typesOfAction") or source.get("typeOfAction")),
        "opening_date": clean_value(source.get("startDate")),
        "deadline": clean_value(source.get("deadlineDate") or source.get("deadline")),
        "description": clean_value(source.get("description")),
        "official_url": official_search_url(reference, title),
    }

@st.cache_data(ttl=1800, show_spinner=False)
def search_eu_calls(keyword: str, page_size: int) -> list[dict[str, Any]]:
    query = {
        "bool": {
            "must": [
                {"terms": {"type": ["1", "2", "8"]}},
                {"term": {"programmePeriod": "2021 - 2027"}},
            ]
        }
    }
    params = {
        "apiKey": EC_API_KEY,
        "text": keyword.strip() or "***",
        "pageSize": str(page_size),
        "pageNumber": "1",
    }
    files = {
        "query": ("query.json", json.dumps(query), "application/json"),
        "sort": ("sort.json", json.dumps({"order": "ASC", "field": "deadlineDate"}), "application/json"),
        "languages": ("languages.json", json.dumps(["en"]), "application/json"),
        "displayFields": ("fields.json", json.dumps([
            "type", "identifier", "reference", "callccm2Id", "title", "status",
            "caName", "startDate", "deadlineDate", "frameworkProgramme",
            "typesOfAction", "description"
        ]), "application/json"),
    }
    headers = {
        "Accept": "application/json",
        "Origin": "https://ec.europa.eu",
        "Referer": "https://ec.europa.eu/",
        "X-Requested-With": "XMLHttpRequest",
    }
    response = requests.post(
        EC_SEARCH_API,
        params=params,
        files=files,
        headers=headers,
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    candidates = (
        payload.get("results")
        or payload.get("hits", {}).get("hits")
        or payload.get("response", {}).get("docs")
        or []
    )
    return [normalize_result(x) for x in candidates]

def overlap_score(words: list[str], text: str) -> float:
    if not words:
        return 0.0
    corpus = text.lower()
    hits = sum(1 for word in words if word.strip().lower() in corpus)
    return min(100.0, hits / len(words) * 100.0)

def deadline_info(value: str) -> tuple[float, str, int | None]:
    if not value:
        return 45.0, "Necunoscut", None
    try:
        deadline = date_parser.parse(value)
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        days = (deadline - datetime.now(timezone.utc)).days
        if days < 0:
            return 0.0, "Închis", days
        if days < 14:
            return 15.0, f"{days} zile", days
        if days < 30:
            return 35.0, f"{days} zile", days
        if days < 60:
            return 60.0, f"{days} zile", days
        if days < 120:
            return 82.0, f"{days} zile", days
        return 100.0, f"{days} zile", days
    except Exception:
        return 40.0, "Format necunoscut", None

def score_opportunity(call: dict[str, Any], org: dict[str, Any], project: dict[str, Any]) -> dict[str, Any]:
    corpus = " ".join([
        call.get("title", ""),
        call.get("description", ""),
        call.get("programme", ""),
        call.get("action_type", ""),
    ])
    thematic = overlap_score(project["keywords"], corpus)
    capability = overlap_score(org["capabilities"], corpus)
    timing, deadline_label, days = deadline_info(call.get("deadline", ""))
    evidence = 80.0 if call.get("description") else 45.0
    total = thematic * .38 + capability * .24 + timing * .14 + 70 * .16 + evidence * .08
    return {
        **call,
        "score": round(max(0, min(100, total)), 1),
        "thematic_fit": round(thematic, 1),
        "capability_fit": round(capability, 1),
        "deadline_label": deadline_label,
        "days_left": days,
    }

def ai_generate(task: str, opportunity: dict[str, Any], org: dict[str, Any], project: dict[str, Any]) -> str:
    key = get_secret("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("Lipsește OPENAI_API_KEY în Streamlit Secrets.")
    model = get_secret("OPENAI_MODEL", "gpt-4.1-mini")
    client = OpenAI(api_key=key)
    response = client.responses.create(
        model=model,
        instructions=(
            "Ești consultant senior și evaluator pentru finanțări europene. "
            "Scrie în română. Nu inventa condiții, parteneri, bugete, TRL, "
            "certificări, experiență sau rezultate. Pentru orice lipsă scrie [DE COMPLETAT]. "
            "Separă faptele confirmate de ipoteze și riscuri."
        ),
        input=f"""
SARCINĂ:
{task}

ORGANIZAȚIE:
{json.dumps(org, ensure_ascii=False, indent=2)}

PROIECT:
{json.dumps(project, ensure_ascii=False, indent=2)}

APEL:
{json.dumps(opportunity, ensure_ascii=False, indent=2)}
""",
    )
    return response.output_text

def markdown_to_docx(title: str, content: str) -> bytes:
    doc = Document()
    doc.add_heading(title, level=0)
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            doc.add_paragraph("")
        elif line.startswith("### "):
            doc.add_heading(line[4:], level=3)
        elif line.startswith("## "):
            doc.add_heading(line[3:], level=2)
        elif line.startswith("# "):
            doc.add_heading(line[2:], level=1)
        elif line.startswith("- "):
            doc.add_paragraph(line[2:], style="List Bullet")
        else:
            doc.add_paragraph(line)
    stream = io.BytesIO()
    doc.save(stream)
    return stream.getvalue()

init_db()
org = load_org()
projects = list_projects()

if not projects:
    st.error("Nu există proiecte în baza de date.")
    st.stop()

project_options = {f'{p["name"]} (ID {p["id"]})': p for p in projects}
selected_label = st.sidebar.selectbox("Proiect activ", list(project_options.keys()))
active_project = project_options[selected_label]
active_project_id = int(active_project["id"])

st.sidebar.caption("Datele sunt salvate în SQLite.")
if st.sidebar.button("Reîncarcă datele"):
    st.rerun()

st.title("🇪🇺 GrantAI Europe — Etapa 6")
st.caption("Bază de date, multi-proiect și istoric permanent")

tabs = st.tabs([
    "Dashboard",
    "Organizație",
    "Proiecte",
    "Funding Portal",
    "Selectate",
    "Analiză AI",
    "Generator",
    "Istoric",
])
tab_dashboard, tab_org, tab_projects, tab_portal, tab_saved, tab_analysis, tab_generator, tab_history = tabs

with tab_dashboard:
    opportunities = list_opportunities()
    documents = list_documents()
    analyses = list_analyses()
    sync = last_sync()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Proiecte", len(projects))
    c2.metric("Oportunități", len(opportunities))
    c3.metric("Analize", len(analyses))
    c4.metric("Documente", len(documents))

    st.write(f'**Proiect activ:** {active_project["name"]}')
    if sync:
        st.write(f'**Ultima sincronizare:** {sync["run_at"]} — {sync["result_count"]} rezultate')
    else:
        st.info("Nu există încă sincronizări înregistrate.")

with tab_org:
    st.subheader("Organizație")
    col1, col2 = st.columns(2)
    org["legal_name"] = col1.text_input("Denumire legală", org["legal_name"])
    org["country"] = col2.text_input("Țară", org["country"])
    org["organisation_type"] = col1.text_input("Tip organizație", org["organisation_type"])
    org["pic"] = col2.text_input("PIC", org["pic"])
    org["caen"] = col1.text_input("CAEN", org["caen"])
    org["staff"] = col2.number_input("Angajați", min_value=0, value=int(org["staff"]))
    org["turnover_eur"] = col1.number_input("Cifră de afaceri (€)", min_value=0, value=int(org["turnover_eur"]))
    org["capabilities"] = [
        x.strip() for x in st.text_area(
            "Capabilități",
            ", ".join(org["capabilities"]),
        ).split(",") if x.strip()
    ]
    org["past_projects"] = [
        x.strip() for x in st.text_area(
            "Proiecte anterioare",
            "\n".join(org["past_projects"]),
        ).splitlines() if x.strip()
    ]
    if st.button("Salvează organizația", type="primary"):
        save_org(org)
        st.success("Organizația a fost salvată permanent.")

with tab_projects:
    st.subheader("Gestionare proiecte")
    mode = st.radio("Acțiune", ["Editează proiectul activ", "Creează proiect nou"])

    if mode == "Editează proiectul activ":
        project = dict(active_project)
        project.pop("id", None)
        project_id = active_project_id
    else:
        project = {
            "name": "",
            "summary": "",
            "keywords": [],
            "target_budget_eur": 0,
            "preferred_role": "beneficiary",
        }
        project_id = None

    project["name"] = st.text_input("Nume proiect", project["name"])
    project["summary"] = st.text_area("Rezumat", project["summary"], height=140)
    project["keywords"] = [
        x.strip() for x in st.text_input(
            "Cuvinte-cheie",
            ", ".join(project["keywords"]),
        ).split(",") if x.strip()
    ]
    project["target_budget_eur"] = st.number_input(
        "Buget țintă (€)",
        min_value=0,
        value=int(project["target_budget_eur"]),
    )
    roles = ["beneficiary", "partner", "coordinator", "subcontractor"]
    current_role = project.get("preferred_role", "beneficiary")
    project["preferred_role"] = st.selectbox(
        "Rol preferat",
        roles,
        index=roles.index(current_role) if current_role in roles else 0,
    )

    if st.button("Salvează proiectul", type="primary"):
        if not project["name"].strip():
            st.error("Numele proiectului este obligatoriu.")
        else:
            saved_id = save_project(project_id, project)
            st.success(f"Proiect salvat cu ID {saved_id}.")
            st.rerun()

    if mode == "Editează proiectul activ" and len(projects) > 1:
        if st.button("Șterge proiectul activ"):
            delete_project(active_project_id)
            st.success("Proiect șters.")
            st.rerun()

with tab_portal:
    st.subheader("Funding & Tenders")
    c1, c2 = st.columns([3, 1])
    query = c1.text_input(
        "Căutare",
        " ".join(active_project["keywords"]),
    )
    limit = c2.selectbox("Rezultate", [20, 50, 100], index=1)

    if st.button("Sincronizează", type="primary", use_container_width=True):
        with st.spinner("Preiau apelurile..."):
            try:
                raw = search_eu_calls(query, limit)
                scored = [score_opportunity(x, org, active_project) for x in raw]
                scored.sort(key=lambda x: x["score"], reverse=True)
                st.session_state["portal_results"] = scored
                record_sync(query, len(scored))
                st.success(f"Au fost preluate {len(scored)} rezultate.")
            except Exception as exc:
                st.error(f"Sincronizarea a eșuat: {exc}")

    results = st.session_state.get("portal_results", [])
    if results:
        st.dataframe(pd.DataFrame([{
            "Scor": x["score"],
            "Referință": x["reference"],
            "Titlu": x["title"],
            "Program": x["programme"],
            "Deadline": x["deadline"],
        } for x in results]), use_container_width=True, hide_index=True)

        idx = st.selectbox(
            "Selectează apelul",
            range(len(results)),
            format_func=lambda i: f'{results[i]["score"]}% — {results[i]["reference"]} — {results[i]["title"]}',
        )
        selected = results[idx]
        st.write(f'**Program:** {selected["programme"] or "N/A"}')
        st.write(f'**Deadline:** {selected["deadline"] or "N/A"}')
        st.link_button("Deschide portalul oficial", selected["official_url"])

        if st.button("Salvează oportunitatea"):
            save_opportunity(selected)
            st.success("Oportunitatea a fost salvată permanent.")

with tab_saved:
    opportunities = list_opportunities()
    st.subheader("Oportunități salvate")
    if not opportunities:
        st.info("Nu există oportunități salvate.")
    else:
        st.dataframe(pd.DataFrame([{
            "Scor": x.get("score"),
            "Referință": x.get("reference"),
            "Titlu": x.get("title"),
            "Deadline": x.get("deadline"),
        } for x in opportunities]), use_container_width=True, hide_index=True)

with tab_analysis:
    opportunities = list_opportunities()
    st.subheader("Analiză AI")
    if not opportunities:
        st.warning("Salvează mai întâi o oportunitate.")
    else:
        idx = st.selectbox(
            "Oportunitate",
            range(len(opportunities)),
            format_func=lambda i: f'{opportunities[i].get("reference")} — {opportunities[i].get("title")}',
            key="analysis_opportunity",
        )
        opportunity = opportunities[idx]
        if st.button("Generează analiza", type="primary"):
            task = """
Realizează:
1. verdict GO / CONDITIONAL GO / NO-GO;
2. potrivirea proiectului;
3. condiții de eligibilitate care trebuie verificate;
4. parteneri și capabilități lipsă;
5. documente necesare;
6. riscuri;
7. plan de 14 zile;
8. scor de pregătire 0-100.
"""
            with st.spinner("AI analizează..."):
                try:
                    result = ai_generate(task, opportunity, org, active_project)
                    save_analysis(active_project_id, opportunity, result)
                    st.session_state["analysis_result"] = result
                except Exception as exc:
                    st.error(str(exc))

        result = st.session_state.get("analysis_result")
        if result:
            st.markdown(result)
            st.download_button(
                "Descarcă Word",
                markdown_to_docx("Analiză GrantAI", result),
                file_name="analiza_grantai.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )

with tab_generator:
    opportunities = list_opportunities()
    st.subheader("Generator")
    if not opportunities:
        st.warning("Salvează mai întâi o oportunitate.")
    else:
        idx = st.selectbox(
            "Oportunitate",
            range(len(opportunities)),
            format_func=lambda i: f'{opportunities[i].get("reference")} — {opportunities[i].get("title")}',
            key="generator_opportunity",
        )
        opportunity = opportunities[idx]
        doc_type = st.selectbox(
            "Document",
            [
                "Concept note",
                "Excellence",
                "Impact",
                "Implementation",
                "Work packages",
                "Deliverables și milestones",
                "Plan de impact și KPI",
                "Registru de riscuri",
                "Rezumat executiv",
            ],
        )
        if st.button("Generează documentul", type="primary"):
            task = f"""
Generează {doc_type}.
Folosește numai datele disponibile.
Pentru orice lipsă scrie [DE COMPLETAT].
Include un tabel cerință / răspuns / dovadă.
Încheie cu un checklist de validare.
"""
            with st.spinner("AI redactează..."):
                try:
                    result = ai_generate(task, opportunity, org, active_project)
                    save_document(active_project_id, opportunity, doc_type, result)
                    st.session_state["generated_result"] = result
                except Exception as exc:
                    st.error(str(exc))

        result = st.session_state.get("generated_result")
        if result:
            st.markdown(result)
            st.download_button(
                "Descarcă Word",
                markdown_to_docx(doc_type, result),
                file_name=f'{doc_type.lower().replace(" ", "_")}.docx',
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )

with tab_history:
    st.subheader("Istoric permanent")
    analyses = list_analyses()
    documents = list_documents()

    st.write("### Analize")
    if not analyses:
        st.info("Nu există analize.")
    else:
        for row in analyses:
            with st.expander(f'Analiză #{row["id"]} — {row["created_at"]}'):
                st.markdown(row["content"])

    st.write("### Documente")
    if not documents:
        st.info("Nu există documente.")
    else:
        for row in documents:
            with st.expander(f'{row["title"]} — {row["created_at"]}'):
                st.markdown(row["content"])

st.divider()
st.caption(
    "SQLite oferă persistență pe discul aplicației, dar Streamlit Community Cloud "
    "poate recrea mediul la redeploy. Pentru persistență garantată pe termen lung, "
    "următoarea etapă va folosi o bază de date externă."
)
