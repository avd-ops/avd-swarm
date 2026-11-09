# Action catalog (`catalog/actions/`)

Every reversible action `avd-swarm` may propose and execute through the governance gate (`templates/workflows/approve.yml` and `gate/avd_gate/apply.py`) is declared as a YAML manifest here ([spec.md](../../docs/spec.md) §5a, [governance.md](../../docs/governance.md) §2.1 & §2.6).

## Rules enforced by `scripts/check_actions.py`

1. **Protected path & protected fields:** `catalog/**` is protected in `governance/protected-paths.yml`. Any change that adds an action or loosens `preconditions`, `limits`, `verify`, `rollback`, or `runCommand.sha256` is classified as capability-raising by `scripts/check_timelock.py` (72-hour time-lock in solo mode or two code-owner approvals in team mode).
2. **Host & power preconditions (§5a):** Every `host` and `power` action must declare all nine §5a precondition keys (`notDrainedByOthers`, `restoreDrainStateFound`, `noActiveSessions`, `notPowerLocked`, `notScalingExcluded`, `deallocatedNotUnhealthy`, `hostPoolAvailableHostsAbove`, `oneHostPerPoolAtATime`, `serviceHealthClear`).
3. **Run Command scripts pinned by SHA-256 (brain H6):** Any action with `runCommand` must point to a fixed Windows PowerShell 5.1 script under `catalog/actions/scripts/`, match its exact SHA-256 digest, declare its dedicated write identity (`approve-<action>`), and set `deleteRunCommandAfter: true`.
4. **Positive verification & rollback (brain H5):** Every action requires `verify.positiveEvidenceRequired: true` with non-empty verification fields and `rollback.restoreStateFound: true`.
