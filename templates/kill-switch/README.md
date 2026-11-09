# Kill Switch & Watchdog Template (`templates/kill-switch/`)

Implements the `avd-swarm` multi-layer kill switch and watchdog receiver ([`docs/governance.md`](../../docs/governance.md) §2.3 & §2.4; R5, R6, R13, V1-15).

- **Capability-removing identity (`R5`):** The Consumption Logic App runs under its own user-assigned managed identity (`avd-swarm-switch-*`) with a custom role that can only write resource-group tags (`avd-swarm-switch`), delete federated credentials on the `approve` identity, delete agent role assignments, and stop Container Apps. It can never create credentials, grant roles, start Container Apps, or reset the switch to `run`.
- **ABAC delegation condition (`V1-15`):** `Microsoft.Authorization/roleAssignments/delete` is restricted by a `conditionVersion: '2.0'` condition to the swarm's specialist agent principal IDs so it can never remove the landing zone's deploy identity, the `stop-spend` identity, or the human operator.
- **Stopping spend is never blocked:** The `stop-spend` identity and environment are never touched by any kill-switch position (`halt-actions`, `halt-agents`, `halt-all`).
- **Only a person resets (`R6`):** Resuming to `run` requires a person running the Cloud Shell reset block as themselves; clearing the tag while any layer is still missing re-trips the switch at the previous position.
- **Validation & Drill (`R13`):** Validated in CI by [`scripts/check_kill_switch.py`](../../scripts/check_kill_switch.py).
