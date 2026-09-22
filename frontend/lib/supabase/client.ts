import { createBrowserClient } from "@supabase/ssr";

import { cookieOptions } from "@/lib/supabase/cookies";
import { supabaseUrl } from "@/lib/supabase/env";

export function createClient() {
  return createBrowserClient(
    supabaseUrl(),
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    { cookieOptions },
  );
}
