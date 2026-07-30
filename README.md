# 📧 Smart Enterprise Mail Automation & Filtering Tool

A production-ready Python application designed for **automated email ingestion, intelligent folder monitoring, and operational email management**. 

Built specifically for high-volume enterprise environments, this system connects to mailbox infrastructure via IMAP, automatically discovers and categorizes incoming messages, filters non-essential communications, and provides a real-time web dashboard for email previewing, tracking, and execution.

---

## ⚙️ Operational Impact

* **Automated Mailbox Monitoring:** Eliminates manual tracking across multiple email folders and shared operational mailboxes.
* **Intelligent Domain & Intent Filtering:** Automatically separates internal operational communications from external vendor/insurer updates using domain-matching and regex rules.
* **Stateless & Idempotent Execution:** Leverages IMAP UID-based processing to prevent duplicate processing or sequence conflicts across distributed cloud environments.
* **Instant Mail Preview & Attachment Handling:** Renders HTML email previews and handles attachments directly from the dashboard without needing full email client access.

---

## 💼 Business Impact

* **Reduced Processing Delays:** Accelerates email-driven workflows (such as customer queries, vendor billing, and status updates) through real-time mailbox discovery.
* **Enhanced Productivity:** Frees operations teams from spending hours daily scanning, sorting, and manually forwarding routine emails.
* **Low Operational Overhead:** Designed with efficient filtering logic to minimize API/LLM processing costs by processing only relevant operational emails.
* **Audit-Ready Mail Tracking:** Maintains full historical traceability for every processed message, reducing compliance risks and lost communications.

---

## 👨‍💻 Developer Information

* **Backend Engine:** Built on Python 3 with Flask for lightweight WSGI web API routing and IMAP communication (`imaplib`, `email`).
* **Frontend UI:** Modern Single Page Application (SPA) dashboard built using HTML5, CSS3, and JavaScript for seamless interaction and mail monitoring.
* **Stateless IMAP Logic:** Implements robust UID fetch routines to ensure idempotent, serverless-friendly execution.
* **Deployment Optimization:** Pre-configured with `vercel.json` for zero-downtime serverless deployment on Vercel (`@vercel/python`).

---

## 🌟 Key Capabilities & Features

### 📬 1. Smart IMAP Ingestion & Folder Discovery
* Multi-folder mailbox scanning with dynamic date-range filtering.
* Robust SSL connection handling with app password authentication support.

### 🔍 2. Rule-Based Filtering & Routing
* Domain-level sender extraction to categorize external vs. internal emails.
* Subject and body regex matching to filter out low-priority automated notifications.

### 🖥️ 3. Web Dashboard & Preview Engine
* Live email list monitoring with UID tracking.
* In-browser HTML rendering and email body previewing.

---

## 🏗️ Architecture & Technical Stack

```
   ┌──────────────────┐      IMAP (SSL)       ┌───────────────────────┐
   │ Enterprise Mail  ├──────────────────────►│  Flask Web Engine     │
   │ Mailbox Folders  │                       │  (app.py)             │
   └──────────────────┘                       └──────────┬────────────┘
                                                         │
                                                         ▼
   ┌──────────────────┐     Interactive UI    ┌───────────────────────┐
   │ Web Dashboard /  │◄──────────────────────┤ Email Parser & API    │
   │ Preview Interface│                       │ Endpoint Layer        │
   └──────────────────┘                       └───────────────────────┘
```

* **Backend:** Python 3, Flask, `imaplib`, `email`
* **Frontend:** Vanilla JavaScript (ES6+), HTML5, CSS3
* **Deployment:** Vercel Serverless (`vercel.json`) / WSGI

---

## 📁 Repository Structure

```
.
├── app.py              # Main Flask server handling IMAP connections & email APIs
├── index.html          # Web dashboard for mailbox viewing, filtering & email previews
├── vercel.json         # Serverless deployment configuration
├── requirements.txt    # Required Python dependencies
└── README.md           # Project documentation
```

---

## 🚀 Setup & Installation

### 1. Prerequisites
* Python 3.9 or higher
* IMAP-enabled email account (e.g., Gmail with App Password enabled)

### 2. Local Setup

```bash
# Clone the repository
git clone https://github.com/your-username/mail-tool-main.git
cd mail-tool-main

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Environment Setup & Execution

Set up your IMAP credentials in your environment or `.env` file:

```env
IMAP_HOST=imap.gmail.com
IMAP_PORT=993
IMAP_USER=your_email@organization.com
IMAP_PASS=your_app_password
```

Run the application:

```bash
python app.py
```
Open `http://localhost:5000` in your web browser.

---

## ☁️ Serverless Deployment (Vercel)

1. Install Vercel CLI: `npm i -g vercel`
2. Run `vercel` in the project root folder.
3. Add environment variables in **Vercel Project Settings**.

---

## 🛡️ License & Contributing

Open-source under the **MIT License**. Contributions are welcome!
