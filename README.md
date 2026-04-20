# 🚗 Smart Car Rental Concierge Agent

A high-precision autonomous booking agent built on the Fetch.ai **uAgents** framework and powered by the **Model Context Protocol (MCP)**. This agent transforms fragmented car rental research into a curated, bookable mobility solution in seconds.

## 🌟 Key Features

### 🧠 Smart Discovery & Curation
Instead of dumping endless lists of cars, the agent act as a consultant. It understanding your "Drive"—whether it’s a high-stakes business trip or a 5-person family vacation—and curates a personalized **Top 3 recommendation list**, explaining exactly why each vehicle fits your specific needs.

### 💳 Native Financial Integration
Equipped with a built-in **Stripe payment bridge**, the agent autonomously handles:
- **Prepaid (Pay Now)**: Secure digital checkouts with zero manual data entry.
- **Postpaid (Pay at Location)**: Intelligent collection of vendor-required card guarantees.

### 🌍 MCP-Powered Intelligence
Uses a specialized MCP server to perform deep, real-time searches across global inventories, finding the best rates and live availability that traditional search engines miss.

### 🏎️ Zero-Friction Booking
A fully automated booking pipeline. Once a user confirms their preference and pays, the agent finalizes the booking instantly using autonomous background protocols—eliminating the need for repetitive manual forms.

---

## 🛠️ Setup

### 1. Prerequisites
- **Python 3.10+**
- **ASI1 API Key** (or any OpenAI-compatible LLM)
- **Stripe Account** (For native payments)

### 2. Installation
```bash
pip install -r requirements.txt
```

### 3. Configuration
Copy `.env.example` to `.env` and fill in your keys:
- `ASI1_API_KEY`: Your LLM provider key.
- `STRIPE_SECRET_KEY`: Your Stripe secret key for payment processing.

### 4. Run the Agent
```bash
python agent.py
```

---

## 🏗️ Architecture

1. **Smart Discovery Phase**: The agent analyzes the user's intent and asks for Trip Purpose, Guest Count, and Payment Preferences.
2. **Autonomous Tooling**: Triggers the **MCP Server** via `stdio` to fetch live global data.
3. **Curation Engine**: Filters hundreds of results into a curated "Top 3" list with personalized "Why this fits" logic.
4. **Financial Settlement**: Triggers Stripe for Prepaid options or handles secure card guarantees for Postpaid.
5. **Finalization**: Executes the booking via the Travelopro API using secure placeholders after payment confirmation.

---
*Built with ❤️ for the Fetch.ai Innovation Lab.*
