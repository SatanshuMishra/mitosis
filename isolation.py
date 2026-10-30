import ctypes
import functools
import os
import re
import shutil
import signal
import stat
import time
import unicodedata

import run

CLONE_FOLDER = "clones"

SANDBOX_PROGRAM = "sandbox-exec"

SANDBOX_OFF = "off"

TRANSCRIPT_KEY = re.compile(r"[^A-Za-z0-9]")

WRITABLE_DEVICES = ("/dev/null", "/dev/tty")

WRITABLE_DEVICE_PATTERN = "^/dev/fd/"

CLONE_GIT = ".git"

HFS_IGNORABLE = re.compile("[\u200c-\u200f\u202a-\u202e\u206a-\u206f\ufeff]")


LOCK_CLONE = "lock"

REAP_LIMIT = 10

REAP_PAUSE = 0.02

SYSTEM_LIBRARY = "/usr/lib/libSystem.B.dylib"

MARKED_OPERATION = b"file-read-data"

PATH_FILTER = 1

UNREPORTED_CHECK = 0x40000000

PID_SLACK = 256

SETTLE_ROUNDS = 50

DENIED = 1


def lane_folders(run_dir, lane):
    return stage_folders(run_dir, "lane-%d" % lane)


def lock_folders(run_dir):
    return stage_folders(run_dir, LOCK_CLONE)


def stage_folders(run_dir, name):
    root = os.path.join(os.path.abspath(run_dir), CLONE_FOLDER)
    return {
        "clone": os.path.join(root, name),
        "tmp": os.path.join(root, name + ".tmp"),
        "profile": os.path.join(root, name + ".sb"),
        "mark": os.path.join(root, name + ".mark"),
    }


def _opened_for_removal(folder):
    os.chmod(folder, stat.S_IMODE(os.lstat(folder).st_mode) | stat.S_IRWXU)
    for top, names, _ in os.walk(folder):
        for name in names:
            path = os.path.join(top, name)
            if not os.path.islink(path):
                os.chmod(path, stat.S_IMODE(os.lstat(path).st_mode) | stat.S_IRWXU)


def _remove_left(path):
    if os.path.islink(path):
        os.remove(path)
    elif os.path.isdir(path):
        _opened_for_removal(path)
        shutil.rmtree(path)
    elif os.path.lexists(path):
        os.remove(path)


def remove_lane_folders(folders):
    for key in ("clone", "tmp", "profile", "mark"):
        _remove_left(folders[key])


def cut_clone(tree, folders):
    remove_lane_folders(folders)
    start = run.branch_commit(tree["path"], tree["branch"], required=True)
    os.makedirs(os.path.dirname(folders["clone"]), exist_ok=True)
    run.git(
        [
            "clone",
            "--quiet",
            "--no-local",
            "--no-hardlinks",
            "--single-branch",
            "--branch",
            tree["branch"],
            tree["path"],
            folders["clone"],
        ],
        tree["path"],
    )
    run.git(["remote", "remove", "origin"], folders["clone"])
    return start


def clone_ready(folders, record):
    return bool(record.get("start")) and os.path.isdir(folders["clone"])


def sandbox_program(requested):
    if not requested:
        return None
    found = shutil.which(SANDBOX_PROGRAM)
    if found is None:
        raise run.ConfigError(
            "--worker-sandbox needs %s, which is not on PATH, so no Worker was started"
            % SANDBOX_PROGRAM
        )
    return found


def sandbox_name(program):
    return SANDBOX_OFF if program is None else SANDBOX_PROGRAM


def worker_home(env):
    home = (env if env is not None else os.environ).get("HOME")
    return home if home else os.path.expanduser("~")


def _profile_string(path):
    if "\n" in path:
        raise run.ConfigError(
            "the Worker sandbox profile cannot hold %r, because it holds a newline" % path
        )
    return '"%s"' % path.replace("\\", "\\\\").replace('"', '\\"')


def refuse_unquotable(paths):
    for path in paths:
        _profile_string(path)


def _folded_name(part):
    return HFS_IGNORABLE.sub("", unicodedata.normalize("NFC", part)).casefold()


def _identity_parts(path):
    parts = tuple(part for part in os.path.realpath(path).split(os.sep) if part)
    found = ()
    for depth in range(len(parts)):
        try:
            status = os.stat(os.sep + os.path.join(*parts[: depth + 1]))
        except OSError:
            return found + tuple(("name", _folded_name(part)) for part in parts[depth:])
        found = found + (("node", status.st_dev, status.st_ino),)
    return found


def _overlapping(first, second):
    shorter = min(len(first), len(second))
    return first[:shorter] == second[:shorter]


def refuse_overlapping_writes(run_dir, writes):
    folder = _identity_parts(run_dir)
    for path in writes:
        if _overlapping(folder, _identity_parts(path)):
            raise run.OverlappingWrite(
                "--worker-write %s overlaps the run folder %s; sandboxed code may never write "
                "mitosis's own state, logs or gate folders, so no Worker was started" % (path, run_dir)
            )
    return tuple(os.path.realpath(path) for path in writes)


def transcript_folder(home, clone):
    return os.path.join(home, ".claude", "projects", TRANSCRIPT_KEY.sub("-", os.path.realpath(clone)))


def sandbox_profile(allowed, pinned=(), mark=None):
    rules = (
        tuple("(subpath %s)" % _profile_string(path) for path in allowed)
        + tuple("(literal %s)" % _profile_string(device) for device in WRITABLE_DEVICES)
        + ('(regex #"%s")' % WRITABLE_DEVICE_PATTERN,)
    )
    pins = (
        "(deny file-write* %s)\n" % " ".join("(literal %s)" % _profile_string(path) for path in pinned)
        if pinned
        else ""
    )
    marked = (
        "(deny %s (literal %s))\n" % (MARKED_OPERATION.decode("ascii"), _profile_string(mark))
        if mark
        else ""
    )
    return "(version 1)\n(allow default)\n(deny file-write*)\n(allow file-write* %s)\n%s%s" % (
        " ".join(rules),
        pins,
        marked,
    )


def _confinement(sandbox, folders, temporary, writable):
    pinned = tuple(os.path.realpath(path) for path in (temporary,) + tuple(writable))
    allowed = pinned + tuple(sandbox["writes"])
    with open(folders["mark"], "w", encoding="utf-8"):
        pass
    profile, mark = os.path.realpath(folders["profile"]), os.path.realpath(folders["mark"])
    with open(profile, "w", encoding="utf-8") as handle:
        handle.write(sandbox_profile(allowed, pinned, mark))
    return {"program": sandbox["program"], "profile": profile, "mark": mark, "tmp": pinned[0]}


def confined(argv, env, confinement):
    if confinement is None:
        return list(argv), env
    return (
        [confinement["program"], "-f", confinement["profile"], *argv],
        {**(env if env is not None else os.environ), "TMPDIR": confinement["tmp"]},
    )


def worker_confinement(folders, sandbox, env):
    if sandbox is None:
        return None
    os.makedirs(folders["tmp"])
    transcript = transcript_folder(worker_home(env), folders["clone"])
    os.makedirs(transcript, exist_ok=True)
    return _confinement(sandbox, folders, folders["tmp"], (folders["clone"], transcript))


def gate_folders(log_dir):
    root = os.path.abspath(log_dir)
    return {"log": root, "tmp": root + ".tmp", "profile": root + ".sb", "mark": root + ".mark"}


def gate_confinement(sandbox, log_dir, checkout):
    if sandbox is None:
        return None
    folders = gate_folders(log_dir)
    release_gate(folders)
    os.makedirs(folders["tmp"])
    return _confinement(sandbox, folders, folders["tmp"], (checkout,))


def gate_outputs(log_dir, confinement):
    return log_dir if confinement is None else confinement["tmp"]


def release_gate(folders):
    for key in ("tmp", "profile", "mark"):
        _remove_left(folders[key])


@functools.lru_cache(maxsize=None)
def _system_calls():
    library = ctypes.CDLL(SYSTEM_LIBRARY, use_errno=True)
    check = library.sandbox_check
    check.restype = ctypes.c_int
    check.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    listing = library.proc_listallpids
    listing.restype = ctypes.c_int
    listing.argtypes = [ctypes.c_void_p, ctypes.c_int]
    return check, listing


def _every_pid(listing):
    count = listing(None, 0)
    if count <= 0:
        return None
    room = (ctypes.c_int * (count + PID_SLACK))()
    found = listing(room, ctypes.sizeof(room))
    if found <= 0 or found >= len(room):
        return None
    return tuple(pid for pid in room[:found] if pid > 0)


def _denied(check, pid, path):
    return check(pid, MARKED_OPERATION, PATH_FILTER | UNREPORTED_CHECK, os.fsencode(path))


def _inside(check, pid, confinement):
    return (
        _denied(check, pid, confinement["mark"]) == DENIED
        and _denied(check, pid, confinement["profile"]) == 0
    )


def _sandboxed_pids(confinement):
    if confinement is None:
        return ()
    check, listing = _system_calls()
    own = os.getpid()
    if not os.path.isfile(confinement["mark"]) or _denied(check, own, confinement["mark"]) != 0:
        return None
    checked = frozenset((own,))
    for _ in range(SETTLE_ROUNDS):
        listed = _every_pid(listing)
        if listed is None:
            return None
        born = tuple(pid for pid in listed if pid not in checked)
        if not born:
            return ()
        found = tuple(pid for pid in born if _inside(check, pid, confinement))
        if found:
            return found
        checked = checked | frozenset(born)
    return None


def _group_left(group):
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _signalled(send, target):
    try:
        send(target, signal.SIGKILL)
    except OSError:
        pass


def _end_left(group, confinement, sandboxed):
    _signalled(os.killpg, group)
    for pid in sandboxed:
        if _inside(_system_calls()[0], pid, confinement):
            _signalled(os.kill, pid)


def end_run(proc, confinement, folder):
    deadline = time.monotonic() + REAP_LIMIT
    while True:
        proc.poll()
        sandboxed = _sandboxed_pids(confinement)
        if sandboxed == () and not _group_left(proc.pid):
            return
        if time.monotonic() >= deadline:
            raise run.LingeringProcess(folder, REAP_LIMIT)
        _end_left(proc.pid, confinement, sandboxed or ())
        time.sleep(REAP_PAUSE)


def _clone_entry(clone, path):
    full = run.tree_path(clone, path, last=False)
    try:
        found = os.lstat(full)
    except (FileNotFoundError, NotADirectoryError):
        return None
    if stat.S_ISLNK(found.st_mode):
        return run.SYMLINK_MODE, os.fsencode(os.readlink(full))
    if stat.S_ISDIR(found.st_mode):
        raise run.LinkRefusal(
            path, "%s is a folder in the Worker's clone, where the write-set holds a file" % path
        )
    if not stat.S_ISREG(found.st_mode):
        raise run.LinkRefusal(path, "it is neither a regular file nor a symbolic link in the clone")
    data = run._read_bytes(clone, path)
    if data is None:
        raise run.LinkRefusal(path, "mitosis could not read it from the Worker's clone")
    return (run.EXECUTABLE_MODE if found.st_mode & stat.S_IXUSR else run.REGULAR_MODE), data


def write_set_entries(clone, write_set):
    return {path: _clone_entry(clone, path) for path in sorted(set(write_set))}


def place_entries(tree, entries):
    for path, entry in sorted(entries.items()):
        if entry is None:
            run._drop_entry(tree, path)
        else:
            run._place(tree, path, *entry)


def _names_git_folder(part):
    return _folded_name(part) == CLONE_GIT


def _git_depth(path):
    parts = run._parts(path)
    return next((depth for depth, part in enumerate(parts, 1) if _names_git_folder(part)), None)


def _inside_nested_git(path):
    return _git_depth(path) is not None


def _reported(path):
    depth = _git_depth(path)
    return path if depth is None else "/".join(run._parts(path)[:depth])


def _escapes(clone, path):
    root = os.path.realpath(clone)
    resolved = os.path.realpath(os.path.join(clone, *run._parts(path)))
    return not resolved.startswith(root + os.sep)


def _ignored(tree, paths):
    if not paths:
        return frozenset()
    args = ["check-ignore", "--no-index", "--stdin", "-z"]
    data = b"".join(os.fsencode(path) + b"\0" for path in paths)
    completed = run.git_run(args, tree, raw=True, data=data)
    if completed.returncode not in (0, 1):
        raise run.GitError(args, completed.returncode, completed.stderr)
    return frozenset(os.fsdecode(name) for name in completed.stdout.split(b"\0") if name)


def _ignored_folders(tree, paths):
    folders = sorted({prefix + "/" for path in paths for prefix in run._prefixes(path)[:-1]})
    return _ignored(tree, folders)


def _ignored_report(tree, paths):
    folders = _ignored_folders(tree, paths)
    return sorted(
        {
            next(
                (prefix + "/" for prefix in run._prefixes(path)[:-1] if prefix + "/" in folders),
                path,
            )
            for path in paths
        }
    )


def _clone_file(clone, path):
    if _escapes(clone, path):
        return None
    data = run._read_bytes(clone, path)
    if data is None:
        return None
    executable = os.lstat(os.path.join(clone, *run._parts(path))).st_mode & stat.S_IXUSR
    return (run.EXECUTABLE_MODE if executable else run.REGULAR_MODE), data


def outside_writes(clone, tree, start_entries, write_set):
    linked = tuple(path for path, (mode, _) in start_entries.items() if mode == run.GITLINK_MODE)
    held = {path: entry for path, entry in start_entries.items() if entry[0] != run.GITLINK_MODE}
    found = run._gate_listing(clone, (CLONE_GIT,))

    def outside(path):
        return not run._under(path, write_set) and not run._under(path, linked)

    gone = tuple(
        path for path in sorted(held) if outside(path) and found.get(path) in (None, "dir")
    )
    changed = tuple(
        path
        for path in sorted(held)
        if outside(path)
        and found.get(path) not in (None, "dir")
        and run._differs(clone, path, held[path][0], held[path][1], found[path])
    )
    fresh = tuple(
        path
        for path, kind in sorted(found.items())
        if kind != "dir" and path not in held and outside(path)
    )
    ignored = _ignored(tree, fresh)
    added = tuple(path for path in fresh if path not in ignored)
    dropped = tuple(path for path in fresh if path in ignored)
    deletes = tuple(path for path in gone if not _escapes(clone, path))
    candidates = tuple(
        path
        for path in changed + added
        if found[path] == "file" and not _inside_nested_git(path)
    )
    read = {path: _clone_file(clone, path) for path in candidates}
    writes = {path: entry for path, entry in read.items() if entry is not None}
    unreplayed = sorted(
        {_reported(path) for path in changed + added if path not in writes}
        | {path for path in gone if path not in deletes}
    )
    return {
        "writes": writes,
        "deletes": deletes,
        "unreplayed": unreplayed,
        "ignored": _ignored_report(tree, dropped),
    }


def read_lane(clone, tree, start, write_set):
    entries = write_set_entries(clone, write_set)
    outside = outside_writes(clone, tree, run._committed_entries(tree, start), write_set)
    return {**outside, "entries": entries}


def replay(tree, landing, commit):
    landed = run._committed_entries(tree, commit)
    paths = tuple(sorted(set(landing["deletes"]) | set(landing["writes"])))
    for path in landing["deletes"]:
        run._drop_entry(tree, path)
    for path, (mode, data) in sorted(landing["writes"].items()):
        run._place(tree, path, mode, data)
    return {path: list(landed[path]) if path in landed else None for path in paths}


def released(record, paths):
    replayed = record.get("replayed") or {}
    kept = {path: entry for path, entry in replayed.items() if not run._under(path, paths)}
    return record if kept == replayed else {**record, "replayed": kept}


def msp_replayed(plan, msp, lane_records):
    found = {}
    for index, lane in enumerate(plan.get("lanes") or []):
        record = lane_records.get(str(index)) or {}
        if lane["msp"] != msp or record.get("state") != "ok":
            continue
        for path, entry in sorted((record.get("replayed") or {}).items()):
            found = found if path in found else {**found, path: entry}
    return found


def _msp_paths(plan, msp, lane_records, key):
    return sorted(
        {
            path
            for index, lane in enumerate(plan.get("lanes") or [])
            if lane["msp"] == msp
            for path in (lane_records.get(str(index)) or {}).get(key) or ()
        }
    )


def msp_unreplayed(plan, msp, lane_records):
    return _msp_paths(plan, msp, lane_records, "unreplayed")


def msp_ignored(plan, msp, lane_records):
    return _msp_paths(plan, msp, lane_records, "ignored")


def held_tree(entries, replayed):
    kept = {path: entry for path, entry in entries.items() if path not in replayed}
    return {
        **kept,
        **{
            path: (entry[0], entry[1], None)
            for path, entry in sorted(replayed.items())
            if entry is not None
        },
    }
