# Digital Twin AI

A self-hosted, structured-memory conversational AI platform. Digital Twin AI uses a dual-model architecture to classify user interactions into a persistent taxonomy and generate intelligent, context-aware responses. It features autonomous modes (Live Mode and Thought Network) and supports multi-modal file inputs.

## 📋 Table of Contents
- [Features](#features)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [File Structure](#file-structure)
- [API Documentation](#api-documentation)
- [Technical Notes](#technical-notes)

## ✨ Features

- **Structured Memory Taxonomy:** Automatically organizes conversations into a hierarchical tree of categories and memory items.
- **Dual-Model System:**
  - **Classifier:** A lightweight model for data extraction, classification, and memory mediation.
  - **Analytical:** A powerful model for reasoning, answering, and autonomous thought generation.
- **Autonomous Modes:**
  - **Live Mode:** The AI can send unsolicited follow-up messages based on conversation context.
  - **Thought Network:** The AI periodically generates "thoughts" (predictions/suggestions) based on memory analysis.
- **Multi-Modal Support:** Handles text, images (with OCR), PDFs, DOCX, CSV, JSON, and more.
- **OpenAI-Compatible:** Works with any API provider (OpenAI, Ollama, vLLM, local models, etc.) via standard `/chat/completions` endpoints.
- **Privacy-First:** All data stored in local SQLite; no external cloud dependencies for storage.
- **Real-Time UI:** Event-driven frontend with SSE (Server-Sent Events) for live updates.

## 🏗️ Architecture

The system employs a **Classifier-Analytical Handoff** pattern:

1.  **Input:** User sends a message (text + optional files).
2.  **Classification:** The **Classifier Model** analyzes the input, extracts facts, and updates the SQLite taxonomy tree.
3.  **Routing:** A lightweight "handoff" step packages the user's exact message and current taxonomy overview.
4.  **Analysis:** The **Analytical Model** receives the context, retrieves relevant memory paths from SQLite, and generates a response.
5.  **Output:** The response is saved, classified again, and returned to the user.
6.  **Autonomy:** Background schedulers periodically trigger "Live" or "Thought" modes based on configured intervals.

## 🛠️ Prerequisites

- **Python 3.10+**
- **pip** (Python package manager)
- **Tesseract OCR** (Optional, for image text extraction)
- **LibreOffice** (Optional, for advanced document conversion if needed, though `pypdf`/`docx` are primary)

## 📥 Installation

1.  **Clone the repository:**
    ```bash
    git clone <repository-url>
    cd <project-directory>
    ```

2.  **Create a virtual environment:**
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows: venv\Scripts\activate
    ```

3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
    *Note: If `requirements.txt` is not provided, install manually:*
    ```bash
    pip install fastapi uvicorn httpx python-multipart pypdf python-docx Pillow pytesseract
    ```

4.  **Initialize the database:**
    The database (`data/digital_twin.sqlite3`) is automatically created on the first run.

## ⚙️ Configuration

No environment variables are required. All configuration is done via the Web UI.

1.  **Start the server:**
    ```bash
    python run.py
    ```
    The server will start at `http://127.0.0.1:8000`.

2.  **Configure Models:**
    - Navigate to the **"Settings"** tab.
    - Add two model profiles:
        1.  **Classifier:** Set role to `classifier`. Use a cheap/fast model (e.g., `gpt-3.5-turbo`, `llama-3-8b`).
        2.  **Analytical:** Set role to `analytical`. Use a powerful model (e.g., `gpt-4-turbo`, `claude-3-opus`, `llama-3-70b`).
    - Enter the `Base URL` (e.g., `https://api.openai.com/v1` or `http://localhost:11434/v1`) and `API Key`.

3.  **Adjust Settings:**
    - Configure intervals for **Live Mode** and **Thought Network** (in seconds).
    - Set TTL for short-term memory thoughts.

## 🚀 Usage

### Starting a Conversation
1.  Click **"+ New Chat"**.
2.  Type a message in the input box.
3.  Optionally attach files (PDF, DOCX, TXT, Images, etc.).
4.  Click **Send**.

### Autonomous Features
- **Live Mode:** Toggle the "Live Chat" switch. The AI will periodically check if a follow-up is needed.
- **Thought Network:** Toggle the "Thought Network" switch. The AI will generate "Thoughts" (suggestions/predictions) which appear in the chat area. You can choose to "Continue" (send as a new message) or "Ignore" them.

### Viewing Memory
- Go to the **"Memory & Tree"** tab to visualize the taxonomy.
- Click **"Export JSON"** to download the entire memory structure.

### Monitoring
- Go to the **"Monitoring"** tab to view real-time logs of AI interactions, including request/response details and timing.

## 📂 File Structure

```
.
├── run.py              # Entry point (Uvicorn server)
├── main.py             # FastAPI application, routes, and background scheduler
├── ai.py               # AI logic: chat interface, classifier, analytical turn, prompts
├── db.py               # SQLite database schema, CRUD operations, and taxonomy management
├── extract.py          # File extraction logic (PDF, DOCX, OCR, etc.)
├── __init__.py         # Package initializer
├── static/
│   ├── index.html      # Main frontend interface
│   ├── app.js          # Frontend logic (API calls, UI rendering, SSE)
│   └── styles.css      # Styling
└── data/
    └── digital_twin.sqlite3  # Local database (created on first run)
```

## 📡 API Documentation

The API is RESTful and JSON-based. Key endpoints include:

- `GET /api/settings`: Get current settings and model profiles.
- `POST /api/settings`: Update settings.
- `POST /api/models`: Save a new model profile.
- `DELETE /api/models/{id}`: Remove a model.
- `POST /api/models/test/{id}`: Test connectivity to a model.
- `GET /api/conversations`: List all conversations.
- `POST /api/conversations`: Create a new conversation.
- `GET /api/conversations/{id}`: Get conversation details, messages, and thoughts.
- `PATCH /api/conversations/{id}`: Toggle Live Mode or Thought Network.
- `POST /api/chat/{id}`: Send a message (supports file uploads via `multipart/form-data`).
- `GET /api/taxonomy`: Get the current memory tree and overview.
- `GET /api/events`: SSE endpoint for real-time updates.

## 💡 Technical Notes

- **Database:** Uses SQLite with WAL (Write-Ahead Logging) mode for better concurrency.
- **AI Prompts:** The system uses complex system prompts for classification and analytical reasoning. These are defined in `ai.py`.
- **Memory Retrieval:** The analytical model requests specific paths from the taxonomy, and the classifier mediates the raw data to ensure relevance and prevent hallucination.
- **Autonomous Scheduler:** A background `asyncio` task runs every 2 seconds to check intervals and trigger autonomous modes.

## 🤝 Development

To contribute:
1.  Fork the repository.
2.  Create a feature branch.
3.  Make your changes.
4.  Submit a pull request.

## 📄 License

This project is open-source. Please check the `LICENSE` file for details.
