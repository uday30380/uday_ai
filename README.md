# Uday AI — free, private Telegram assistant

Tasks & reminders · AI chat (local Ollama) · developer tools · freelance manager (clients, payments, PDF invoices) · club management with roles.
**Runtime cost: ₹0.** No paid API, hosting, database, domain or card is required.

> ⚠️ **Never share your bot token.** If it was ever pasted in a chat, revoke it: @BotFather → `/revoke` → pick the bot → put the *new* token only in your local `.env`.

## What is free, and what isn't

| Part | Cost | Notes |
|---|---|---|
| Software (Python, python-telegram-bot, SQLAlchemy, SQLite, APScheduler, ReportLab, Ollama) | Free / open source | |
| Telegram Bot API | Free | |
| AI inference | Free **only if run locally** with Ollama | Uses your own CPU/RAM/GPU and electricity. |
| Hosting | Free **only on your own computer** | See "Free remote hosting" below: nothing suitable was verified. |

The bot never calls a paid service. If an optional hosted AI provider returns a quota/billing error (HTTP 402/429), the bot stops using it and tells you; it does **not** fall back to anything else.

## Honest limitations

- Reminders and the morning briefing are delivered **only while the bot process is running and can reach Telegram**. If your PC is off, asleep or offline, reminders wait in SQLite and are delivered *late* (the message says so) once the bot is back. They are not lost, but they are not on time.
- Local AI quality/speed depends on your hardware and the model you choose. Without a model, every non-AI feature still works and AI commands give deterministic fallbacks (extractive summary, basic rewrite, built-in dev help).
- Telegram bot chats are not end-to-end encrypted.
- Payments are a personal ledger. The bot cannot verify real bank/UPI payments.
- Club members can only use the bot if their Telegram ID is in `ALLOWED_USER_IDS`; announcements reach only members who have a Telegram ID **and** have started the bot.
- SQLite + single process: designed for one owner and a small club, not thousands of users.

## Local AI (Ollama): hardware

Rule of thumb for quantised models: ~1–3 B parameters need ≈ 2–4 GB free RAM; ~7–8 B need ≈ 8 GB; larger needs 16 GB+ (a GPU helps a lot). CPU-only works but is slow (seconds per word on weak machines). Browse https://ollama.com/library, pick a small model that fits, and set `OLLAMA_MODEL` to its exact name.

## Install — Windows (PowerShell)

```powershell
winget install -e --id Python.Python.3.12
winget install -e --id Git.Git            # optional
# reopen PowerShell, then:
cd path\to\uday-ai
python -m venv .venv
.\.venv\Scripts\Activate.ps1               # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements.txt
winget install -e --id Ollama.Ollama       # optional (local AI)
ollama pull <model-name>                   # e.g. a small model from ollama.com/library
Copy-Item .env.example .env
notepad .env
python scripts/init_db.py
python -m pytest -q
python -m app.main
```

## Install — Linux / macOS

```bash
sudo apt install -y python3.12 python3.12-venv git      # macOS: brew install python@3.12 git
cd uday-ai
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
curl -fsSL https://ollama.com/install.sh | sh            # optional (macOS: brew install ollama)
ollama pull <model-name>
cp .env.example .env && nano .env
python scripts/init_db.py
python -m pytest -q
python -m app.main
```

## Create the bot and find your ID

1. In Telegram open **@BotFather** → `/newbot` → follow the prompts → copy the token into `TELEGRAM_BOT_TOKEN`.
2. Message **@userinfobot** (or send `/id` to your new bot) → copy the number into `ADMIN_IDS`.
3. Set `OLLAMA_MODEL` to the model you pulled (leave empty to disable AI). Start the bot, open it, send `/start`.

## Run it

`python -m app.main` (long polling; keep the window open). Docker is optional: `docker compose up -d --build` (uses `host.docker.internal` to reach host Ollama).
Auto-start: Windows → Task Scheduler "At log on" running `.venv\Scripts\python.exe -m app.main`; Linux → a `systemd --user` service.

## Backup, restore, update

```bash
python scripts/backup.py              # consistent copy into ./backups (safe while running)
# restore: stop the bot first
python scripts/restore.py backups/uday_ai_YYYYMMDD_HHMMSS.db
# update safely
python scripts/backup.py && git pull && pip install -r requirements.txt && python scripts/init_db.py && python -m pytest -q && python -m app.main
```

## Commands

`/start /menu /help /about /id /ping /profile /settings /privacy /cancel` · AI `/ask /summarize /rewrite /explain /clear` ·
Tasks `/task /tasks /done /delete_task /edit_task /today /upcoming /dailyplan` · Reminders `/remind /reminders /cancel_reminder /reschedule` ·
Dev `/snippet /snippets /project /projects /project_status /logwork /devhelp` ·
Freelance `/client /clients /newproject /freelance /payment /payments /reverse_payment /followup /followup_done /invoice` ·
Clubs `/club /newclub /event /events /member /members /clubtask /clubtasks /clubdone /announce /clubexport`.
Most commands accept the first answer inline (`/task Buy milk`). Dates: *tomorrow 5pm*, *in 2 hours*, *30 oct 18:00*; ambiguous input ("at 5") triggers a clarifying question.

## Free remote hosting — what I could and could not verify (checked 27 Oct 2026)

**No suitable free always-on host was verified. Use the local setup.** Findings:

- **Oracle Cloud Always Free**: described as always-on VMs, but sign-up needs a credit card for identity verification → violates the "no card" rule.
- **FPS.ms free Telegram-bot plan**: advertises 128 MB RAM, 25 % CPU, 250 MB storage, no card, but its own page says it must be **renewed every 24 hours**. That is a manual chore, tight on memory for this app, and I could not verify persistence guarantees or long-term terms. Not recommended.
- Other "free 24/7 bot host" sites in search results were marketing pages with referral gates or unverifiable terms. I did not evaluate them.
- Typical free web tiers sleep or have ephemeral disks; SQLite data and reminder polling would be unreliable. Ollama cannot run on any of these. I did not use uptime pingers to defeat sleep rules.

**Optional hosted AI (Mode B)**: Google's Gemini API has a rate-limited free tier (no billing needed for a key), but Google no longer publishes per-model free quotas in its docs (only in the AI Studio console), free-tier prompts may be used to improve Google products, and sources note new AI Studio users may be pushed to a prepaid plan. Because that is not a guaranteed permanent free tier, it is **not** pre-configured. The `openai_compatible` provider is generic and stays off until *you* verify a provider's current free terms and fill `HOSTED_AI_*`. A local daily cap (`AI_DAILY_REQUEST_LIMIT`) applies to all providers.

## Troubleshooting

- **`Configuration problems: ...`** → fix the listed `.env` values (token format `123456:ABC…`, numeric `ADMIN_IDS`).
- **`InvalidToken` / 401 / 404 on start** → token wrong or revoked; re-copy from @BotFather.
- **`Conflict: terminated by other getUpdates request`** → the bot is already running elsewhere (another terminal, Docker, old server). Stop it; only one poller per token.
- **`ModuleNotFoundError`** → activate the venv and `pip install -r requirements.txt`.
- **AI says offline** → `ollama list` must show your model; `ollama serve` must run; `OLLAMA_MODEL` must match exactly; `/about` shows AI status. Timeouts → use a smaller model or raise `AI_TIMEOUT_SECONDS`.
- **Reminder not delivered** → bot not running at that time (it will arrive late), you never pressed Start (Telegram forbids bots messaging first), or you blocked the bot. `/reminders` shows retry errors; after 5 failed attempts a reminder is marked `failed`.
- **Windows "tzdata" / timezone errors** → `pip install tzdata` (already in requirements).

## Security checklist

- [x] Secrets only in `.env` (git-ignored); `.env.example` has no real values; startup validates config
- [x] Private by default: only `ADMIN_IDS` (+ optional `ALLOWED_USER_IDS`) can talk to the bot; others get a refusal and are not stored
- [x] Owner-only personal modules; club permissions (owner/admin/member) checked in the service layer; other clubs' existence is not leaked
- [x] Every personal record query is scoped by `user_id`; tests cover cross-user isolation
- [x] Per-user rate limit, daily AI cap, input/output length limits
- [x] Parameterised ORM queries, FK constraints, transactions, WAL, Alembic migrations
- [x] HTML-escaped output; CSV formula-injection neutralised; PDFs built with ReportLab from escaped text
- [x] No `eval`/`exec`/`subprocess`; snippets are stored as text only; AI output is never executed
- [x] Errors show a generic message to users; stack traces only in logs; bot token/API keys redacted from logs; `httpx` URL logging suppressed
- [x] Announcements need explicit confirmation and can be sent once; payments need confirmation; reversals keep history; deletions confirm
- [x] No paid fallbacks; hosted-AI quota errors stop requests
- [ ] **You**: revoke any token you ever pasted publicly; keep `data/` and backups private (they hold your records unencrypted)
