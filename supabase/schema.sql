-- Run once in Supabase: Dashboard -> SQL Editor. Stores metadata only (never audio).
create table if not exists public.analyses (
  id          uuid primary key,
  created_at  timestamptz not null default now(),
  filename    text,
  source      text,
  client_id   text,
  duration_s  real,
  verdict     text check (verdict in ('REAL', 'FAKE')),
  prob_fake   real,
  max_prob    real,
  n_windows   int,
  n_flagged   int,
  latency_ms  real
);
create index if not exists analyses_created_idx on public.analyses (created_at desc);

-- Row Level Security: the browser (anon key) may only READ. Inserts come from the API with the service_role key.
alter table public.analyses enable row level security;
drop policy if exists "anon can read analyses" on public.analyses;
create policy "anon can read analyses" on public.analyses for select to anon using (true);

-- Realtime: broadcast INSERTs to subscribed browsers.
alter publication supabase_realtime add table public.analyses;
