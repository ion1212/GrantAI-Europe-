        ("All messages personally confirmed sent", all_messages_sent),
        ("All actual contact routes recorded", all(len(norm(record.get("contact_route"))) >= 5 for record in contact_records)),
        ("All sent timestamps recorded", all(len(norm(record.get("sent_at_text"))) >= 8 for record in contact_records)),
    ])
if record_responses:
    checks.extend([
        ("Previous sending evidence present", bool(existing and existing.get("external_contact_performed"))),
        ("At least one real response recorded", response_count > 0),
        ("Response timestamp and summary present", all(
            record.get("response_status") == "AWAITING_RESPONSE"
            or (len(norm(record.get("response_received_at_text"))) >= 8 and len(norm(record.get("response_note"))) >= 10)
            for record in contact_records
        )),
    ])
if close_no_response:
    checks.extend([
        ("Previous sending evidence present", bool(existing and existing.get("external_contact_performed"))),
        ("No real response recorded", response_count == 0),
        ("Every candidate remains awaiting response", all(
            record.get("response_status") == "AWAITING_RESPONSE" for record in contact_records
        )),
        ("Passed application deadline recorded", len(norm(deadline_text)) >= 8),
        ("No-response closure personally confirmed", bool(no_response_confirmed)),
    ])

with st.expander("Execution/response checks", expanded=True):
    st.dataframe([{"Check": name, "PASS": passed} for name, passed in checks], use_container_width=True, hide_index=True)

truth_confirmed = st.checkbox("I confirm every status, timestamp and evidence above describes an action or response that actually occurred.", key="stage83_truth")
manual_control = st.checkbox("I understand the AI did not send messages, read an inbox or contact any organisation.", key="stage83_manual_control")
no_commitment = st.checkbox("I understand interest is non-binding until formal eligibility and written consortium agreement are verified.", key="stage83_no_commitment")
phrase_target = "CONFIRM STAGE 83 PARTNER CONTACT EVIDENCE"
phrase = st.text_input("Confirmation phrase", placeholder=f"Type exactly: {phrase_target}", key="stage83_phrase")
ready = all(passed for _, passed in checks) and truth_confirmed and manual_control and no_commitment and norm(phrase) == phrase_target

if st.button("📨 Persist Stage 83 evidence", type="primary", use_container_width=True, disabled=not ready, key="stage83_persist"):
    external_contact_performed = all_messages_sent
    evidence = {
        "execution_version": "stage83-v1.0",
        "stage82_run_id": stage82_run_id,
        "application_reference": application_reference,
        "final_proposal_id": final_proposal_id,
        "contact_records": contact_records,
        "manual_contact_authorized": True,
        "external_contact_performed": external_contact_performed,
        "all_messages_sent": all_messages_sent,
        "response_count": response_count,
        "positive_response_count": positive_response_count,
        "no_response_deadline_text": norm(deadline_text) if close_no_response else None,
        "portal_change_performed": False,
    }
    evidence_sha = sha_json(evidence)
    records_sha = sha_json(contact_records)
    run_basis = {
        "stage": 83,
        "contract": "stage83-v1.0-partner-contact-execution-evidence",
        "stage82_run_id": stage82_run_id,
        "outcome": outcome,
        "contact_records_sha256": records_sha,
        "execution_evidence_sha256": evidence_sha,
    }
    event = {"outcome": outcome, "recorded_at": now_iso(), "evidence_sha256": evidence_sha}
    previous_history = existing.get("event_history") if existing else []
    payload = {
        "user_id": user_id,
        "project_id": project_id,
        "opportunity_lock_id": lock_id,
        "stage82_run_id": stage82_run_id,
        "stage": 83,
        "execution_version": "stage83-v1.0",
        "opportunity_identity": identity,
        "application_reference": application_reference,
        "final_proposal_id": final_proposal_id,
        "run_status": "COMPLETED",
        "execution_outcome": outcome,
        "manual_contact_authorized": True,
        "external_contact_performed": external_contact_performed,
        "all_messages_sent": all_messages_sent,
        "candidate_count": candidate_count,
        "response_count": response_count,
        "positive_response_count": positive_response_count,
        "no_response_deadline_text": norm(deadline_text) if close_no_response else None,
        "contact_records": contact_records,
        "contact_records_sha256": records_sha,
        "portal_change_performed": False,
        "stage82_run_fingerprint": stage82_fingerprint,
        "execution_evidence_sha256": evidence_sha,
        "run_fingerprint": sha_json(run_basis),
        "execution_payload": evidence,
        "run_payload": run_basis,
        "event_history": (previous_history or []) + [event],
        "authorized_at": existing.get("authorized_at") if existing else now_iso(),
        "sent_at": now_iso() if external_contact_performed else None,
        "updated_at": now_iso(),
        "completed_at": now_iso(),
    }
    try:
        if existing:
            supabase.table("stage83_partner_contact_execution_evidence").update(payload).eq("id", existing["id"]).execute()
        else:
            payload["created_at"] = now_iso()
            supabase.table("stage83_partner_contact_execution_evidence").insert(payload).execute()
        st.success(f"Stage 83 persisted — {outcome}.")
        st.rerun()
    except Exception as exc:
        st.error(f"Stage 83 persistence failed. Run Stage 83 SQL first. {type(exc).__name__}: {str(exc)[:1600]}")

latest_rows = rows("stage83_partner_contact_execution_evidence", {"stage82_run_id": stage82_run_id}, "created_at", 1)
latest = latest_rows[0] if latest_rows else None
if latest:
    st.divider()
    st.subheader("Stage 83 outcome")
    st.success(f"Run ID: {latest.get('id')} — Outcome: {latest.get('execution_outcome')}")
    a, b, c, d = st.columns(4)
    a.metric("Authorized", "YES" if latest.get("manual_contact_authorized") else "NO")
    b.metric("All sent", "YES" if latest.get("all_messages_sent") else "NO")
    c.metric("Responses", latest.get("response_count"))
    d.metric("Positive/possible", latest.get("positive_response_count"))
    st.dataframe(latest.get("contact_records") or [], use_container_width=True, hide_index=True)
    st.write(f"**Run fingerprint:** `{latest.get('run_fingerprint')}`")

st.caption("Invariant Stage 83 v1.0: authorization, sending and responses are distinct states. No external action is inferred or invented.")
