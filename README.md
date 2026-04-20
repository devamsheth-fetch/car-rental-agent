# Enterprise Car Rental Agent

A high-end AI booking agent for Enterprise Rent-A-Car, built on the Fetch.ai **uAgents** framework and powered by the **Enterprise MCP** (Model Context Protocol).

## Features

- **Natural Language Search**: Find vehicles in any location for any date/time.
- **Vehicle Intelligence**: Get details on specific cars and rate breakdown.
- **Reservation Management**: Book cars, modify reservations, or cancel them through chat.
- **Rich UI**: Interactive markdown-based cards for comparing vehicle options.
- **Account Integration**: Login to your Enterprise Plus account to view free days and points.

## Setup

### 1. Prerequisites
- **Python 3.10+**
- **Node.js/npx** (Required to run the Enterprise MCP server)
- **ASI1 API Key** (or any OpenAI-compatible LLM)

### 2. Installation
```bash
pip install -r requirements.txt
```

### 3. Configuration
Copy `.env.example` to `.env` and add your `ASI1_API_KEY`.
```bash
cp .env.example .env
```
Ensure that `ENTERPRISE_MCP_COMMAND` and `ENTERPRISE_MCP_ARGS` point to your local Enterprise MCP build.

### 4. Run the Agent
```bash
python agent.py
```

## How it Works

The agent uses the **ASI1 reasoning loop** to decide when to call Enterprise tools.
1. When you ask for a car at "LAX", the agent triggers the Enterprise MCP via `node`.
2. The MCP server uses stealth browser automation to fetch live data from Enterprise.
3. The results are formatted into clean UI cards for your review.

---
*Built with ❤️ for the Fetch.ai Innovation Lab.*
