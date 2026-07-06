"""Presentation layer — pure functions that shape domain objects into the
strings and widget payloads the UI consumes.

Presenters have no I/O and no framework dependencies beyond the domain types they
render. They are leaf modules: services, tools, and workers depend on them, never
the reverse. Extracting presentation here (per CLAUDE.md "Presenters") keeps the
card/markdown formatting out of tools and services without changing any output —
the produced strings and payload dicts are byte-for-byte identical.
"""
