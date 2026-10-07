-- Stage 93 v1.2 — strict automatic SOLO_ONLY verification.
-- Run once in Supabase Dashboard > SQL Editor before deploying the Python page.

alter table public.stage93_final_agent_runs
    drop constraint if exists stage93_final_agent_runs_verified_solo_count_check;

alter table public.stage93_final_agent_runs
    add constraint stage93_final_agent_runs_verified_solo_count_check
    check (verified_solo_count >= 0 and verified_solo_count <= review_queue_count);

alter table public.stage93_opportunity_review_queue
    drop constraint if exists stage93_opportunity_review_queue_review_status_check;

alter table public.stage93_opportunity_review_queue
    add constraint stage93_opportunity_review_queue_review_status_check
    check (
        review_status in (
            'REQUIRES_OFFICIAL_ELIGIBILITY_REVIEW',
            'REJECTED_INSUFFICIENT_LEAD_TIME',
            'REJECTED_LOW_RELEVANCE',
            'REJECTED_CONSORTIUM_REQUIRED',
            'REJECTED_SOLO_RULE_NOT_PROVEN',
            'READY_FOR_SEPARATE_HUMAN_VERIFICATION'
        )
    );

comment on constraint stage93_final_agent_runs_verified_solo_count_check
on public.stage93_final_agent_runs is
'Only opportunities with retained official single-applicant evidence count as verified solo.';
