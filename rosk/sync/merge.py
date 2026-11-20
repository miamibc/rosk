"""Field-level three-way merge shared by the Tasks and Keep adapters."""


def three_way(local, base, remote, fields, remote_newer, prefer=None):
    """Returns (changes_to_apply_locally, conflicts).

    local/base/remote are dicts. For each field:
      - changed only remotely -> take remote
      - changed only locally  -> keep local
      - changed on both sides -> `prefer[field](l, r)` if given, else the newer side wins
    """
    prefer = prefer or {}
    changes, conflicts = {}, []
    for f in fields:
        l, b, r = local.get(f), base.get(f), remote.get(f)
        if r == b or r == l:
            continue
        if l == b:
            changes[f] = r
            continue
        if f in prefer:
            winner = prefer[f](l, r)
        else:
            winner = r if remote_newer else l
        conflicts.append((f, l, r))
        if winner != l:
            changes[f] = winner
    return changes, conflicts


def done_wins(l, r):
    return "done" if "done" in (l, r) else l
