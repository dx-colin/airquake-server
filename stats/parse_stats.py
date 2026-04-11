#!/usr/bin/env python3
"""
Airquake Server Stats Parser
=============================
Parses qconsole.log and generates comprehensive player statistics as an HTML page.

Usage:
  # Generate stats.html once
  python parse_stats.py --log /path/to/qconsole.log

  # Serve stats on http://0.0.0.0:8080, auto-refreshing every 60s
  python parse_stats.py --log /path/to/qconsole.log --serve --port 8080 --interval 60
"""

import re
import time
import argparse
import http.server
import threading
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional
from html import escape

# ── Log patterns ──────────────────────────────────────────────────────────────
#
# Quake prints kill/event messages as raw console text — no structured format.
# The exact wording depends on progs.dat (Airquake uses custom messages).
# Add new regexes here if you see unmatched kill lines in qconsole.log.

# "Killer killed Victim"
_ACTIVE_KILL = re.compile(r"^(?P<killer>.+?) killed (?P<victim>.+)$")

# "Victim was <verb> by Killer"  — covers most passive-voice death messages
_PASSIVE_KILL = re.compile(
    r"^(?P<victim>.+?) was (?:killed|gibbed|fragged|destroyed|shot down|"
    r"blasted|telefragged|blown up|taken out|terminated) by (?P<killer>.+)$"
)

# Suicides / environmental deaths (player is the only name on the line)
_SUICIDE_PATTERNS = [
    re.compile(r"^(?P<player>.+?) (?:killed|blew) (?:him|her|them)self$"),
    re.compile(r"^(?P<player>.+?) (?:suicided|cratered|drowned|melted|crashed)$"),
    re.compile(r"^(?P<player>.+?) (?:fell to|fell from|plummeted to) (?:his |her |their )?death$"),
    re.compile(r"^(?P<player>.+?) was in the wrong place$"),
    re.compile(r"^(?P<player>.+?) tried to leave the map$"),
]

# Connect / disconnect
_CONNECT = re.compile(
    r'^(?:Client "(?P<a>.+?)" connected|(?P<b>.+?) entered the game|(?P<c>.+?) joined the game)$'
)
_DISCONNECT = re.compile(
    r'^(?:Client "(?P<a>.+?)" disconnected|(?P<b>.+?) (?:has )?disconnected|(?P<c>.+?) left the game)$'
)

# Map changes  — NetQuake dedicated server format
_MAP_CHANGE = re.compile(
    r"^(?:-{3,}\s*Spawned server (?P<a>\S+)|Changelevel to (?P<b>\S+)|MAP: (?P<c>\S+))$",
    re.IGNORECASE,
)

# Optional leading timestamp [HH:MM:SS] or HH:MM:SS
_TIMESTAMP = re.compile(r"^\[?(\d{2}:\d{2}:\d{2})\]?\s*(.+)$")

# Weapon inference — map kill message keywords to weapon names
_WEAPON_KEYWORDS: list[tuple[str, list[str]]] = [
    ("missile",    ["missile", "heat-seeking", "homing"]),
    ("rocket",     ["rocket", "blasted", "explosion"]),
    ("grenade",    ["grenade", "shrapnel"]),
    ("railgun",    ["rail"]),
    ("lightning",  ["lightning", "bolt", "electr", "zapped"]),
    ("chaingun",   ["chaingun", "bullets", "machinegun", "machine gun"]),
    ("shotgun",    ["shotgun", "buckshot"]),
    ("bomb",       ["bomb", "napalm", "dropped"]),
    ("axe",        ["axe", "crowbar", "melee"]),
]


def _infer_weapon(line: str) -> str:
    low = line.lower()
    for weapon, keywords in _WEAPON_KEYWORDS:
        if any(kw in low for kw in keywords):
            return weapon
    return "unknown"


def _extract_name(match: re.Match, *groups: str) -> Optional[str]:
    for g in groups:
        try:
            v = match.group(g)
            if v:
                return v.strip()
        except IndexError:
            pass
    return None


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class PlayerStats:
    name: str
    kills: int = 0
    deaths: int = 0
    suicides: int = 0
    sessions: int = 0
    total_play_seconds: float = 0.0
    best_streak: int = 0
    worst_death_streak: int = 0
    weapons: dict = field(default_factory=lambda: defaultdict(int))
    maps_played: dict = field(default_factory=lambda: defaultdict(int))
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None
    # transient — not serialised
    _streak: int = field(default=0, repr=False)
    _death_streak: int = field(default=0, repr=False)
    _connect_wall: Optional[float] = field(default=None, repr=False)

    @property
    def kd_ratio(self) -> float:
        return round(self.kills / max(1, self.deaths), 2)

    @property
    def efficiency(self) -> float:
        total = self.kills + self.deaths
        return round(self.kills / total * 100, 1) if total else 0.0

    @property
    def avg_kills_per_session(self) -> float:
        return round(self.kills / max(1, self.sessions), 1)

    @property
    def play_time_fmt(self) -> str:
        s = int(self.total_play_seconds)
        h, rem = divmod(s, 3600)
        m, s = divmod(rem, 60)
        return f"{h}h {m}m" if h else f"{m}m {s}s"

    @property
    def top_weapon(self) -> str:
        return max(self.weapons, key=self.weapons.__getitem__) if self.weapons else "—"

    @property
    def top_map(self) -> str:
        return max(self.maps_played, key=self.maps_played.__getitem__) if self.maps_played else "—"


@dataclass
class MatchRecord:
    map_name: str
    started_at: str
    ended_at: Optional[str] = None
    scores: dict = field(default_factory=dict)   # player -> frags

    @property
    def winner(self) -> Optional[str]:
        if not self.scores:
            return None
        top = max(self.scores.values())
        winners = [p for p, s in self.scores.items() if s == top]
        return winners[0] if len(winners) == 1 else f"{winners[0]} (tied)"

    @property
    def duration_fmt(self) -> str:
        if not self.ended_at:
            return "ongoing"
        try:
            fmt = "%H:%M:%S"
            diff = (
                datetime.strptime(self.ended_at, fmt)
                - datetime.strptime(self.started_at, fmt)
            ).seconds
            m, s = divmod(diff, 60)
            return f"{m}m {s}s"
        except Exception:
            return "—"


# ── Parser ────────────────────────────────────────────────────────────────────

class LogParser:
    def __init__(self) -> None:
        self.players: dict[str, PlayerStats] = {}
        self.matches: list[MatchRecord] = []
        self._current: Optional[MatchRecord] = None
        self._current_map: str = "unknown"
        self.total_frags: int = 0
        self.map_plays: dict[str, int] = defaultdict(int)
        self.map_frags: dict[str, int] = defaultdict(int)
        self.parsed_at: str = ""
        self.log_lines: int = 0
        self.unmatched_lines: int = 0

    # ── helpers ──

    def _player(self, name: str) -> PlayerStats:
        if name not in self.players:
            self.players[name] = PlayerStats(name=name)
        return self.players[name]

    def _on_kill(self, killer: str, victim: str, line: str, ts: str) -> None:
        weapon = _infer_weapon(line)
        k = self._player(killer)
        v = self._player(victim)

        k.kills += 1
        k.weapons[weapon] += 1
        k.maps_played[self._current_map] += 1
        k._streak += 1
        k._death_streak = 0
        if k._streak > k.best_streak:
            k.best_streak = k._streak
        k.last_seen = ts

        v.deaths += 1
        v._death_streak += 1
        v._streak = 0
        if v._death_streak > v.worst_death_streak:
            v.worst_death_streak = v._death_streak
        v.last_seen = ts

        self.total_frags += 1
        self.map_frags[self._current_map] += 1

        if self._current is not None:
            self._current.scores[killer] = self._current.scores.get(killer, 0) + 1

    def _on_suicide(self, player: str, ts: str) -> None:
        p = self._player(player)
        p.suicides += 1
        p.deaths += 1
        p._streak = 0
        p._death_streak += 1
        if p._death_streak > p.worst_death_streak:
            p.worst_death_streak = p._death_streak
        p.last_seen = ts

    def _on_connect(self, player: str, ts: str) -> None:
        p = self._player(player)
        p.sessions += 1
        p._connect_wall = time.monotonic()
        p.last_seen = ts
        if not p.first_seen:
            p.first_seen = ts

    def _on_disconnect(self, player: str, ts: str) -> None:
        p = self._player(player)
        if p._connect_wall is not None:
            p.total_play_seconds += time.monotonic() - p._connect_wall
            p._connect_wall = None
        p.last_seen = ts

    def _start_match(self, map_name: str, ts: str) -> None:
        if self._current is not None:
            self._current.ended_at = ts
            self.matches.append(self._current)
        self._current_map = map_name
        self._current = MatchRecord(map_name=map_name, started_at=ts)
        self.map_plays[map_name] += 1

    # ── main parse ──

    def parse(self, log_path: Path) -> None:
        self.parsed_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        with open(log_path, encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                self.log_lines += 1
                line = raw.strip()
                if not line or line.startswith("]") or line.startswith("#"):
                    continue

                # Strip optional leading timestamp
                ts = "00:00:00"
                m = _TIMESTAMP.match(line)
                if m:
                    ts, line = m.group(1), m.group(2).strip()

                # Map change
                m = _MAP_CHANGE.match(line)
                if m:
                    map_name = m.group("a") or m.group("b") or m.group("c")
                    self._start_match(map_name, ts)
                    continue

                # Connect
                m = _CONNECT.match(line)
                if m:
                    player = _extract_name(m, "a", "b", "c")
                    if player:
                        self._on_connect(player, ts)
                    continue

                # Disconnect
                m = _DISCONNECT.match(line)
                if m:
                    player = _extract_name(m, "a", "b", "c")
                    if player:
                        self._on_disconnect(player, ts)
                    continue

                # Suicide (check before kill — "killed himself" would also match active kill)
                matched = False
                for pat in _SUICIDE_PATTERNS:
                    m = pat.match(line)
                    if m:
                        self._on_suicide(m.group("player").strip(), ts)
                        matched = True
                        break
                if matched:
                    continue

                # Active kill: "Killer killed Victim"
                m = _ACTIVE_KILL.match(line)
                if m:
                    killer = m.group("killer").strip()
                    victim = m.group("victim").strip()
                    if killer != victim:
                        self._on_kill(killer, victim, line, ts)
                    else:
                        self._on_suicide(killer, ts)
                    continue

                # Passive kill: "Victim was <verb> by Killer"
                m = _PASSIVE_KILL.match(line)
                if m:
                    killer = m.group("killer").strip()
                    victim = m.group("victim").strip()
                    if killer != victim:
                        self._on_kill(killer, victim, line, ts)
                    else:
                        self._on_suicide(killer, ts)
                    continue

                self.unmatched_lines += 1

        # Close out the current match
        if self._current is not None:
            self._current.ended_at = ts if self.log_lines else None
            self.matches.append(self._current)
            self._current = None


# ── HTML generation ───────────────────────────────────────────────────────────

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: #0d0d1a;
  color: #c8d0e0;
  font-family: 'Courier New', monospace;
  font-size: 14px;
  padding: 24px;
}
h1 { color: #00e5ff; font-size: 2em; letter-spacing: 2px; margin-bottom: 4px; }
h2 { color: #ff6b35; font-size: 1.1em; letter-spacing: 1px; margin: 28px 0 10px; text-transform: uppercase; }
.meta { color: #556; font-size: 0.85em; margin-bottom: 24px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 12px; margin-bottom: 8px; }
.card {
  background: #161628;
  border: 1px solid #2a2a4a;
  border-radius: 6px;
  padding: 14px;
  text-align: center;
}
.card .val { color: #00e5ff; font-size: 1.8em; font-weight: bold; }
.card .lbl { color: #556; font-size: 0.8em; margin-top: 4px; }
table {
  width: 100%;
  border-collapse: collapse;
  margin-bottom: 8px;
}
th {
  background: #161628;
  color: #ff6b35;
  padding: 8px 12px;
  text-align: left;
  font-size: 0.8em;
  text-transform: uppercase;
  letter-spacing: 1px;
  border-bottom: 1px solid #2a2a4a;
}
td {
  padding: 7px 12px;
  border-bottom: 1px solid #1a1a30;
}
tr:hover td { background: #161628; }
.rank { color: #556; }
.gold { color: #ffd700; }
.silver { color: #c0c0c0; }
.bronze { color: #cd7f32; }
.bar-wrap { background: #1a1a30; border-radius: 3px; height: 6px; width: 100px; display: inline-block; vertical-align: middle; margin-left: 6px; }
.bar { background: #00e5ff; height: 6px; border-radius: 3px; }
.positive { color: #4caf50; }
.negative { color: #f44336; }
.neutral { color: #ff6b35; }
.section { max-width: 1200px; margin: 0 auto; }
.badge { background: #1e1e3a; border: 1px solid #2a2a4a; border-radius: 12px; padding: 2px 8px; font-size: 0.8em; }
"""


def _medal(rank: int) -> str:
    return ["🥇", "🥈", "🥉"][rank] if rank < 3 else str(rank + 1)


def _bar(value: float, max_value: float, width: int = 100) -> str:
    pct = int(value / max_value * width) if max_value else 0
    return f'<span class="bar-wrap"><span class="bar" style="width:{pct}px"></span></span>'


def _kd_class(kd: float) -> str:
    if kd >= 2.0:
        return "positive"
    if kd >= 1.0:
        return "neutral"
    return "negative"


def render_html(parser: LogParser) -> str:
    players = sorted(parser.players.values(), key=lambda p: p.kills, reverse=True)
    matches = list(reversed(parser.matches))  # most recent first

    total_players = len(players)
    total_matches = len(parser.matches)
    top_killer = players[0].name if players else "—"
    top_kd = max(players, key=lambda p: p.kd_ratio).name if players else "—"
    max_kills = players[0].kills if players else 1
    maps_sorted = sorted(parser.map_plays, key=parser.map_plays.__getitem__, reverse=True)

    # ── Summary cards ──
    cards = f"""
    <div class="grid">
      <div class="card"><div class="val">{parser.total_frags:,}</div><div class="lbl">Total Frags</div></div>
      <div class="card"><div class="val">{total_matches}</div><div class="lbl">Matches Played</div></div>
      <div class="card"><div class="val">{total_players}</div><div class="lbl">Unique Players</div></div>
      <div class="card"><div class="val">{escape(top_killer)}</div><div class="lbl">Top Fragger</div></div>
      <div class="card"><div class="val">{escape(top_kd)}</div><div class="lbl">Best K/D</div></div>
      <div class="card"><div class="val">{escape(maps_sorted[0]) if maps_sorted else '—'}</div><div class="lbl">Most Played Map</div></div>
    </div>"""

    # ── Leaderboard ──
    lb_rows = ""
    for i, p in enumerate(players):
        kd_cls = _kd_class(p.kd_ratio)
        lb_rows += f"""
        <tr>
          <td class="rank">{_medal(i)}</td>
          <td><strong>{escape(p.name)}</strong></td>
          <td class="positive">{p.kills} {_bar(p.kills, max_kills)}</td>
          <td class="negative">{p.deaths}</td>
          <td class="{kd_cls}">{p.kd_ratio}</td>
          <td>{p.efficiency}%</td>
          <td>{p.suicides}</td>
          <td class="positive">{p.best_streak}</td>
          <td class="negative">{p.worst_death_streak}</td>
          <td>{p.avg_kills_per_session}</td>
          <td>{p.sessions}</td>
          <td>{p.play_time_fmt}</td>
          <td><span class="badge">{escape(p.top_weapon)}</span></td>
          <td><span class="badge">{escape(p.top_map)}</span></td>
        </tr>"""

    leaderboard = f"""
    <table>
      <thead>
        <tr>
          <th>#</th><th>Player</th><th>Kills</th><th>Deaths</th>
          <th>K/D</th><th>Efficiency</th><th>Suicides</th>
          <th>Best Streak</th><th>Worst Death Streak</th>
          <th>Avg Kills/Session</th><th>Sessions</th><th>Play Time</th>
          <th>Fav Weapon</th><th>Fav Map</th>
        </tr>
      </thead>
      <tbody>{lb_rows}</tbody>
    </table>"""

    # ── Per-player weapon breakdown ──
    weapon_rows = ""
    for p in players:
        if not p.weapons:
            continue
        weapon_str = " ".join(
            f'<span class="badge">{escape(w)}: {c}</span>'
            for w, c in sorted(p.weapons.items(), key=lambda x: x[1], reverse=True)
        )
        weapon_rows += f"<tr><td><strong>{escape(p.name)}</strong></td><td>{weapon_str}</td></tr>"

    weapon_table = f"""
    <table>
      <thead><tr><th>Player</th><th>Kills by Weapon</th></tr></thead>
      <tbody>{weapon_rows}</tbody>
    </table>"""

    # ── Map stats ──
    max_map_plays = max(parser.map_plays.values(), default=1)
    map_rows = ""
    for map_name in maps_sorted:
        plays = parser.map_plays[map_name]
        frags = parser.map_frags.get(map_name, 0)
        avg = round(frags / plays, 1) if plays else 0
        map_rows += f"""
        <tr>
          <td><strong>{escape(map_name)}</strong></td>
          <td>{plays} {_bar(plays, max_map_plays)}</td>
          <td>{frags:,}</td>
          <td>{avg}</td>
        </tr>"""

    map_table = f"""
    <table>
      <thead><tr><th>Map</th><th>Times Played</th><th>Total Frags</th><th>Avg Frags/Play</th></tr></thead>
      <tbody>{map_rows}</tbody>
    </table>"""

    # ── Match history (last 30) ──
    match_rows = ""
    for i, match in enumerate(matches[:30]):
        score_str = " | ".join(
            f"{escape(p)}: {s}"
            for p, s in sorted(match.scores.items(), key=lambda x: x[1], reverse=True)
        ) or "—"
        winner = escape(match.winner) if match.winner else "—"
        match_rows += f"""
        <tr>
          <td>{len(matches) - i}</td>
          <td><span class="badge">{escape(match.map_name)}</span></td>
          <td>{match.started_at}</td>
          <td>{match.duration_fmt}</td>
          <td class="gold">{winner}</td>
          <td style="font-size:0.85em">{score_str}</td>
        </tr>"""

    match_table = f"""
    <table>
      <thead>
        <tr><th>#</th><th>Map</th><th>Started</th><th>Duration</th><th>Winner</th><th>Scores</th></tr>
      </thead>
      <tbody>{match_rows}</tbody>
    </table>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Airquake Server Stats</title>
  <style>{_CSS}</style>
</head>
<body>
<div class="section">
  <h1>AIRQUAKE STATS</h1>
  <p class="meta">Generated {parser.parsed_at} &nbsp;|&nbsp; {parser.log_lines:,} log lines parsed &nbsp;|&nbsp; {parser.unmatched_lines:,} unmatched</p>

  <h2>Server Overview</h2>
  {cards}

  <h2>Leaderboard</h2>
  {leaderboard}

  <h2>Weapon Breakdown</h2>
  {weapon_table}

  <h2>Map Stats</h2>
  {map_table}

  <h2>Match History</h2>
  {match_table}
</div>
</body>
</html>"""


# ── Web server ────────────────────────────────────────────────────────────────

class StatsHandler(http.server.BaseHTTPRequestHandler):
    html: str = ""

    def do_GET(self):  # noqa: N802
        body = self.html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # suppress default access log
        pass


def _refresh_loop(log_path: Path, interval: int) -> None:
    while True:
        parser = LogParser()
        try:
            parser.parse(log_path)
            StatsHandler.html = render_html(parser)
        except Exception as e:
            StatsHandler.html = f"<pre>Error parsing log: {e}</pre>"
        time.sleep(interval)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Airquake stats parser")
    ap.add_argument("--log",      required=True, help="Path to qconsole.log")
    ap.add_argument("--output",   default="stats.html", help="Output HTML file (default: stats.html)")
    ap.add_argument("--serve",    action="store_true", help="Serve stats over HTTP instead of writing a file")
    ap.add_argument("--port",     type=int, default=8080, help="HTTP port (default: 8080)")
    ap.add_argument("--interval", type=int, default=60, help="Refresh interval in seconds when serving (default: 60)")
    args = ap.parse_args()

    log_path = Path(args.log)
    if not log_path.exists():
        print(f"Log file not found: {log_path}")
        raise SystemExit(1)

    if args.serve:
        print(f"Starting stats server on http://0.0.0.0:{args.port}  (refreshing every {args.interval}s)")
        # Initial parse before accepting requests
        parser = LogParser()
        parser.parse(log_path)
        StatsHandler.html = render_html(parser)
        # Background refresh loop
        t = threading.Thread(target=_refresh_loop, args=(log_path, args.interval), daemon=True)
        t.start()
        httpd = http.server.HTTPServer(("0.0.0.0", args.port), StatsHandler)
        httpd.serve_forever()
    else:
        parser = LogParser()
        parser.parse(log_path)
        html = render_html(parser)
        out = Path(args.output)
        out.write_text(html, encoding="utf-8")
        print(f"Stats written to {out}")
        print(f"  Players: {len(parser.players)}  |  Matches: {len(parser.matches)}  |  Frags: {parser.total_frags}")


if __name__ == "__main__":
    main()
