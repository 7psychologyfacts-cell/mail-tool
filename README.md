# 📬 MailOps — Enterprise Mail Console

**A real, working webmail client — not a UI mockup.** MailOps connects to any real IMAP mailbox (Gmail, Office 365, cPanel, Zimbra), lets you search, read, and bulk-download mail as real `.zip` archives, and streams large exports straight to Google Drive when they're too big to download directly.

Built as a lean **3-file full-stack project**: one FastAPI backend, one Alpine.js + Tailwind frontend, one requirements file. No database, no build step, no framework bloat — just clean, deployable code.

> 🔗 **Live demo:** *https://mailops-mail.vercel.app/*
> 
> 🖼️ **Screenshots:**
> 
><img width="1919" height="946" alt="image" src="https://github.com/user-attachments/assets/d8d5679f-e45c-464b-9e1f-8c9a734ca721" />
---
><img width="1917" height="997" alt="image" src="https://github.com/user-attachments/assets/ae2de3ff-4c12-41de-8d62-4f59d165f803" />



---

## ✨ Why this project stands out

- **It's real, not fake data.** Every email, folder, attachment, and download in this app comes from a live IMAP connection — there's no mock JSON sitting behind the UI.
- **Handles the hard parts of email properly:** MIME parsing, IMAP `BODYSTRUCTURE` parsing for attachments, correct UID-based fetch ordering, character-set decoding of headers, and streaming multi-gigabyte zip downloads without loading them into memory.
- **Production-minded security:** credentials are never stored in plaintext — passwords are Fernet-encrypted at rest, sessions are signed JWTs, and nothing is hardcoded (all secrets come from environment variables).
- **Thoughtful UX details:** 10 accent color themes, light/dark mode, configurable mail sorting, a working audit-log export, responsive layout, and a genuinely fast advanced-search experience.
- **Deploys in minutes:** ships as a single Vercel-ready serverless function — clone, set 2 environment variables, deploy.

---

## 🚀 Features

### Mail
- 🔐 Real IMAP sign-in for Gmail, Google Workspace, Office 365, cPanel, and Zimbra (auto-detects the IMAP host from the email domain)
- 📥 Live inbox with real folder counts, unread badges, and per-folder navigation
- 🔎 Universal search plus an advanced filter drawer (from, to, subject, has-attachment, date range)
- ↕️ Sortable mail list — newest first, oldest first, sender A–Z, or unread first
- 📎 Full attachment support — preview metadata and download individual files or entire messages
- 📤 Bulk download of selected emails as a `.zip` (with or without attachments), streamed directly from the server
- ☁️ Automatic Google Drive hand-off for exports too large to stream to the browser
- ✅ Real read/unread state, synced back to the mail server via IMAP flags

### Interface
- 🎨 10 selectable accent color themes + light/dark mode, managed from a dedicated **Settings** panel
- 🧾 A working **Security & Audit Log** — every sign-in, sign-out, download, delete, and settings change is recorded and exportable as a real `.csv` file
- 📱 Responsive three-pane layout (folders → list → reading pane)
- ⚡ Debounced search, optimistic UI states, toast notifications

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3, [FastAPI](https://fastapi.tiangolo.com/), `imaplib` (real IMAP client) |
| Auth & Security | JWT (`PyJWT`), Fernet symmetric encryption (`cryptography`) |
| Bulk downloads | `zipstream-ng` (true streaming zip, no full-file buffering) |
| Cloud storage | Google Drive API (`google-api-python-client`, OAuth2) |
| Frontend | Alpine.js (reactive state, no build step), Tailwind CSS (CDN) |
| Deployment | Vercel serverless (Python runtime) |

**Why this stack:** it's intentionally minimal — a single backend file and a single HTML file, deployable anywhere Python runs, with zero frontend build pipeline. It demonstrates the ability to design a real system end-to-end rather than leaning on scaffolding.

---

## 🧩 Architecture

```
┌─────────────────┐      HTTPS / JWT       ┌───────────────────┐      IMAP / SMTP-less      ┌─────────────────┐
│   index.html     │  ───────────────────▶  │      app.py        │  ────────────────────────▶ │  Mail Provider   │
│ (Alpine + Tail-  │  ◀───────────────────  │   (FastAPI)         │  ◀──────────────────────── │ (Gmail / O365 /  │
│  wind, no build) │      JSON / zip        │                     │        real mailbox         │  cPanel / Zimbra)│
└─────────────────┘                        └─────────┬───────────┘
                                                       │ OAuth2
                                                       ▼
                                             ┌───────────────────┐
                                             │   Google Drive     │
                                             │  (large exports)   │
                                             └───────────────────┘
```

The backend never persists mail — every request opens a fresh authenticated IMAP session, fetches exactly what's needed, and closes it. Session state lives entirely in a signed JWT held by the browser.

---

## 📡 API Reference

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/api/auth/login` | Authenticate against a real IMAP server, returns a JWT |
| `GET` | `/api/auth/google/authorize` | Get the Google OAuth consent URL |
| `GET` | `/api/auth/google/callback` | OAuth2 redirect handler |
| `POST` | `/api/auth/attach-drive` | Link a Google Drive account to the current session |
| `GET` | `/api/auth/drive/status` | Check Drive connection + quota |
| `GET` | `/api/folders` | List real mailbox folders with unread/total counts |
| `POST` | `/api/search` | Server-side search across folder, query, sender, subject, date range (paginated) |
| `GET` | `/api/email/{folder}/{uid}` | Fetch a full email (body, headers, attachment list) |
| `GET` | `/api/email/{folder}/{uid}/attachment/{filename}` | Download a single attachment |
| `POST` | `/api/download/estimate` | Estimate zip size before committing to a bulk download |
| `POST` | `/api/download` | Stream a real zip archive, or upload to Drive if it's too large |
| `GET` | `/api/health` | Health check |

---

## ⚙️ Getting Started

### 1. Clone & install
```bash
git clone <your-repo-url>
cd mailops
pip install -r requirements.txt
```

### 2. Configure environment variables
```bash
JWT_SECRET=<a long random string>          # required — signs session tokens
FERNET_KEY=<a Fernet.generate_key() value> # required — encrypts stored IMAP passwords

# Optional — only needed for Google Drive exports
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=http://localhost:8000/api/auth/google/callback
```

Generate a Fernet key:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 3. Run locally
```bash
uvicorn app:app --reload
```
Visit `http://localhost:8000`. Sign in with a real Gmail/Workspace App Password or a corporate IMAP mailbox.

### 4. Deploy to Vercel
```bash
vercel
```
This repo ships with a `vercel.json` already configured for the Python runtime — just set the environment variables in the Vercel dashboard and deploy.

---

## 🔒 Security Notes

- IMAP passwords are **never stored in plaintext** — they're Fernet-encrypted and embedded only inside the signed JWT held by the client.
- All secrets (`JWT_SECRET`, `FERNET_KEY`, Google OAuth credentials) are read from environment variables — nothing sensitive is committed to source.
- Every mailbox action re-authenticates a fresh IMAP session; there is no server-side session store to leak.

---

## 🗺️ Roadmap

- [ ] SMTP send support (currently read/search/download only)
- [ ] Server-side delete/move (currently view-only removal)
- [ ] Persistent labels and starring across sessions

---

## 👤 About the Developer

**Milan Vadher** — building practical, production-style projects to demonstrate full-stack engineering skills: real API integrations, secure auth, streaming file handling, and clean UI/UX — not just tutorials.

📧 milanvadher2003@gmail.com 

---

## 📄 License

This project is available under the MIT License — feel free to fork, adapt, and build on it.
