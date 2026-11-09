"""A plain-file overlap guard for the notebook, so two runs never touch Bronze
at once.

Concurrent runs are now more dangerous than they used to be: OPTIMIZE/VACUUM
(see bronze_merge._compact) rewrite and delete files, so a second run reading
or writing the same table mid-compaction can lose Delta's optimistic
concurrency check ("a concurrent transaction deleted data this operation
read"). That's exactly what happened when a manual trigger landed 90 seconds
before a scheduled run.

Uses a plain file under Files/ (not Tables/) — this is a simple existence/mtime
check, not a Delta commit, so the OneLake mount's rename restriction that forces
write_deltalake() onto the abfss endpoint elsewhere in this project does not
apply here; a plain write through the mount is fine.

Not a distributed lock in the strict sense (there is a race between the
existence check and the write), but the pipeline only ever has one scheduled
trigger plus occasional manual ones, so the realistic failure mode is two runs
starting a minute or so apart — which this closes — not a tight race.
"""

import datetime
import os

DEFAULT_LOCK = "/lakehouse/default/Files/_locks/ngp2_spc_refresh.lock"

# Longer than any observed run (~10 min) so a genuinely active run's lock stays
# held, but short enough that a run killed without cleanup (a crashed session,
# not a normal failure — normal failures still hit the `finally` and release
# it) self-clears well before the next scheduled trigger.
MAX_AGE_SECONDS = 20 * 60


def acquire(path=DEFAULT_LOCK, max_age_seconds=MAX_AGE_SECONDS):
    """Return True if the lock was acquired, False if another run holds it."""
    if os.path.exists(path):
        age = datetime.datetime.now().timestamp() - os.path.getmtime(path)
        if age < max_age_seconds:
            return False
        # Stale — the process that wrote it never reached release(). Treat as
        # abandoned rather than block every future run forever.
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(datetime.datetime.now(datetime.timezone.utc).isoformat())
    return True


def release(path=DEFAULT_LOCK):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
