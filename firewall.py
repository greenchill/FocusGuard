# -*- coding: utf-8 -*-
"""
FocusGuard Firewall - local domain blocking via the system hosts file.

During focus the selected domains are redirected to 0.0.0.0 (a dead address), so the
browser cannot open them. On break they are unblocked.

IMPORTANT: editing hosts requires administrator rights. This module does nothing on
its own until run with --block (or until "firewall.enabled": true in config.json and
the app is launched as administrator).

The domain list lives in blocklist.txt (one domain per line).

CLI:
    python firewall.py --list                    show the domain list
    python firewall.py --add youtube.com x.com   add domains to the list
    python firewall.py --remove youtube.com      remove a domain from the list
    python firewall.py --status                  show whether hosts is blocked now
    python firewall.py --block                   BLOCK now (needs admin)
    python firewall.py --unblock                 UNBLOCK now (needs admin)

Also usable as a module: from firewall import block, unblock, load_domains
"""

import os
import sys
import shutil
import platform
import subprocess

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# In a PyInstaller build the source dir is read-only, so keep the (user-editable)
# blocklist next to the .exe; in dev it sits beside this file.
if getattr(sys, "frozen", False):
    HERE = os.path.dirname(sys.executable)
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
BLOCKLIST_PATH = os.path.join(HERE, "blocklist.txt")

IS_WINDOWS = platform.system() == "Windows"
if IS_WINDOWS:
    HOSTS_PATH = os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                              "System32", "drivers", "etc", "hosts")
else:
    HOSTS_PATH = "/etc/hosts"

BACKUP_PATH = HOSTS_PATH + ".focusguard.bak"

MARK_START = "# >>> FocusGuard block >>>"
MARK_END = "# <<< FocusGuard block <<<"
REDIRECT_IP = "0.0.0.0"


# --------------------------------------------------------------------------- #
#  Domain list (blocklist.txt)
# --------------------------------------------------------------------------- #
def _normalize(domain):
    d = domain.strip().lower()
    for pref in ("http://", "https://", "www."):
        if d.startswith(pref):
            d = d[len(pref):]
    d = d.split("/")[0].strip()
    return d


def load_domains():
    if not os.path.exists(BLOCKLIST_PATH):
        return []
    out = []
    with open(BLOCKLIST_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            d = _normalize(line)
            if d and d not in out:
                out.append(d)
    return out


def save_domains(domains):
    uniq = []
    for d in domains:
        d = _normalize(d)
        if d and d not in uniq:
            uniq.append(d)
    with open(BLOCKLIST_PATH, "w", encoding="utf-8") as f:
        f.write("# FocusGuard blocklist - one domain per line.\n")
        f.write("# Lines starting with # are ignored. Example: youtube.com\n")
        for d in uniq:
            f.write(d + "\n")
    return uniq


def add_domains(new):
    domains = load_domains()
    for d in new:
        d = _normalize(d)
        if d and d not in domains:
            domains.append(d)
    return save_domains(domains)


def remove_domains(rm):
    rmset = {_normalize(d) for d in rm}
    domains = [d for d in load_domains() if d not in rmset]
    return save_domains(domains)


# --------------------------------------------------------------------------- #
#  Admin rights
# --------------------------------------------------------------------------- #
def is_admin():
    try:
        if IS_WINDOWS:
            import ctypes
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        return os.geteuid() == 0
    except Exception:
        return False


# --------------------------------------------------------------------------- #
#  Read/write hosts while preserving the rest of the file
# --------------------------------------------------------------------------- #
def _read_hosts():
    with open(HOSTS_PATH, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _write_hosts(text):
    # Always keep a one-time backup of the original hosts file.
    if not os.path.exists(BACKUP_PATH):
        try:
            shutil.copy2(HOSTS_PATH, BACKUP_PATH)
        except Exception:
            pass
    # Atomic write: write to a temp file in the same dir, then replace. This way a
    # crash / full disk mid-write cannot corrupt or truncate the real hosts file
    # (which would break ALL networking on the machine).
    d = os.path.dirname(HOSTS_PATH)
    tmp = os.path.join(d, "hosts.focusguard.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, HOSTS_PATH)


def _strip_section(text):
    """Removes any existing FocusGuard block, returns the rest of the text."""
    if MARK_START not in text:
        return text
    before = text.split(MARK_START)[0]
    after = ""
    if MARK_END in text:
        after = text.split(MARK_END, 1)[1]
    cleaned = (before.rstrip() + "\n" + after.lstrip()).strip() + "\n"
    return cleaned


def is_blocked():
    try:
        return MARK_START in _read_hosts()
    except Exception:
        return False


def flush_dns():
    try:
        if IS_WINDOWS:
            subprocess.run(["ipconfig", "/flushdns"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            for cmd in (["resolvectl", "flush-caches"],
                        ["dscacheutil", "-flushcache"]):
                try:
                    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    pass
    except Exception:
        pass


def block(domains=None, do_flush=True):
    """Adds the FocusGuard block to hosts. Returns (ok, message)."""
    domains = domains if domains is not None else load_domains()
    domains = [_normalize(d) for d in domains if _normalize(d)]
    if not domains:
        return False, "Domain list is empty - nothing to block (see blocklist.txt)."
    if not is_admin():
        return False, "Administrator rights required (run as administrator)."

    try:
        base = _strip_section(_read_hosts())
        lines = [MARK_START,
                 "# Do not edit by hand - managed by FocusGuard."]
        for d in domains:
            lines.append(f"{REDIRECT_IP} {d}")
            # The hosts file has no wildcards, so cover the common web-facing subdomains
            # (the apex + www + m). e.g. youtube.com -> youtube.com, www.youtube.com,
            # m.youtube.com. Skip a prefix the domain already carries.
            for sub in ("www", "m"):
                if not d.startswith(sub + "."):
                    lines.append(f"{REDIRECT_IP} {sub}.{d}")
        lines.append(MARK_END)
        new_text = base.rstrip() + "\n" + "\n".join(lines) + "\n"
        _write_hosts(new_text)
    except PermissionError:
        return False, "Access to hosts denied. Run as administrator."
    except Exception as e:
        return False, f"Error writing hosts: {e}"

    if do_flush:
        flush_dns()
    return True, f"Blocked {len(domains)} domain(s)."


def unblock(do_flush=True):
    """Removes the FocusGuard block from hosts. Returns (ok, message)."""
    try:
        if not is_blocked():
            return True, "No FocusGuard block found in hosts - already unblocked."
        if not is_admin():
            return False, "Administrator rights required to remove the block."
        cleaned = _strip_section(_read_hosts())
        _write_hosts(cleaned)
    except PermissionError:
        return False, "Access to hosts denied. Run as administrator."
    except Exception as e:
        return False, f"Error writing hosts: {e}"

    if do_flush:
        flush_dns()
    return True, "Block removed."


def enforce(want_blocked, do_flush=True, wait=True):
    """Apply (want_blocked=True) or remove the block, ELEVATING via a short-lived helper
    process when we're not admin — so the app can block/unblock without restarting.

    Returns (ok, msg). With admin it edits hosts directly; without admin it runs the same
    exe with --fw-block / --fw-unblock under a UAC prompt (one prompt per call).
    wait=False fires the helper without waiting (used on app exit so shutdown can't hang)."""
    if want_blocked and not load_domains():
        return False, "Domain list is empty - add a site to block."
    if is_admin():
        return block(do_flush=do_flush) if want_blocked else unblock(do_flush=do_flush)
    try:
        import elevation
        ok = elevation.run_hosts_helper("block" if want_blocked else "unblock", wait=wait)
    except Exception as e:
        return False, f"Admin helper error: {e}"
    if not wait:
        return True, "Admin helper launched."
    if ok:
        return True, ("Sites blocked (admin granted)." if want_blocked
                      else "Block removed (admin granted).")
    return False, "Admin prompt was cancelled."


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #
def _print_list():
    domains = load_domains()
    if not domains:
        print("List is empty. Add with: python firewall.py --add youtube.com")
    else:
        print(f"Domains in list: {len(domains)}")
        for d in domains:
            print("  -", d)


def main(argv):
    if not argv:
        print(__doc__)
        return 0

    cmd = argv[0]
    rest = argv[1:]

    if cmd in ("--list", "-l"):
        _print_list()
    elif cmd in ("--add", "-a"):
        if not rest:
            print("Specify domain(s): python firewall.py --add youtube.com")
            return 1
        domains = add_domains(rest)
        print(f"Added. List now has {len(domains)} domain(s).")
        _print_list()
    elif cmd in ("--remove", "--rm", "-r"):
        if not rest:
            print("Specify domain(s) to remove.")
            return 1
        domains = remove_domains(rest)
        print(f"Removed. List now has {len(domains)} domain(s).")
        _print_list()
    elif cmd == "--status":
        print("hosts:", HOSTS_PATH)
        print("Currently blocked:", "YES" if is_blocked() else "no")
        print("Administrator:", "yes" if is_admin() else "NO")
        _print_list()
    elif cmd == "--block":
        ok, msg = block()
        print(("[OK] " if ok else "[!] ") + msg)
        return 0 if ok else 2
    elif cmd == "--unblock":
        ok, msg = unblock()
        print(("[OK] " if ok else "[!] ") + msg)
        return 0 if ok else 2
    else:
        print(f"Unknown command: {cmd}\n")
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
