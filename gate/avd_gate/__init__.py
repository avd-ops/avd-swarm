# avd-swarm gate. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""The governance gate's rules and approved write-path runner as pure functions (spec section 5, 5a, 6d, 6f; governance.md).

Includes:
- Evidence provenance (V1-4) and Desktop DevOps data-loss guard (D1) in `evidence.py`
- Pre-execution live re-read, host preconditions (section 5a), `why` validation (C18, section 6f),
  canonical plan hash comparison, positive verification, and redacted log/summary formatting in `apply.py`
"""
