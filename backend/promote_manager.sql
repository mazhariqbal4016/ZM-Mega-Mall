-- Run manually in the Supabase SQL Editor as an administrator.
-- Public signup can create staff users only.

update public.profiles
set role = 'manager', is_active = true
where lower(email) = lower('mazhariqbal4016@gmail.com');

-- Run this after the admin has completed Supabase Auth signup.
