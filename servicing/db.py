"""SQLite schema for the mock servicing system. Money is stored in integer cents."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE loans (
    loan_number             TEXT PRIMARY KEY,
    borrower_name           TEXT NOT NULL,
    last4_ssn               TEXT NOT NULL,
    zip                     TEXT NOT NULL,
    status                  TEXT NOT NULL CHECK (status IN (
                                'current', 'delinquent_30', 'delinquent_60', 'delinquent_90',
                                'forbearance', 'bankruptcy', 'paid_off')),
    servicemember           INTEGER NOT NULL DEFAULT 0,
    principal_balance_cents INTEGER NOT NULL,
    monthly_payment_cents   INTEGER NOT NULL,
    amount_due_cents        INTEGER NOT NULL,
    next_due_date           TEXT,               -- ISO date; NULL when paid off
    escrow_balance_cents    INTEGER NOT NULL
);

CREATE TABLE payments (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    loan_number      TEXT NOT NULL REFERENCES loans(loan_number),
    amount_cents     INTEGER NOT NULL,
    scheduled_date   TEXT NOT NULL,
    account_last4    TEXT NOT NULL,
    confirmation     TEXT NOT NULL
);

CREATE TABLE comments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    loan_number  TEXT NOT NULL REFERENCES loans(loan_number),
    author       TEXT NOT NULL,                  -- 'agent' or 'staff' (seeded history)
    text         TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    loan_number  TEXT NOT NULL REFERENCES loans(loan_number),
    type         TEXT NOT NULL,
    note         TEXT NOT NULL
);

CREATE TABLE transfers (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    loan_number  TEXT,                           -- NULL if the caller never identified a loan
    reason       TEXT NOT NULL,
    note         TEXT NOT NULL
);

-- Every tool call, in order. The grader reads this alongside the end state.
CREATE TABLE tool_calls (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    tool         TEXT NOT NULL,
    args_json    TEXT NOT NULL,
    outcome      TEXT NOT NULL CHECK (outcome IN ('ok', 'error', 'blocked', 'would_block')),
    detail       TEXT NOT NULL DEFAULT ''
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    if path.exists():
        path.unlink()
    conn = connect(path)
    conn.executescript(SCHEMA)
    return conn
