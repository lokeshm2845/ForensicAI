// Runtime configuration. Local: leave as is (the page talks to the API on the same origin).
// Production (Vercel): scripts/make-config.mjs regenerates this file from environment variables at build time.
window.FORENSIC_CONFIG = {
  apiBase: "",            // e.g. "https://YOUR-SPACE.hf.space" when the UI and API are on different hosts
  supabaseUrl: "",        // e.g. "https://abcdxyz.supabase.co"      (public)
  supabaseAnonKey: ""     // the *anon* key only - read-only through Row Level Security (public by design)
};
