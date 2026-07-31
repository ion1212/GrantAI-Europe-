create table if not exists public.grant_matches (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    project_id uuid not null references public.projects(id) on delete cascade,
    opportunity_identity text not null,
    match_score integer not null default 0 check (match_score between 0 and 100),
    eligibility_score integer not null default 0 check (eligibility_score between 0 and 100),
    recommendation text not null default 'Review',
    analysis jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique(user_id, project_id, opportunity_identity)
);

create table if not exists public.grant_reviews (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    project_id uuid not null references public.projects(id) on delete cascade,
    opportunity_identity text not null,
    proposal_version_id uuid references public.proposal_versions(id) on delete set null,
    review_type text not null default 'full_readiness_review',
    overall_score integer not null default 0 check (overall_score between 0 and 100),
    result jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create table if not exists public.grant_tasks (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    project_id uuid not null references public.projects(id) on delete cascade,
    opportunity_identity text not null,
    review_id uuid references public.grant_reviews(id) on delete set null,
    title text not null,
    description text not null default '',
    priority text not null default 'Medium'
        check (priority in ('High', 'Medium', 'Low')),
    status text not null default 'Open'
        check (status in ('Open', 'In progress', 'Done')),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.grant_chat_messages (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    project_id uuid not null references public.projects(id) on delete cascade,
    opportunity_identity text not null,
    role text not null check (role in ('user', 'assistant')),
    content text not null,
    created_at timestamptz not null default now()
);

alter table public.grant_matches enable row level security;
alter table public.grant_reviews enable row level security;
alter table public.grant_tasks enable row level security;
alter table public.grant_chat_messages enable row level security;

drop policy if exists "own grant matches" on public.grant_matches;
create policy "own grant matches"
on public.grant_matches
for all to authenticated
using ((select auth.uid()) = user_id)
with check ((select auth.uid()) = user_id);

drop policy if exists "own grant reviews" on public.grant_reviews;
create policy "own grant reviews"
on public.grant_reviews
for all to authenticated
using ((select auth.uid()) = user_id)
with check ((select auth.uid()) = user_id);

drop policy if exists "own grant tasks" on public.grant_tasks;
create policy "own grant tasks"
on public.grant_tasks
for all to authenticated
using ((select auth.uid()) = user_id)
with check ((select auth.uid()) = user_id);

drop policy if exists "own grant chat messages" on public.grant_chat_messages;
create policy "own grant chat messages"
on public.grant_chat_messages
for all to authenticated
using ((select auth.uid()) = user_id)
with check ((select auth.uid()) = user_id);

create index if not exists grant_matches_lookup_idx
on public.grant_matches(user_id, project_id, opportunity_identity);

create index if not exists grant_reviews_lookup_idx
on public.grant_reviews(user_id, project_id, opportunity_identity, created_at desc);

create index if not exists grant_tasks_lookup_idx
on public.grant_tasks(user_id, project_id, opportunity_identity, status);

create index if not exists grant_chat_lookup_idx
on public.grant_chat_messages(user_id, project_id, opportunity_identity, created_at);
