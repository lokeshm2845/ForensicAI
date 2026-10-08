# Forensic AI - Audio Deepfake Detector (web app)

Explainable detection of synthetic speech with a hybrid CNN (EfficientNet-B0 on the Mel-spectrogram) + Transformer (raw waveform) model.
Upload / record audio, get a verdict with **Grad-CAM evidence**, a plain-English forensic report, a **live microphone monitor** and a
**realtime dashboard** backed by a database. Only metadata is stored - never the audio.

```
web/        static UI (HTML/CSS/JS, no build step)         -> deploy on Vercel
server/     FastAPI + PyTorch model API                     -> deploy on Hugging Face Spaces / Render (Docker)
model/      trained weights (21 MB) + measured results
samples/    7 ASVspoof 2019 eval clips (ODC-BY)
supabase/   schema.sql  (table + Row Level Security + Realtime)
tests/      API tests, Supabase mock test, browser end-to-end tests
```

## 1. Run and test on localhost (Windows)

```bash
cd Forensic_AI_Web
py -m pip install -r requirements.txt          # torch is large; skip if already installed
py -m uvicorn server.main:app --port 8000      # or double-click run_local.bat
```
Open **http://127.0.0.1:8000** (UI and API are served together). The pill in the top-right should say *API online*.

**Manual checklist**
1. *Analyze* tab -> click a **Genuine speech** sample -> verdict **REAL**; click **Synthetic - attack A07** -> **FAKE** with a red Grad-CAM region.
2. Hover the spectrogram (time / frequency / evidence tooltip), toggle **Grad-CAM**, drag **Opacity**, press **Play** (playhead moves).
3. Upload your own file (drag & drop, any WAV/FLAC/MP3/OGG/M4A). Clips longer than 4 s get several windows - click a bar in **Window scores** to explain that window.
4. **Record** (allow the microphone) -> Stop & analyze.
5. *Live monitor* -> Start listening; play a sample from your speakers near the mic.
6. *Dashboard*: open it in a **second browser window**, analyse something in the first one and watch the row + counters appear instantly (**LIVE** badge).
7. Theme button (dark/light), resize the window / open on a phone.

**Automated tests** (server must be running for the browser ones)
```bash
py -m pytest -q tests/test_api.py tests/test_supabase_mock.py   # API + database logic
py tests/e2e.py                                                  # 21 browser checks (needs Microsoft Edge/Chrome; first: py -m playwright install msedge)
py tests/e2e_split.py                                            # UI and API on different origins, as in production
```

## 2. How the pieces connect in production

```
Browser --https--> Vercel (static UI)
   |                    
   +--https--> API host (Docker, FastAPI + PyTorch)  --service key--> Supabase Postgres
   +--wss----> Supabase Realtime (new rows are pushed to every open dashboard)
```
Vercel cannot run the model (PyTorch + weights exceed its function size limit; no GPU), so Vercel serves the UI and a small container hosts the API.

### 2a. Supabase (database + realtime)
1. Create a project at supabase.com. SQL Editor -> paste and run `supabase/schema.sql`.
2. Settings -> API: copy the **Project URL**, the **anon** key (public) and the **service_role** key (**secret**).

### 2b. API on Hugging Face Spaces (free CPU, Docker)
1. New Space -> SDK **Docker** -> blank. Push this repo's `Dockerfile`, `server/`, `model/`, `samples/` to the Space repo, and add this header to the Space's `README.md`:
   ```
   ---
   title: Forensic AI API
   sdk: docker
   app_port: 7860
   ---
   ```
2. Space -> Settings -> Variables and secrets: `CORS_ORIGINS=https://YOUR-APP.vercel.app`, `STORE_FILENAMES=0`, `SUPABASE_URL=...`, and **secret** `SUPABASE_SERVICE_KEY=...`.
3. Check `https://YOUR-SPACE.hf.space/api/health` -> `"database": {"backend": "sqlite+supabase"}`.
(Render/Railway/Fly.io work too with the same Dockerfile; give the container >= 1 GB RAM.)

### 2c. UI on Vercel
1. Push this repo to GitHub, then Vercel -> *Add New Project* -> import it. Framework: *Other*. `vercel.json` already sets the build command and output folder.
2. Environment variables: `API_BASE_URL=https://YOUR-SPACE.hf.space`, `SUPABASE_URL`, `SUPABASE_ANON_KEY` (public values only).
3. Deploy. `scripts/make-config.mjs` writes `web/config.js` from those variables at build time.

## 3. Privacy and safety notes
* Audio is processed in memory and discarded; the database holds verdict, score, duration, latency, source and a random per-browser id. Set `STORE_FILENAMES=0` so file names are not stored.
* The browser only ever holds the **anon** key (read-only via RLS). The **service_role** key lives only on the API host.
* The API limits uploads (15 MB, 60 s), rate-limits per IP, and CORS is restricted to your Vercel origin.
* A screening aid, not proof: see the *About* tab for measured performance and limitations.

Model: 5.11 M parameters; EER 4.7 % (range 4.0-5.4 % over 2 seeds) on 14,000 held-out clips with 11 unseen attack types (ASVspoof 2019 LA subset).
Project: Lokesh Magare (B292), NMIMS MPSTME - guide Dr. Suresh Kurumbanshi.
