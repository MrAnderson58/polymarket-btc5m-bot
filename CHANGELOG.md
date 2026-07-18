# Changelog

Все значимые изменения проекта документируются здесь.

Формат: [Keep a Changelog](https://keepachangelog.com/) по смыслу, сгруппировано по релизам.

---

## [v7.1-alpha] — AI Trading Terminal Alpha

**Released:** 2026-07-18

Первая полноценная Alpha-версия AI Trading Terminal.

### Новое

#### AI Trading Terminal

- Новый модуль `bot/terminal`
- Telegram Terminal UI
- Screen Controller
- Command Dispatcher
- Event Bus
- Session Manager
- Telemetry

#### Scanner

- Universal Scanner
- Ranking Engine
- Instrument Registry
- Market Profiles

#### Decision Engine

- Decision Card
- Explainability (`/why`)
- Score Breakdown
- Risk Display
- Entry / Stop / TP

#### Portfolio Intelligence

- Portfolio Risk
- Sector Exposure
- Crypto Exposure
- Portfolio Advice

#### Research

- Claude Review (поверх готового DecisionCard)
- Decision Review
- Context Builder
- Template fallback (без Claude)

#### Morning Brief

- Daily Brief Builder
- Markets / Crypto / Stocks
- Portfolio Summary
- Watchlist Updates

#### Watchlist

- Favorites
- Persistent Watchlists

#### Alerts

- Rule Engine
- Alert Models

#### Timeline

- Symbol Timeline (read-only)

#### Testing

- 100+ terminal tests

---

## S5 — AI Research & Learning

- AI Audit Engine
- Telegram Research
- Learning Worker
- Paper Performance
- Daily Reports

---

## S4 — Learning Engine

- Learning Queue
- Performance Tracking
- Pattern Validation

---

## S3 — Pattern Intelligence

- Pattern Intelligence
- Pattern Evidence
- News Collector

---

## S2 — Decision Engine

- Trading Decision Engine
- Validation Pipeline
- Diagnostics
