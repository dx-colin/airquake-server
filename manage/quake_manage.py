#!/usr/bin/env python3
"""
OrangePi / AirQuake Management Dashboard
==========================================
LAN-only management page for the whole host, not just the game: host
resource stats, top processes, currently-connected players (login state,
IP, session frags/deaths), all registered accounts, and a control panel
to change the map or live server settings.

Talks to nqserver via two files it also reads/writes (see
nexquake/server-patch/sv_accounts.c's "live management dashboard" section
for the engine side of this):
  - live_status.json  -- read: current map/hostname/rules/players, written
                          by the engine roughly every 2 seconds.
  - admin_command.txt -- write: a single pending console command, run
                          (as a trusted, server-console-equivalent command)
                          and deleted by the engine on its next frame.
  - accounts.dat       -- read: every registered account (same file/format
                          the engine itself reads/writes).

No login of its own -- deliberately relies on network-level (LAN-only)
trust instead, per explicit instruction. Do not expose this through
Traefik/the public domain.
"""

import os
import json
import time
import argparse
import http.server
import threading
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import parse_qs
from html import escape

# ── host stats ───────────────────────────────────────────────────────────
#
# Deliberately reading /proc directly (no psutil) -- matches quake-stats'
# zero-dependency, stdlib-only approach. Expects the host's /proc bind-
# mounted read-only at PROCFS (see docker-compose.yml), and the host
# filesystem to statvfs for disk usage at DISKPATH.

PROCFS = Path(os.environ.get("HOST_PROCFS", "/host/proc"))
DISKPATH = os.environ.get("HOST_DISKPATH", "/host/root")


def _read_cpu_times():
    try:
        line = (PROCFS / "stat").read_text().splitlines()[0]
    except OSError:
        return None
    parts = line.split()
    if parts[0] != "cpu":
        return None
    nums = [int(x) for x in parts[1:]]
    idle = nums[3] + (nums[4] if len(nums) > 4 else 0)  # idle + iowait
    total = sum(nums)
    return idle, total


_CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100


def _read_process_jiffies():
    """{pid: (utime+stime jiffies, comm, rss_kb)} for every readable process."""
    out = {}
    try:
        pids = [p for p in os.listdir(PROCFS) if p.isdigit()]
    except OSError:
        return out
    for pid in pids:
        try:
            stat = (PROCFS / pid / "stat").read_text()
            # comm is the first '(...)' field and may itself contain
            # spaces/parens -- split on the *last* ')' to stay correct.
            rparen = stat.rindex(")")
            comm = stat[stat.index("(") + 1:rparen]
            rest = stat[rparen + 2:].split()
            utime, stime = int(rest[11]), int(rest[12])
            rss_kb = None
            try:
                for line in (PROCFS / pid / "status").read_text().splitlines():
                    if line.startswith("VmRSS:"):
                        rss_kb = int(line.split()[1])
                        break
            except OSError:
                pass
            out[pid] = (utime + stime, comm, rss_kb)
        except (OSError, ValueError, IndexError):
            continue  # process exited mid-scan, or a /proc entry that isn't a real process
    return out


def sample_system(sample_seconds=0.3):
    """One combined sampling pass: overall CPU% plus a per-process snapshot,
    sharing a single sleep window instead of sampling twice separately."""
    cpu_first = _read_cpu_times()
    proc_first = _read_process_jiffies()
    time.sleep(sample_seconds)
    cpu_second = _read_cpu_times()
    proc_second = _read_process_jiffies()

    cpu_pct = None
    if cpu_first and cpu_second:
        idle_delta = cpu_second[0] - cpu_first[0]
        total_delta = cpu_second[1] - cpu_first[1]
        if total_delta > 0:
            cpu_pct = round((1 - idle_delta / total_delta) * 100, 1)

    n_cpus = os.cpu_count() or 1
    processes = []
    for pid, (jiffies2, comm, rss_kb) in proc_second.items():
        jiffies1 = proc_first.get(pid, (0, "", None))[0]
        delta = jiffies2 - jiffies1
        if delta <= 0:
            continue
        pct = round(delta / _CLK_TCK / sample_seconds / n_cpus * 100, 1)
        processes.append({
            "pid": pid, "name": comm, "cpu_pct": pct,
            "rss_mb": round(rss_kb / 1024, 1) if rss_kb else None,
        })
    processes.sort(key=lambda p: p["cpu_pct"], reverse=True)
    return cpu_pct, processes


def mem_stats():
    try:
        lines = (PROCFS / "meminfo").read_text().splitlines()
    except OSError:
        return None
    vals = {}
    for line in lines:
        parts = line.split(":")
        if len(parts) != 2:
            continue
        key = parts[0].strip()
        val = parts[1].strip().split()[0]  # drop " kB"
        vals[key] = int(val)
    total = vals.get("MemTotal")
    avail = vals.get("MemAvailable")
    if total is None or avail is None:
        return None
    used = total - avail
    return {
        "total_gb": round(total / 1024 / 1024, 2),
        "used_gb": round(used / 1024 / 1024, 2),
        "pct": round(used / total * 100, 1) if total else 0,
    }


def disk_stats():
    try:
        st = os.statvfs(DISKPATH)
    except OSError:
        return None
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    used = total - free
    return {
        "total_gb": round(total / 1024 ** 3, 2),
        "used_gb": round(used / 1024 ** 3, 2),
        "pct": round(used / total * 100, 1) if total else 0,
    }


def load_avg():
    try:
        return (PROCFS / "loadavg").read_text().split()[:3]
    except OSError:
        return None


# ── nqserver live status / command channel ──────────────────────────────

def read_live_status(path: Path):
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None  # caught mid-write despite the atomic rename -- just skip this refresh


def send_command(path: Path, command: str) -> None:
    # Single-file, last-write-wins -- see sv_accounts.c's doc comment on
    # SV_Accounts_ProcessPendingCommand for why this is an accepted,
    # documented limitation rather than something queued/locked.
    path.write_text(command + "\n", encoding="utf-8")


def read_accounts(accounts_dat_path: Path):
    """Same colon-delimited format the engine itself reads/writes -- see
    sv_accounts.c's _SV_Accounts_Save: username:hash:is_admin:kills:deaths:
    playtime_seconds:created_at:last_login. The hash is never rendered."""
    try:
        lines = accounts_dat_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    accounts = []
    for line in lines:
        parts = line.split(":")
        if len(parts) != 8:
            continue  # skip anything that doesn't match the format rather than guess
        username, _hash, is_admin, kills, deaths, playtime, created_at, last_login = parts
        try:
            accounts.append({
                "username": username,
                "is_admin": is_admin == "1",
                "kills": int(kills),
                "deaths": int(deaths),
                "playtime_seconds": int(playtime),
                "created_at": int(created_at),
                "last_login": int(last_login),
            })
        except ValueError:
            continue
    accounts.sort(key=lambda a: a["last_login"], reverse=True)
    return accounts


def read_votable_maps(server_cfg_path: Path):
    try:
        text = server_cfg_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("sv_votable_maps"):
            parts = line.split('"')
            if len(parts) >= 2:
                return parts[1].split()
    return []


# ── HTML ──────────────────────────────────────────────────────────────────

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
.section { max-width: 1100px; margin: 0 auto; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 12px; margin-bottom: 8px; }
.card {
  background: #161628;
  border: 1px solid #2a2a4a;
  border-radius: 6px;
  padding: 14px;
  text-align: center;
}
.card .val { color: #00e5ff; font-size: 1.6em; font-weight: bold; }
.card .lbl { color: #556; font-size: 0.8em; margin-top: 4px; }
.bar-wrap { background: #1a1a30; border-radius: 3px; height: 8px; width: 100%; margin-top: 8px; }
.bar { background: #00e5ff; height: 8px; border-radius: 3px; }
.bar.warn { background: #ff6b35; }
.bar.crit { background: #f44336; }
table { width: 100%; border-collapse: collapse; margin-bottom: 8px; }
th {
  background: #161628; color: #ff6b35; padding: 8px 12px; text-align: left;
  font-size: 0.8em; text-transform: uppercase; letter-spacing: 1px;
  border-bottom: 1px solid #2a2a4a;
}
td { padding: 7px 12px; border-bottom: 1px solid #1a1a30; }
tr:hover td { background: #161628; }
.positive { color: #4caf50; }
.negative { color: #f44336; }
.neutral { color: #ff6b35; }
.badge { background: #1e1e3a; border: 1px solid #2a2a4a; border-radius: 12px; padding: 2px 8px; font-size: 0.8em; }
.badge.admin { border-color: #ff6b35; color: #ff6b35; }
.badge.guest { color: #667; }
form.inline { display: inline; }
.panel {
  background: #161628; border: 1px solid #2a2a4a; border-radius: 6px;
  padding: 18px; margin-bottom: 16px;
}
.panel-row { display: flex; gap: 10px; align-items: center; margin-bottom: 10px; flex-wrap: wrap; }
.panel-row label { color: #889; min-width: 90px; }
select, input[type=text], input[type=number] {
  background: #0d0d1a; color: #c8d0e0; border: 1px solid #2a2a4a;
  border-radius: 4px; padding: 6px 10px; font-family: inherit; font-size: 0.9em;
}
button {
  background: #00e5ff; color: #0d0d1a; border: none; border-radius: 4px;
  padding: 6px 16px; font-family: inherit; font-weight: bold; cursor: pointer;
  font-size: 0.85em;
}
button:hover { background: #33ebff; }
button.danger { background: #f44336; color: #fff; }
button.danger:hover { background: #ff5c4f; }
.empty { color: #556; padding: 16px; text-align: center; }
"""


def _bar_class(pct):
    if pct >= 90:
        return "crit"
    if pct >= 70:
        return "warn"
    return ""


def _bar(pct):
    cls = _bar_class(pct)
    return f'<div class="bar-wrap"><div class="bar {cls}" style="width:{min(pct, 100)}%"></div></div>'


def render_host_cards(cpu):
    mem = mem_stats()
    disk = disk_stats()
    load = load_avg()

    def card(val, lbl, pct=None):
        bar = _bar(pct) if pct is not None else ""
        return f'<div class="card"><div class="val">{val}</div><div class="lbl">{lbl}</div>{bar}</div>'

    cards = ""
    cards += card(f"{cpu}%" if cpu is not None else "—", "CPU", cpu)
    if mem:
        cards += card(f"{mem['used_gb']}/{mem['total_gb']} GB", "Memory", mem["pct"])
    else:
        cards += card("—", "Memory")
    if disk:
        cards += card(f"{disk['used_gb']}/{disk['total_gb']} GB", "Disk", disk["pct"])
    else:
        cards += card("—", "Disk")
    cards += card(load[0] if load else "—", "Load avg (1m)")
    return f'<div class="grid">{cards}</div>'


def render_top_processes(processes, limit=12):
    top = [p for p in processes if p["cpu_pct"] > 0][:limit]
    if not top:
        return '<div class="panel"><div class="empty">No process activity this sample.</div></div>'
    rows = ""
    for p in top:
        mem = f'{p["rss_mb"]} MB' if p["rss_mb"] is not None else "—"
        rows += f"""
        <tr>
          <td>{escape(p["pid"])}</td>
          <td><strong>{escape(p["name"])}</strong></td>
          <td class="neutral">{p["cpu_pct"]}%</td>
          <td>{mem}</td>
        </tr>"""
    return f"""
    <table>
      <thead><tr><th>PID</th><th>Process</th><th>CPU</th><th>Memory</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>"""


def render_registered_players(accounts):
    if not accounts:
        return '<div class="panel"><div class="empty">No registered accounts.</div></div>'
    rows = ""
    for a in accounts:
        role = '<span class="badge admin">admin</span>' if a["is_admin"] else '<span class="badge">player</span>'
        hours = round(a["playtime_seconds"] / 3600, 1)
        created = datetime.fromtimestamp(a["created_at"], tz=timezone.utc).strftime("%Y-%m-%d")
        last_login = datetime.fromtimestamp(a["last_login"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        username_attr = escape(a["username"], quote=True)
        rows += f"""
        <tr>
          <td><strong>{escape(a["username"])}</strong></td>
          <td>{role}</td>
          <td class="positive">{a["kills"]}</td>
          <td class="negative">{a["deaths"]}</td>
          <td>{hours}h</td>
          <td>{created}</td>
          <td>{last_login}</td>
          <td>
            <form class="inline" method="post" action="/action">
              <input type="hidden" name="cmd" value="setrole">
              <input type="hidden" name="target" value="{username_attr}">
              <select name="role">
                <option value="player"{"" if a["is_admin"] else " selected"}>player</option>
                <option value="admin"{" selected" if a["is_admin"] else ""}>admin</option>
              </select>
              <button type="submit" onclick="return confirm('Change role for {escape(a["username"])}?')">Set</button>
            </form>
          </td>
        </tr>"""
    return f"""
    <table>
      <thead>
        <tr><th>Username</th><th>Role</th><th>Kills</th><th>Deaths</th><th>Playtime</th><th>Registered</th><th>Last Login</th><th></th></tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>"""


def render_players(status):
    players = status.get("players", []) if status else []
    if not players:
        return '<div class="panel"><div class="empty">No players connected.</div></div>'

    rows = ""
    for p in players:
        if p.get("authenticated"):
            login_badge = f'<span class="badge admin">{escape(p.get("account") or "?")}</span>' if p.get("is_admin") \
                else f'<span class="badge">{escape(p.get("account") or "?")}</span>'
        else:
            login_badge = '<span class="badge guest">guest</span>'
        rows += f"""
        <tr>
          <td><strong>{escape(p.get("name", "?"))}</strong></td>
          <td>{login_badge}</td>
          <td>{escape(p.get("address", "?"))}</td>
          <td class="positive">{p.get("kills", 0)}</td>
          <td class="negative">{p.get("deaths", 0)}</td>
          <td>{"yes" if p.get("spawned") else "loading..."}</td>
          <td>
            <form class="inline" method="post" action="/action">
              <input type="hidden" name="cmd" value="kick">
              <input type="hidden" name="target" value="{escape(p.get('name', ''), quote=True)}">
              <button type="submit" class="danger" onclick="return confirm('Kick {escape(p.get('name', ''))}?')">Kick</button>
            </form>
          </td>
        </tr>"""

    return f"""
    <table>
      <thead>
        <tr><th>Name</th><th>Account</th><th>Address</th><th>Kills</th><th>Deaths</th><th>Spawned</th><th></th></tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>"""


def render_management(status, maps):
    current_map = status.get("map", "") if status else ""
    fraglimit = status.get("fraglimit", "") if status else ""
    timelimit = status.get("timelimit", "") if status else ""
    hostname = status.get("hostname", "") if status else ""

    map_options = "".join(
        f'<option value="{escape(m)}"{" selected" if m == current_map else ""}>{escape(m)}</option>'
        for m in maps
    )

    return f"""
    <div class="panel">
      <div class="panel-row">
        <form class="inline panel-row" method="post" action="/action">
          <label>Map</label>
          <input type="hidden" name="cmd" value="changelevel">
          <select name="target">{map_options}</select>
          <button type="submit">Go</button>
        </form>
      </div>
      <div class="panel-row">
        <form class="inline panel-row" method="post" action="/action">
          <label>Fraglimit</label>
          <input type="hidden" name="cmd" value="fraglimit">
          <input type="number" name="target" value="{escape(str(fraglimit))}" style="width:80px">
          <button type="submit">Set</button>
        </form>
      </div>
      <div class="panel-row">
        <form class="inline panel-row" method="post" action="/action">
          <label>Timelimit</label>
          <input type="hidden" name="cmd" value="timelimit">
          <input type="number" name="target" value="{escape(str(timelimit))}" style="width:80px">
          <button type="submit">Set</button>
        </form>
      </div>
      <div class="panel-row">
        <form class="inline panel-row" method="post" action="/action">
          <label>Hostname</label>
          <input type="hidden" name="cmd" value="hostname">
          <input type="text" name="target" value="{escape(str(hostname), quote=True)}" style="width:220px">
          <button type="submit">Set</button>
        </form>
      </div>
    </div>"""


def render_page(status, maps, accounts, cpu, processes, message=None):
    map_name = escape(status.get("map", "unknown")) if status else "unknown"
    hostname = escape(status.get("hostname", "")) if status else ""
    updated = status.get("updated") if status else None
    age = f"{int(time.time() - updated)}s ago" if updated else "unknown"
    stale_warn = "" if status and (time.time() - updated) < 15 else \
        '<p style="color:#f44336">Warning: game status looks stale -- is nqserver running?</p>'

    msg_html = f'<p style="color:#00e5ff;margin-bottom:16px">{escape(message)}</p>' if message else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="10">
  <title>OrangePi Management</title>
  <style>{_CSS}</style>
</head>
<body>
<div class="section">
  <h1>ORANGEPI MANAGEMENT</h1>
  <p class="meta">AirQuake: {hostname} &nbsp;|&nbsp; map: <strong>{map_name}</strong> &nbsp;|&nbsp; status updated {age}</p>
  {stale_warn}
  {msg_html}

  <h2>Host</h2>
  {render_host_cards(cpu)}

  <h2>Top Processes</h2>
  {render_top_processes(processes)}

  <h2>Current Players</h2>
  {render_players(status)}

  <h2>Registered Players</h2>
  {render_registered_players(accounts)}

  <h2>Server Control</h2>
  {render_management(status, maps)}
</div>
</body>
</html>"""


# ── web server ────────────────────────────────────────────────────────────

class ManageHandler(http.server.BaseHTTPRequestHandler):
    status_path: Path
    command_path: Path
    server_cfg_path: Path
    accounts_path: Path

    def _send_html(self, body: str, code=200):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path not in ("/", ""):
            self.send_response(404)
            self.end_headers()
            return
        status = read_live_status(self.status_path)
        maps = read_votable_maps(self.server_cfg_path)
        accounts = read_accounts(self.accounts_path)
        cpu, processes = sample_system()
        self._send_html(render_page(status, maps, accounts, cpu, processes))

    def do_POST(self):  # noqa: N802
        if self.path != "/action":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        fields = parse_qs(body)
        cmd = (fields.get("cmd", [""])[0] or "").strip()
        target = (fields.get("target", [""])[0] or "").strip()

        message = None
        if cmd in ("changelevel", "fraglimit", "timelimit", "kick") and target:
            send_command(self.command_path, f'{cmd} "{target}"')
            message = f"Sent: {cmd} {target}"
        elif cmd == "hostname":
            send_command(self.command_path, f'hostname "{target}"')
            message = f"Sent: hostname {target}"
        elif cmd == "setrole" and target:
            role = (fields.get("role", [""])[0] or "").strip()
            if role in ("admin", "player"):
                send_command(self.command_path, f'setrole "{target}" {role}')
                message = f"Sent: setrole {target} {role}"
            else:
                message = "Invalid role."
        else:
            message = "Ignored empty or unrecognized command."

        # POST-redirect-GET so a page refresh doesn't resend the action.
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()
        # Message is best-effort only (lost on redirect) -- acceptable for
        # a LAN admin tool; the page reflects the actual new state within
        # one status refresh (~2s) either way.
        _ = message

    def log_message(self, fmt, *args):  # suppress default access log
        pass


def main():
    ap = argparse.ArgumentParser(description="OrangePi / AirQuake management dashboard")
    ap.add_argument("--accounts-dir", required=True,
                     help="Directory containing live_status.json / admin_command.txt / accounts.dat (shared with nqserver)")
    ap.add_argument("--server-cfg", required=True,
                     help="Path to server.cfg, for reading sv_votable_maps")
    ap.add_argument("--port", type=int, default=26200)
    args = ap.parse_args()

    ManageHandler.status_path = Path(args.accounts_dir) / "live_status.json"
    ManageHandler.command_path = Path(args.accounts_dir) / "admin_command.txt"
    ManageHandler.accounts_path = Path(args.accounts_dir) / "accounts.dat"
    ManageHandler.server_cfg_path = Path(args.server_cfg)

    print(f"Starting OrangePi management dashboard on http://0.0.0.0:{args.port}")
    httpd = http.server.HTTPServer(("0.0.0.0", args.port), ManageHandler)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
