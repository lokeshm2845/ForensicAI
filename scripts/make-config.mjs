// Vercel build step: writes web/config.js from environment variables (all three values are public by design).
import { writeFileSync } from "node:fs";
const cfg = {
  apiBase: (process.env.API_BASE_URL || "").replace(/\/$/, ""),
  supabaseUrl: process.env.SUPABASE_URL || "",
  supabaseAnonKey: process.env.SUPABASE_ANON_KEY || "",
};
writeFileSync(new URL("../web/config.js", import.meta.url), `window.FORENSIC_CONFIG = ${JSON.stringify(cfg, null, 2)};\n`);
console.log("config.js written:", { apiBase: cfg.apiBase || "(same origin)", supabase: cfg.supabaseUrl ? "enabled" : "off" });
if (!cfg.apiBase) console.warn("WARNING: API_BASE_URL is empty - the deployed UI will have no backend to talk to.");
