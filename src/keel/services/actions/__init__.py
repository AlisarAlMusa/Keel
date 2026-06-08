"""Action pattern home (services/actions/) — every side-effecting write.

Constitution Principle III + ARCH §7: every write goes through the SAME six-step
pattern (validate -> approval -> single TX(write+outbox) -> audit). Implemented
once here and instantiated per action (enroll, waitlist, petition, major-change,
graduation) in later phases. Empty in Phase 0.
"""
