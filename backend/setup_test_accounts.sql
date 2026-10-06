-- Safe role setup for the requested accounts.
-- This only updates profiles for users who already completed Supabase Auth signup.
-- It never creates passwords or inserts an orphan profile.

update public.profiles
set role = 'manager', is_active = true
where lower(email) = lower('sanakhan031788@gmail.com');

update public.profiles
set role = 'staff', is_active = true
where lower(email) = lower('mazharoo6371@gmail.com');

select email, role, is_active
from public.profiles
where lower(email) in (
    lower('sanakhan031788@gmail.com'),
    lower('mazharoo6371@gmail.com')
)
order by email;
