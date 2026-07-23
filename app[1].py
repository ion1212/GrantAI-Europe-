import io
import json
import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

import pandas as pd
import requests
import streamlit as st
from dateutil import parser as date_parser
from docx import Document
from openai import OpenAI

st.set_page_config(page_title="GrantAI Europe", page_icon="🇪🇺", layout="wide")

EC_SEARCH_API = "https://api.tech.ec.europa.eu/search-api/prod/rest/search"
EC_API_KEY = "SEDIA"
PORTAL_BASE = "https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/opportunities/topic-search"

PROGRAMMES = [
    "Toate",
    "Horizon Europe",
    "LIFE",
    "Digital Europe",
    "Erasmus+",
    "Connecting Europe Facility",
    "Single Market Programme",
    "EU4Health",
    "European Defence Fund",
]

DEFAULT_PROFILE = {
    "organisation": {
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
    },
    "project": {
        "name": "GreenRise",
        "summary": "Seră inteligentă cu energie regenerabilă, baterii și automatizare AI.",
        "keywords": [
            "agriculture",
            "greenhouse",
            "energy",
            "battery",
            "AI",
            "rural",
            "agrifood",
        ],
        "target_budget_eur": 0,
        "preferred_role": "beneficiary",
    },
}

def get_secret(name: str, default: str = "") -> str:
    try:
        return str(st.secrets.get(name, default))
    except Exception:
        return os.getenv(name, default)

def ensure_state() -> None:
    st.session_state.setdefault("profile", json.loads(json.dumps(DEFAULT_PROFILE)))
    st.session_state.setdefault("saved_opportunities", [])
    st.session_state.setdefault("generated_documents", [])
    st.session_state.setdefault("results", [])

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
        "programme": clean_value(
            source.get("frameworkProgramme")
            or source.get("programme")
            or source.get("caName")
        ),
        "action_type": clean_value(
            source.get("typesOfAction")
            or source.get("typeOfAction")
        ),
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
    multipart_files = {
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
        files=multipart_files,
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

def score_opportunity(call: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    corpus = " ".join([
        call.get("title", ""),
        call.get("description", ""),
        call.get("programme", ""),
        call.get("action_type", ""),
    ])
    thematic = overlap_score(profile["project"]["keywords"], corpus)
    capability = overlap_score(profile["organisation"]["capabilities"], corpus)
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

def filter_results(
    results: list[dict[str, Any]],
    programme: str,
    only_open: bool,
    minimum_score: int,
    minimum_days: int,
) -> list[dict[str, Any]]:
    filtered = []
    for item in results:
        if programme != "Toate" and programme.lower() not in item.get("programme", "").lower():
            continue
        if only_open and item.get("days_left") is not None and item["days_left"] < 0:
            continue
        if item.get("score", 0) < minimum_score:
            continue
        if item.get("days_left") is not None and item["days_left"] < minimum_days:
            continue
        filtered.append(item)
    return filtered

def ai_generate(task: str, opportunity: dict[str, Any], profile: dict[str, Any]) -> str:
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
            "certificări sau experiență. Pentru orice lipsă scrie [DE COMPLETAT]. "
            "Prioritizează documentele oficiale și indică riscurile explicit."
        ),
        input=f"""
SARCINĂ:
{task}

PROFIL:
{json.dumps(profile, ensure_ascii=False, indent=2)}

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

ensure_state()
profile = st.session_state["profile"]

st.title("🇪🇺 GrantAI Europe — Etapa 5")
st.caption("Conectare extinsă la EU Funding & Tenders Portal și analiză automată")

tabs = st.tabs([
    "Dashboard",
    "Profil",
    "Funding Portal",
    "Selectate",
    "Analiză AI",
    "Generator",
    "Documente",
])
tab_dashboard, tab_profile, tab_portal, tab_saved, tab_analysis, tab_generator, tab_docs = tabs

with tab_dashboard:
    st.subheader("Dashboard")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Rezultate curente", len(st.session_state["results"]))
    c2.metric("Selectate", len(st.session_state["saved_opportunities"]))
    c3.metric("Documente", len(st.session_state["generated_documents"]))
    c4.metric("AI", "Activ" if get_secret("OPENAI_API_KEY") else "Neconfigurat")
    st.info(
        "Aplicația caută în datele publice Funding & Tenders. "
        "Depunerea oficială, EU Login și semnarea rămân în portalul Comisiei Europene."
    )

with tab_profile:
    org = profile["organisation"]
    proj = profile["project"]
    st.subheader("Profil organizație")
    col1, col2 = st.columns(2)
    org["legal_name"] = col1.text_input("Denumire legală", org["legal_name"])
    org["country"] = col2.text_input("Țară", org["country"])
    org["organisation_type"] = col1.text_input("Tip organizație", org["organisation_type"])
    org["pic"] = col2.text_input("PIC", org["pic"])
    org["caen"] = col1.text_input("CAEN", org["caen"])
    org["staff"] = col2.number_input("Angajați", min_value=0, value=int(org["staff"]))
    org["capabilities"] = [
        x.strip() for x in st.text_area(
            "Capabilități, separate prin virgulă",
            ", ".join(org["capabilities"]),
        ).split(",") if x.strip()
    ]
    st.subheader("Proiect principal")
    proj["name"] = st.text_input("Nume proiect", proj["name"])
    proj["summary"] = st.text_area("Rezumat", proj["summary"], height=130)
    proj["keywords"] = [
        x.strip() for x in st.text_input(
            "Cuvinte-cheie",
            ", ".join(proj["keywords"]),
        ).split(",") if x.strip()
    ]
    if st.button("Salvează profilul", type="primary"):
        st.session_state["profile"] = profile
        st.success("Profil actualizat.")

with tab_portal:
    st.subheader("EU Funding & Tenders Portal")
    search_col, limit_col = st.columns([3, 1])
    keywords = search_col.text_input(
        "Căutare",
        "agriculture greenhouse battery renewable energy AI",
    )
    limit = limit_col.selectbox("Rezultate API", [20, 50, 100], index=1)

    f1, f2, f3, f4 = st.columns(4)
    programme = f1.selectbox("Program", PROGRAMMES)
    only_open = f2.checkbox("Doar apeluri active", value=True)
    minimum_score = f3.slider("Scor minim", 0, 100, 35, 5)
    minimum_days = f4.slider("Minimum zile rămase", 0, 180, 14, 7)

    if st.button("Sincronizează apelurile", type="primary", use_container_width=True):
        with st.spinner("Preiau și analizez datele publice ale portalului..."):
            try:
                raw = search_eu_calls(keywords, limit)
                scored = [score_opportunity(x, profile) for x in raw]
                scored.sort(key=lambda x: x["score"], reverse=True)
                st.session_state["results"] = scored
                st.success(f"Sincronizare finalizată: {len(scored)} rezultate preluate.")
            except Exception as exc:
                st.error(f"Conectarea la portal nu a reușit: {exc}")

    filtered = filter_results(
        st.session_state["results"],
        programme,
        only_open,
        minimum_score,
        minimum_days,
    )

    if filtered:
        st.write(f"**Rezultate după filtrare: {len(filtered)}**")
        table = pd.DataFrame([{
            "Scor": x["score"],
            "Referință": x["reference"],
            "Titlu": x["title"],
            "Program": x["programme"],
            "Tip": x["action_type"],
            "Deadline": x["deadline"],
            "Zile": x["days_left"],
        } for x in filtered])
        st.dataframe(table, use_container_width=True, hide_index=True)

        index = st.selectbox(
            "Deschide apelul",
            range(len(filtered)),
            format_func=lambda i: f'{filtered[i]["score"]}% — {filtered[i]["reference"]} — {filtered[i]["title"]}',
        )
        selected = filtered[index]
        st.subheader(selected["title"] or "Apel")
        st.write(f'**Referință:** {selected["reference"] or "N/A"}')
        st.write(f'**Program:** {selected["programme"] or "N/A"}')
        st.write(f'**Tip acțiune:** {selected["action_type"] or "N/A"}')
        st.write(f'**Deadline:** {selected["deadline"] or "N/A"}')

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Scor", f'{selected["score"]}%')
        m2.metric("Tematic", f'{selected["thematic_fit"]}%')
        m3.metric("Capabilități", f'{selected["capability_fit"]}%')
        m4.metric("Timp", selected["deadline_label"])

        st.link_button("Deschide în portalul oficial", selected["official_url"])

        if selected["description"]:
            with st.expander("Descriere publică"):
                st.write(selected["description"])

        if st.button("Salvează oportunitatea"):
            saved = st.session_state["saved_opportunities"]
            identity = selected.get("id") or selected.get("reference")
            existing = {x.get("id") or x.get("reference") for x in saved}
            if identity not in existing:
                saved.append(selected)
                st.success("Oportunitate salvată.")
            else:
                st.info("Oportunitatea este deja salvată.")
    elif st.session_state["results"]:
        st.warning("Niciun rezultat nu respectă filtrele selectate.")

with tab_saved:
    saved = st.session_state["saved_opportunities"]
    st.subheader("Oportunități selectate")
    if not saved:
        st.info("Nu există oportunități selectate.")
    else:
        st.dataframe(pd.DataFrame([{
            "Scor": x["score"],
            "Referință": x["reference"],
            "Titlu": x["title"],
            "Deadline": x["deadline"],
        } for x in saved]), use_container_width=True, hide_index=True)
        st.download_button(
            "Export JSON",
            json.dumps(saved, ensure_ascii=False, indent=2),
            file_name="grantai_opportunities.json",
            mime="application/json",
        )

with tab_analysis:
    saved = st.session_state["saved_opportunities"]
    st.subheader("Analiză AI bazată pe apel")
    if not saved:
        st.warning("Salvează mai întâi un apel.")
    else:
        idx = st.selectbox(
            "Apel",
            range(len(saved)),
            format_func=lambda i: f'{saved[i]["reference"]} — {saved[i]["title"]}',
            key="analysis",
        )
        selected = saved[idx]
        if st.button("Analizează eligibilitatea și strategia", type="primary"):
            task = """
Efectuează o analiză profesională:
1. verdict GO / CONDITIONAL GO / NO-GO;
2. potrivirea cu GreenRise;
3. condițiile care trebuie verificate în portal;
4. tipul probabil de consorțiu;
5. capabilități și parteneri lipsă;
6. documentele oficiale care trebuie citite;
7. riscurile juridice, tehnice, financiare și de calendar;
8. plan de lucru pentru următoarele 14 zile;
9. scor de pregătire 0-100.
"""
            with st.spinner("AI analizează oportunitatea..."):
                try:
                    st.session_state["analysis_result"] = ai_generate(task, selected, profile)
                except Exception as exc:
                    st.error(str(exc))

        result = st.session_state.get("analysis_result")
        if result:
            st.markdown(result)
            st.download_button(
                "Descarcă analiza Word",
                markdown_to_docx("Analiză GrantAI", result),
                file_name="analiza_grantai.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )

with tab_generator:
    saved = st.session_state["saved_opportunities"]
    st.subheader("Generator de propunere")
    if not saved:
        st.warning("Salvează mai întâi un apel.")
    else:
        idx = st.selectbox(
            "Apel",
            range(len(saved)),
            format_func=lambda i: f'{saved[i]["reference"]} — {saved[i]["title"]}',
            key="generator",
        )
        selected = saved[idx]
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
        if st.button("Generează draftul", type="primary"):
            task = f"""
Generează {doc_type}.
Folosește numai informațiile disponibile în profil și apel.
Pentru date lipsă scrie [DE COMPLETAT].
Include un tabel cu cerință / răspuns / dovadă necesară.
Încheie cu o listă de verificare înainte de depunere.
"""
            with st.spinner("AI redactează..."):
                try:
                    result = ai_generate(task, selected, profile)
                    st.session_state["generated_text"] = result
                    st.session_state["generated_documents"].append({
                        "title": doc_type,
                        "call": selected["reference"],
                        "content": result,
                    })
                except Exception as exc:
                    st.error(str(exc))

        result = st.session_state.get("generated_text")
        if result:
            st.markdown(result)
            st.download_button(
                "Descarcă Word",
                markdown_to_docx(doc_type, result),
                file_name=f'{doc_type.lower().replace(" ", "_")}.docx',
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )

with tab_docs:
    docs = st.session_state["generated_documents"]
    st.subheader("Documente generate")
    if not docs:
        st.info("Nu există documente generate.")
    else:
        for n, doc in enumerate(reversed(docs), 1):
            with st.expander(f'{n}. {doc["title"]} — {doc["call"]}'):
                st.markdown(doc["content"])

st.divider()
st.caption(
    "Integrarea folosește date publice. GrantAI nu completează sau trimite automat "
    "formulare în EU Login și nu semnează declarații."
)
