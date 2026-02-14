# CortexAI: Intelligent Business Intelligence for Tally Prime

CortexAI is a powerful, AI-driven Business Intelligence (BI) assistant designed specifically for Tally Prime. It leverages the Model Context Protocol (MCP), Retrieval-Augmented Generation (RAG), and Large Language Models (LLMs) to provide real-time, conversational insights into your accounting data.

---

## 🚀 Overview

CortexAI acts as a bridge between your Tally Prime instance and an AI "brain". It allows business owners and accountants to ask natural language questions like _"Who owes us the most money?"_ or _"What was our net profit last month?"_ and get instant, accurate answers derived directly from their Tally data.

### Key Features

- **🤖 Conversational Interface**: Interact with Tally via Telegram.
- **🔌 Model Context Protocol (MCP)**: Native integration for dynamic tool discovery and execution.
- **📚 Local RAG**: Semantic search for ledger names and injection of custom business rules (e.g., credit policies).
- **💾 Offline Snapshot Cache**: Access last-known-good data even when Tally is offline.
- **🛡️ Secure & Private**: Highly configurable user access and local embedding processing.

---

## 🛠️ Installation & Setup

### Prerequisites

1.  **Python 3.10+**: Ensure Python is installed and added to your PATH.
2.  **Tally Prime**: Must be running with the **ODBC/XML Server** enabled.
    - Default port is usually `9000`.
    - Ensure a company is loaded.
3.  **Telegram Bot**: Create a bot via [@BotFather](https://t.me/botfather) and get the API Token.
4.  **OpenRouter API Key**: Required for the LLM "brain" (supports various models).

### Step-by-Step Setup

1.  **Clone the Repository**:

    ```bash
    git clone <repository-url>
    cd CortexAI
    ```

2.  **Create a Virtual Environment**:

    ```bash
    python -m venv venv
    venv\Scripts\activate  # Windows
    # source venv/bin/activate  # Linux/Mac
    ```

3.  **Install Dependencies**:

    ```bash
    pip install -r requirements.txt
    ```

4.  **Configure Environment Variables**:
    Create a `.env` file in the root directory:

    ```ini
    # OpenRouter Config
    OPENROUTER_KEY="your_openrouter_api_key"
    MODEL="arcee-ai/trinity-large-preview:free" # Or any other model

    # Tally Config
    TALLY_URL="http://localhost:9000"
    TALLY_COMPANY="Your Company Name"

    # Telegram Config
    TELEGRAM_BOT_TOKEN="your_telegram_bot_token"
    ALLOWED_TELEGRAM_USERS="user_id_1,user_id_2" # Comma-separated IDs
    ```

5.  **Initialize RAG (First Run)**:
    Run the RAG script once to sync your Tally ledgers and business rules to the local vector database:

    ```bash
    python rag.py
    ```

6.  **Start the Bot**:
    ```bash
    python telegram_bot.py
    ```

---

## 🏗️ System Architecture

CortexAI is built on a modular "Orchestrator" pattern that coordinates multiple specialized components.

### Component Breakdown

#### 1. [telegram_bot.py](telegram_bot.py) (The Face)

- Uses `python-telegram-bot` to handle user interactions.
- Manages user authorization via `ALLOWED_TELEGRAM_USERS`.
- Maintains a local `conversation_history` for multi-turn dialogues.

#### 2. [orchestrator.py](orchestrator.py) (The Brain)

- **MCP Client**: Launches and connects to the MCP server.
- **LLM Integration**: Communicates with OpenRouter.
- **Tool Calling Loop**: Implements a logical loop where the LLM can call multiple tools (e.g., `get_trial_balance`, then `get_sundry_debtors`) to formulate a final answer.
- **RAG Injection**: Queries the RAG module to add business context to the system prompt.

#### 3. [tally_mcp.py](tally_mcp.py) (The Data Layer)

- An **MCP Server** built with `FastMCP`.
- **XML Engine**: Translates natural language requests into Tally XML queries.
- **Snapshot Logic**: Implements a `_live_or_cache` wrapper. If Tally is unreachable, it automatically serves the most recent cached snapshot.
- **Tools**: Exposes 11+ dedicated tools for Trial Balance, P&L, Balance Sheet, Ledger Search, etc.

#### 4. [rag.py](rag.py) (The Memory)

- **Local Embeddings**: Uses `SentenceTransformers` (`all-MiniLM-L6-v2`) running locally.
- **ChromaDB**: A persistent vector database stored in `/chroma_db`.
- **Collections**:
  - `ledger_names`: Supports semantic search (e.g., searching "phone" might find "Telephone Expenses").
  - `business_rules`: Stores domain-specific knowledge like "Overdue is 45 days".

#### 5. [snapshot.py](snapshot.py) (The Persistence)

- A lightweight JSON-based caching system.
- Stores data in the `/snapshots` directory.
- Tags data with timestamps to inform the user of "data age" during offline mode.

---

## 🔄 The MCP Interaction Flow

CortexAI follows the standardized Model Context Protocol flow for high reliability:

1.  **Connection**: `orchestrator.py` starts `tally_mcp.py` as a subprocess using the `stdio` transport.
2.  **Discovery**: The Orchestrator calls `list_tools()` to see what capabilities the Tally server has.
3.  **Context**: The Orchestrator gathers business context from RAG.
4.  **Prompting**: A system prompt is built containing the tool list and context.
5.  **Intelligence**: The LLM analyzes the user query. If it needs data, it returns a `TOOL_CALL`.
6.  **Execution**: The Orchestrator executes the tool via MCP, gets the result (live or cached), and feeds it back to the LLM.
7.  **Final Response**: Once sufficient data is gathered, the LLM provides the final human-friendly answer.

---

## 📋 Available Tools

| Tool Name                   | Description                                 |
| :-------------------------- | :------------------------------------------ |
| `get_trial_balance`         | Group-wise Trial Balance (Debit/Credit).    |
| `get_profit_and_loss`       | Revenue, COGS, Expenses, and Net Profit.    |
| `get_balance_sheet`         | Assets, Liabilities, Capital, and Loans.    |
| `get_all_ledgers`           | Full list of ledgers, groups, and balances. |
| `get_sundry_debtors`        | Receivables sorted by highest amount.       |
| `get_sundry_creditors`      | Payables sorted by highest amount.          |
| `get_transactions_for_date` | Day-specific voucher list.                  |
| `search_ledger`             | Partial/Fuzzy search for specific accounts. |
| `get_tally_status`          | Returns Tally connectivity and cache ages.  |

---

## 📝 Usage Examples

- **Receivables**: _"Who owes us money right now?"_
- **Profitability**: _"What was our gross margin for the year?"_
- **Vouchers**: _"Show me transactions from yesterday."_
- **Audit**: _"Check if any debtor has exceeded 90 days based on our policy."_
- **Health Check**: _"Is Tally connected?"_

---

## ⚠️ Notes

- **Educational Mode**: Fully compatible with Tally Prime Educational Mode.
- **TDL-Free**: Does not require any custom TDL (Tally Definition Language) files; uses built-in collections.
- **Currency**: Formats output in Indian Rupee (₹) by default.
