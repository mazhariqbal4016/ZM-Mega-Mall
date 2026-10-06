-- Run manually in the Supabase SQL Editor as an administrator.
-- Public signup can create staff users only.

update public.profiles
set role = 'manager'
where email = 'manager@example.com';

-- Replace manager@example.com with the verified staff user's email.
