---
title: AI Finance Coach
hide:
  - navigation
  - toc
---

<div class="afc-hero" markdown>
<span class="kicker">Self-hosted · open source · France &amp; Italy</span>

<h1>Your household's money, explained with your own numbers.</h1>

A private coach for one household. It syncs your bank accounts through the EU's open-banking rules (PSD2), sorts every
payment, and answers questions like "why was July expensive?" with figures **computed by code**, never guessed by a model.
Everything lives in an encrypted database on your own machine.

[Start in ten minutes](getting-started/quickstart.md){ .md-button .md-button--primary }
[Try the demo household](getting-started/demo.md){ .md-button }
[Back to the home page](https://mmornati.github.io/ai-finance-coach/){ .md-button }
</div>

<video class="cast" src="assets/video/tour.mp4" poster="assets/video/tour.webp" autoplay muted loop playsinline></video>

## Where to start

<div class="grid cards" markdown>

-   :material-rocket-launch-outline:{ .lg } **Getting started**

    ---

    What the coach is, the three principles it never breaks, and the four ways in: demo, install, quickstart, server.

    [:octicons-arrow-right-24: Getting started](getting-started/index.md)

-   :material-flask-outline:{ .lg } **Try the demo**

    ---

    The synthetic Rossi household on your machine: two years of invented payments, no bank and no model needed.

    [:octicons-arrow-right-24: Try the demo](getting-started/demo.md)

-   :material-server-outline:{ .lg } **Run it on a server**

    ---

    Docker Compose on a home server, reached through Tailscale, with the daily job and encrypted backups.

    [:octicons-arrow-right-24: Run it on a server](getting-started/server.md)

-   :material-view-dashboard-outline:{ .lg } **User guide**

    ---

    Every page of the web app with screenshots: dashboard, transactions, subscriptions, net worth, the kids' money...

    [:octicons-arrow-right-24: User guide](guide/index.md)

-   :material-robot-outline:{ .lg } **AI models**

    ---

    Claude Code, the Anthropic API, OpenRouter, Eden AI or a local Ollama: where a model is used, and where it never is.

    [:octicons-arrow-right-24: AI models](configuration/models.md)

-   :material-shield-lock-outline:{ .lg } **Privacy modes**

    ---

    Coarse redaction by default, `local_only` for a fully local model, `offline` for no network at all.

    [:octicons-arrow-right-24: Privacy modes](configuration/privacy.md)

</div>

## Three things it never does

| | |
|---|---|
| **Put a model in the data path** | Sync, de-duplication, recurring payments, forecasts and budgets are plain code. A model labels only the merchants your rules and memory leave open, and writes the coach's words. |
| **Let a model do arithmetic** | The coach calls read-only tools that return computed, redacted results (<span class="pseudo">adult-1</span>, <span class="pseudo">account-main-1</span>, <span class="pseudo">h_946d99720b</span>), and every claim cites the payments behind it. |
| **Change your records by itself** | It can only *propose* a change to the household memory. You read the diff and accept it yourself, in a terminal. |

!!! note "Every name, bank, merchant and amount in this documentation is invented"
    The screenshots and screencasts come from the demo household built by `scripts/demo/seed_demo.py`: Anna and Luca Rossi,
    their children Mia and Noa, Banque Aurore, Nova Bank and Credit Horizon. Nothing in these pages comes from a real person.

!!! warning "Not financial advice"
    The coach explains your own spending and saving habits. It does not recommend investments, loans or insurers, and gives no
    tax or legal advice. Estimates (a mortgage renegotiation, a cancellation date) are labelled as estimates.
