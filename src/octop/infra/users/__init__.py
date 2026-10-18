"""User domain.

Contents:
    identity  — ``User`` / ``Role`` / ``UserToken`` dataclasses
    manager   — ``UserManager`` lifecycle (bootstrap admin, create/disable)
    password  — argon2id password hashing
"""

from __future__ import annotations
