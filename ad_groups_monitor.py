"""Monitor Active Directory group membership and report changes between runs."""
import argparse
import json
import os
import sys


def fetch_members(server, user, password, base_dn, groups):
    """Return {group_name: sorted list of member DNs} using LDAP (requires ldap3)."""
    from ldap3 import Connection, Server, SUBTREE
    from ldap3.utils.conv import escape_filter_chars

    conn = Connection(Server(server, use_ssl=True), user=user, password=password, auto_bind=True)
    result = {}
    for group in groups:
        conn.search(base_dn, f"(&(objectClass=group)(cn={escape_filter_chars(group)}))",
                    SUBTREE, attributes=["member"])
        members = set()
        for entry in conn.entries:
            members.update(str(m) for m in entry.member.values)
        result[group] = sorted(members)
    conn.unbind()
    return result


def diff_snapshots(old, new):
    """Return {group: {"added": [...], "removed": [...]}} for groups that changed."""
    changes = {}
    for group in sorted(set(old) | set(new)):
        before, after = set(old.get(group, [])), set(new.get(group, []))
        if before != after:
            changes[group] = {"added": sorted(after - before), "removed": sorted(before - after)}
    return changes


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--server", required=True, help="LDAP server host")
    p.add_argument("--user", required=True, help="Bind DN or user principal name")
    p.add_argument("--base-dn", required=True)
    p.add_argument("--group", action="append", required=True, help="Group CN (repeatable)")
    p.add_argument("--state", default="ad_groups_state.json", help="Snapshot file")
    args = p.parse_args(argv)

    new = fetch_members(args.server, args.user, os.environ["AD_PASSWORD"], args.base_dn, args.group)
    old = None
    if os.path.exists(args.state):
        with open(args.state) as f:
            old = json.load(f)
    with open(args.state, "w") as f:
        json.dump(new, f, indent=2, sort_keys=True)

    if old is None:
        print("Initial snapshot saved.")
        return 0
    changes = diff_snapshots(old, new)
    for group, c in changes.items():
        print(f"[{group}]")
        for m in c["added"]:
            print(f"  + {m}")
        for m in c["removed"]:
            print(f"  - {m}")
    return 1 if changes else 0


if __name__ == "__main__":
    sys.exit(main())
