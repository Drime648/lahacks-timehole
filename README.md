# Timehole

**Reclaim your focus with AI-driven network filtering.**

Timehole is a self-hosted, network-level productivity suite that combines a DNS server and an intelligent web proxy to filter distractions at the source. Unlike browser extensions that are easily bypassed, Timehole operates at the application network layer, using Gemma (LLM) to contextually analyze traffic against your specific professional goals.

##  Overview

Distractions are engineered to be addictive. Timehole fights back by turning your home network into a productivity powerhouse. By defining what you want to focus on in natural language, Timehole acts as a personalized classifier, deciding in real-time whether a request is productive or a "timehole."

- **Layered Defense:** DNS for fast, coarse blocking of egregious distractions; Proxy for deep, contextual analysis of URLs and content.
- **AI-Powered Context:** Uses Gemma to evaluate if content aligns with your stated goals (e.g., "Is this YouTube video about React hooks or cat memes?").
- **Centralized Control:** A unified web dashboard for configuration, schedules, and detailed productivity analytics.

## Tech Stack

- **Docker:** Containerized deployment for easy self-hosting (e.g., on a Raspberry Pi or Linux VM).
- **Gemma:** Lightweight LLM for contextual classification of requests.
- **MongoDB Atlas:** Centralized storage for user goals, blacklists, logs, and cached AI decisions.
- **Python:** Core logic for the DNS relay and Proxy server.
    - **dnslib:** High-performance DNS packet handling.
    - **Cryptography:** TLS/SSL certificate management for HTTPS inspection.
- **React:** Modern, responsive interface for configuration and dashboards.

## Architecture

1.  **Web Interface:** Users set focus schedules, block categories (e.g., "Social Media"), and provide a "Focus Paragraph" describing their current work.
2.  **DNS Relay:** Fast-path filtering. Blocks domains based on direct blacklists and broad categories.
3.  **Intelligent Proxy:** Inspects request URLs and (optionally) response content. It queries the LLM: *"Given the user's goal of 'Building a FastAPI backend', is 'reddit.com/r/gaming' on topic?"*
4.  **Database:** Shared state between the proxy and web interface ensures real-time updates and persistent logging.

## Key Features

- **Goal-Oriented Filtering:** Natural language goal setting instead of rigid URL lists.
- **Focus Modes:** Schedule specific "Study" or "Work" times.
- **Analytics Dashboard:** Visualize where your time goes with aggregated logs and blocked-request metrics.
- **Heuristic + AI Hybrid:** Combines the speed of rule-based matching with the intelligence of LLMs.

---

## How to Install

1. Set up application on server in your network - docker compose up --build -d
2. Set up DNS Server - Go into WiFi settings, edit DNS server assignment, set to manual, turn on IPv4, set preferred DNS to the server
3. Make an account on the web interface on port 3000.
4. Go to the web proxy step and download the certificate.
5. Go into browser settings and import the certificate.
6. Go into browser settings and set proxy to point to the server on port 8080.

*Developed for LAHacks 2026*
