import json
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import requests
import streamlit as st
from dateutil import parser as date_parser

st.set_page_config(page_title="EU GrantAI Europe", page_icon="🇪🇺", layout="wide")

EC_SEARCH_API = "https://api.tech.ec.europa.eu/search-api/prod/rest/search"
EC_API_KEY = "SEDIA"

PROFILE = {
    "organisation": {
        "legal_name": "II Ciobotaru Viorel Razvan Ionut",
        "country": "Romania",
        "organisation_type": "SME / întreprindere individuală",
        "capabilities": [
            "agriculture", "smart greenhouse", "renewable energy",
            "battery storage", "AI automation"
        ],
    },
    "project": {
        "name": "GreenRise",
        "summary": "Seră inteligentă cu energie regenerabilă, baterii și automatizare AI.",
        "keywords": ["agriculture", "greenhouse", "energy", "battery", "AI", "rural", "agrifood"],
    },
}

def clean_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    if isinstance(value, dict):
        return ", ".join(str(item) for item in value.values())
    return str(value)

def normalize_result(item: dict[str, Any]) -> dict[str, Any]:
    source = item.get("_source", item)
    return {
        "id": clean_value(source.get("identifier") or source.get("callccm2Id") or source.get("reference")),
        "reference": clean_value(source.get("reference")),
        "title": clean_value(source.get("title")),
        "status": clean_value(source.get("status")),
        "programme": clean_value(source.get("frameworkProgramme") or source.get("programme") or source.get("caName")),
        "action_type": clean_value(source.get("typesOfAction") or source.get("typeOfAction")),
        "deadline": clean_value(source.get("deadlineDate") or source.get("deadline")),
        "description": clean_value(source.get("description")),
    }

@st.cache_data(ttl=3600, show_spinner=False)
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
        EC_SEARCH_API, params=params, files=multipart_files, headers=headers, timeout=60
    )
    response.raise_for_status()
    payload = response.json()
    candidates = (
        payload.get("results")
        or payload.get("hits", {}).get("hits")
        or payload.get("response", {}).get("docs")
        or []
    )
    return [normalize_result(item) for item in candidates]

def overlap_score(words: list[str], text: str) -> float:
    if not words:
        return 0.0
    corpus = text.lower()
    matches = sum(1 for word in words if word.strip().lower() in corpus)
    return min(100.0, matches / len(words) * 100.0)

def deadline_score(deadline_text: str) -> tuple[float, str]:
    if not deadline_text:
        return 45.0, "Deadline necunoscut"
    try:
        deadline = date_parser.parse(deadline_text)
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        days = (deadline - datetime.now(timezone.utc)).days
        if days < 0:
            return 0.0, "Închis"
        if days < 14:
            return 15.0, f"{days} zile"
        if days < 30:
            return 35.0, f"{days} zile"
        if days < 60:
            return 60.0, f"{days} zile"
        if days < 120:
            return 82.0, f"{days} zile"
        return 100.0, f"{days} zile"
    except Exception:
        return 40.0, "Format necunoscut"

def score_opportunity(call: dict[str, Any]) -> dict[str, Any]:
    corpus = " ".join([
        call.get("title", ""), call.get("description", ""),
        call.get("programme", ""), call.get("action_type", "")
    ])
    thematic = overlap_score(PROFILE["project"]["keywords"], corpus)
    capability = overlap_score(PROFILE["organisation"]["capabilities"], corpus)
    timing, deadline_label = deadline_score(call.get("deadline", ""))
    eligibility = 70.0
    evidence = 80.0 if call.get("description") else 45.0
    total = (
        thematic * 0.36 + capability * 0.24 + timing * 0.16
        + eligibility * 0.16 + evidence * 0.08
    )
    return {
        **call,
        "score": round(max(0.0, min(100.0, total)), 1),
        "thematic_fit": round(thematic, 1),
        "capability_fit": round(capability, 1),
        "deadline_label": deadline_label,
    }

def recommendation(score: float) -> str:
    if score >= 72:
        return "Prioritate ridicată"
    if score >= 50:
        return "Verificare manuală"
    return "Potrivire redusă"

st.title("🇪🇺 EU GrantAI Europe")
st.caption("Agent AI pentru identificarea și pregătirea proiectelor europene")

tab_profile, tab_calls, tab_saved, tab_generator = st.tabs([
    "Profil", "Apeluri reale", "Selectate", "Generator"
])

with tab_profile:
    st.subheader("Organizație")
    st.json(PROFILE["organisation"])
    st.subheader("Proiect principal")
    st.json(PROFILE["project"])

with tab_calls:
    st.subheader("Căutare reală în Funding & Tenders")
    col1, col2 = st.columns([3, 1])
    keyword = col1.text_input("Cuvinte-cheie", "agriculture energy battery AI")
    result_limit = col2.selectbox("Rezultate", [10, 20, 30, 50], index=1)

    if st.button("Caută apeluri", type="primary", use_container_width=True):
        with st.spinner("Interoghez portalul Comisiei Europene..."):
            try:
                results = [score_opportunity(x) for x in search_eu_calls(keyword, result_limit)]
                results.sort(key=lambda x: x["score"], reverse=True)
                st.session_state["results"] = results
                st.success(f"Au fost analizate {len(results)} oportunități.")
            except Exception as exc:
                st.error(f"Căutarea nu a reușit: {exc}")

    results = st.session_state.get("results", [])
    if results:
        df = pd.DataFrame([{
            "Scor": x["score"],
            "Referință": x["reference"],
            "Titlu": x["title"],
            "Program": x["programme"],
            "Deadline": x["deadline"],
            "Recomandare": recommendation(x["score"]),
        } for x in results])
        st.dataframe(df, use_container_width=True, hide_index=True)

        selected_index = st.selectbox(
            "Deschide oportunitatea",
            range(len(results)),
            format_func=lambda i: f'{results[i]["score"]}% — {results[i]["reference"]} — {results[i]["title"]}'
        )
        selected = results[selected_index]
        st.subheader(selected["title"] or "Oportunitate")
        st.write(f'**Referință:** {selected["reference"] or "N/A"}')
        st.write(f'**Program:** {selected["programme"] or "N/A"}')
        st.write(f'**Deadline:** {selected["deadline"] or "N/A"}')

        c1, c2, c3 = st.columns(3)
        c1.metric("Scor total", f'{selected["score"]}%')
        c2.metric("Potrivire tematică", f'{selected["thematic_fit"]}%')
        c3.metric("Timp disponibil", selected["deadline_label"])

        if selected["description"]:
            with st.expander("Descriere"):
                st.write(selected["description"])

        if st.button("Adaugă la selectate"):
            saved = st.session_state.setdefault("saved_opportunities", [])
            existing = {x.get("id") or x.get("reference") for x in saved}
            current_id = selected.get("id") or selected.get("reference")
            if current_id not in existing:
                saved.append(selected)
                st.success("Oportunitate adăugată.")
            else:
                st.info("Este deja selectată.")

with tab_saved:
    st.subheader("Oportunități selectate")
    saved = st.session_state.get("saved_opportunities", [])
    if not saved:
        st.info("Nu ai selectat încă nicio oportunitate.")
    else:
        st.dataframe(pd.DataFrame([{
            "Scor": x["score"],
            "Referință": x["reference"],
            "Titlu": x["title"],
            "Deadline": x["deadline"],
        } for x in saved]), use_container_width=True, hide_index=True)
        st.download_button(
            "Descarcă lista JSON",
            data=json.dumps(saved, ensure_ascii=False, indent=2),
            file_name="oportunitati_selectate.json",
            mime="application/json",
        )

with tab_generator:
    st.subheader("Generator de structură")
    saved = st.session_state.get("saved_opportunities", [])
    if not saved:
        st.warning("Selectează mai întâi o oportunitate.")
    else:
        idx = st.selectbox(
            "Alege oportunitatea",
            range(len(saved)),
            format_func=lambda i: f'{saved[i]["reference"]} — {saved[i]["title"]}'
        )
        selected = saved[idx]
        if st.button("Generează structura aplicației", type="primary"):
            draft = f'''
# {PROFILE["project"]["name"]}

## Apel selectat
- Referință: {selected["reference"]}
- Titlu: {selected["title"]}
- Program: {selected["programme"]}
- Deadline: {selected["deadline"]}

## 1. Excellence
[DE COMPLETAT]

## 2. Impact
[DE COMPLETAT]

## 3. Implementation
[DE COMPLETAT]

## Work packages
[DE COMPLETAT]

## Buget
[DE COMPLETAT ȘI CONFIRMAT]
'''
            st.session_state["draft"] = draft

        draft = st.session_state.get("draft")
        if draft:
            st.markdown(draft)
            st.download_button(
                "Descarcă structura",
                data=draft,
                file_name="structura_aplicatie.md",
                mime="text/markdown",
            )

st.divider()
st.caption(
    "Scorul este orientativ. Eligibilitatea juridică, bugetul, "
    "declarațiile și depunerea finală trebuie verificate."
)
