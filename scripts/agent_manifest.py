#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Mnemotron Wiki — scripts/agent_manifest.py
# Originally developed by Patrick R. Wallace, Hamilton College LITS.
# Licensed under the GNU General Public License v3 or later.
# See <https://www.gnu.org/licenses/gpl-3.0.html>.

"""
agent_manifest.py — Coordination manifest for parallel synthesis agents.

Tracks which Claude Code agents are active and which wiki pages they claim,
preventing conflicting concurrent edits.  The manifest is a JSON file at
wiki/.agent-manifest.json.

Before launching a parallel agent, call:
    python scripts/agent_manifest.py check wiki/topics/foo.md [...]
If the exit code is non-zero, a conflict exists and the launch should wait.

Each agent should call claim at start and release at finish.  The prune step
removes any entries older than STALE_HOURS that were never released (handles
crashed agents).

CLI:
    python scripts/agent_manifest.py list
    python scripts/agent_manifest.py claim  AGENT_ID DESCRIPTION page [page ...]
    python scripts/agent_manifest.py release AGENT_ID
    python scripts/agent_manifest.py check   page [page ...]
    python scripts/agent_manifest.py prune  [--hours N]

Import (from within a Claude task):
    from scripts.agent_manifest import AgentManifest
    m = AgentManifest()
    conflicts = m.check_conflicts(["wiki/topics/my-topic.md"])
    if not conflicts:
        m.claim("my-agent-id", "Depth pass: my-topic",
                ["wiki/topics/my-topic.md"])
    ...
    m.release("my-agent-id")
"""

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

WIKI_ROOT      = Path(__file__).resolve().parent.parent
MANIFEST_PATH  = WIKI_ROOT / "wiki" / ".agent-manifest.json"
STALE_HOURS    = 4   # agents not released within this window are considered stale


# ---------------------------------------------------------------------------
# AgentManifest class
# ---------------------------------------------------------------------------

class AgentManifest:
    """Read/write interface to the agent coordination manifest."""

    def __init__(self, path: Path = MANIFEST_PATH):
        self.path = path
        self._data: dict = self._load()

    # -- persistence --------------------------------------------------------

    def _load(self) -> dict:
        if not self.path.exists():
            return {"agents": {}}
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _parse_iso(s: str) -> datetime:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))

    def _active_agents(self) -> dict:
        """Return agents dict with stale entries already filtered out."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=STALE_HOURS)
        active = {}
        for agent_id, entry in self._data.get("agents", {}).items():
            try:
                started = self._parse_iso(entry["started"])
            except (KeyError, ValueError):
                continue
            if started >= cutoff:
                active[agent_id] = entry
        return active

    # -- public API ---------------------------------------------------------

    def claim(self, agent_id: str, description: str, target_pages: list) -> None:
        """Register *agent_id* as active and owning *target_pages*.

        Raises ValueError if any target page is already claimed by a different
        active agent.
        """
        conflicts = self.check_conflicts(target_pages, exclude=agent_id)
        if conflicts:
            lines = []
            for aid, entry in conflicts.items():
                lines.append(f"  {aid}: {entry.get('description', '?')} "
                             f"→ {', '.join(entry.get('target_pages', []))}")
            raise ValueError(
                f"Cannot claim pages — conflicts with active agent(s):\n"
                + "\n".join(lines)
            )
        agents = self._data.setdefault("agents", {})
        agents[agent_id] = {
            "description":  description,
            "target_pages": [str(p) for p in target_pages],
            "started":      self._now_iso(),
            "status":       "running",
        }
        self._save()

    def release(self, agent_id: str) -> bool:
        """Mark *agent_id* as complete and remove it from the manifest.

        Returns True if the agent was present, False if it was not found.
        """
        agents = self._data.get("agents", {})
        if agent_id not in agents:
            return False
        del agents[agent_id]
        self._save()
        return True

    def check_conflicts(self, target_pages: list, exclude: str = "") -> dict:
        """Return active agents whose claimed pages overlap with *target_pages*.

        Pass *exclude* to ignore a specific agent_id (useful when re-claiming
        after a crash where the old entry was never released).

        Returns a dict {agent_id: entry} of conflicting agents.
        """
        target_set = {str(p) for p in target_pages}
        conflicts = {}
        for agent_id, entry in self._active_agents().items():
            if agent_id == exclude:
                continue
            claimed = set(entry.get("target_pages", []))
            if claimed & target_set:
                conflicts[agent_id] = entry
        return conflicts

    def list_active(self) -> dict:
        """Return all active (non-stale) agents."""
        return self._active_agents()

    def prune_stale(self, max_age_hours: float = STALE_HOURS) -> int:
        """Remove agents whose started timestamp is older than *max_age_hours*.

        Returns the number of stale entries removed.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        agents = self._data.get("agents", {})
        stale_ids = []
        for agent_id, entry in agents.items():
            try:
                started = self._parse_iso(entry["started"])
            except (KeyError, ValueError):
                stale_ids.append(agent_id)
                continue
            if started < cutoff:
                stale_ids.append(agent_id)
        for aid in stale_ids:
            del agents[aid]
        if stale_ids:
            self._save()
        return len(stale_ids)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_list(args, manifest: AgentManifest) -> int:
    active = manifest.list_active()
    if not active:
        print("No active agents.")
        return 0
    print(f"{len(active)} active agent(s):")
    for agent_id, entry in active.items():
        print(f"\n  [{agent_id}]  {entry.get('status', '?')}  "
              f"started {entry.get('started', '?')}")
        print(f"  Description: {entry.get('description', '—')}")
        pages = entry.get("target_pages", [])
        if pages:
            print("  Pages:")
            for p in pages:
                print(f"    {p}")
    return 0


def _cmd_claim(args, manifest: AgentManifest) -> int:
    try:
        manifest.claim(args.agent_id, args.description, args.pages)
        print(f"Claimed {len(args.pages)} page(s) for agent '{args.agent_id}'.")
        return 0
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def _cmd_release(args, manifest: AgentManifest) -> int:
    found = manifest.release(args.agent_id)
    if found:
        print(f"Released agent '{args.agent_id}'.")
        return 0
    print(f"Agent '{args.agent_id}' not found in manifest.", file=sys.stderr)
    return 1


def _cmd_check(args, manifest: AgentManifest) -> int:
    conflicts = manifest.check_conflicts(args.pages)
    if not conflicts:
        print("No conflicts — pages are free to claim.")
        return 0
    print(f"CONFLICT: {len(conflicts)} active agent(s) own overlapping pages:",
          file=sys.stderr)
    for agent_id, entry in conflicts.items():
        overlap = set(args.pages) & set(entry.get("target_pages", []))
        print(f"  [{agent_id}] {entry.get('description', '?')}", file=sys.stderr)
        print(f"    Overlapping: {', '.join(sorted(overlap))}", file=sys.stderr)
    return 1


def _cmd_prune(args, manifest: AgentManifest) -> int:
    n = manifest.prune_stale(max_age_hours=args.hours)
    if n:
        print(f"Pruned {n} stale agent entry(ies) (older than {args.hours}h).")
    else:
        print("No stale entries to prune.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="Show active agents")

    p_claim = sub.add_parser("claim", help="Register an agent and claim pages")
    p_claim.add_argument("agent_id", help="Unique identifier for this agent")
    p_claim.add_argument("description", help="Human-readable description of the task")
    p_claim.add_argument("pages", nargs="+",
                         help="Wiki page paths this agent will edit")

    p_rel = sub.add_parser("release", help="Mark an agent as complete")
    p_rel.add_argument("agent_id", help="Agent ID to release")

    p_chk = sub.add_parser("check",
                            help="Check whether pages are free (exit 0) or "
                                 "claimed by another agent (exit 1)")
    p_chk.add_argument("pages", nargs="+", help="Wiki page paths to check")

    p_prune = sub.add_parser("prune", help="Remove stale agent entries")
    p_prune.add_argument("--hours", type=float, default=float(STALE_HOURS),
                         help=f"Max age in hours before an entry is stale "
                              f"(default: {STALE_HOURS})")

    args = parser.parse_args()
    manifest = AgentManifest()

    dispatch = {
        "list":    _cmd_list,
        "claim":   _cmd_claim,
        "release": _cmd_release,
        "check":   _cmd_check,
        "prune":   _cmd_prune,
    }
    sys.exit(dispatch[args.command](args, manifest))


if __name__ == "__main__":
    main()
