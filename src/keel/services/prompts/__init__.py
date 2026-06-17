"""Versioned tool prompt templates (spec Appendix C).

These are NOT the agent's system prompt. Each Day-5 tool that needs an LLM
generation step (narrative, proposal, draft) imports a builder from here, fills
in engine-supplied variables, and makes one focused LLM call. Keeping prompts in
source control (never inlined in tool logic) is the standard (CLAUDE.md §7).

Each module exports a ``PROMPT_VERSION`` string and a ``build(...) -> str`` builder.
"""

from __future__ import annotations
