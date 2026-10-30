import atexit
import json
import os
import sys
import threading

CO_OPTIMIZED = 0x0001

TRACE_FOLDER = "MITOSIS_LINE_TRACE"

TRACE_ROOT = "MITOSIS_TRACE_ROOT"


def _relative_place(prefix, places, filename):
    if filename in places:
        return places[filename]
    try:
        real = None if filename.startswith("<") else os.path.realpath(filename)
        found = (
            real[len(prefix):].replace(os.sep, "/")
            if real is not None and real.startswith(prefix)
            else None
        )
    except Exception:
        found = None
    places[filename] = found
    return found


def _written_records(records):
    by_path = {}
    for path, function, names, lines in records.values():
        entry = {"function": function, "names": sorted(set(names)), "lines": sorted(lines)}
        by_path = {**by_path, path: by_path.get(path, ()) + (entry,)}
    return {
        path: sorted(entries, key=lambda entry: (entry["lines"], entry["function"], entry["names"]))
        for path, entries in sorted(by_path.items())
    }


def _writer(folder, records):
    def write():
        try:
            sys.settrace(None)
            threading.settrace(None)
            if not records:
                return
            target = os.path.join(folder, "%d.json" % os.getpid())
            partial = target + ".partial"
            with open(partial, "w", encoding="utf-8") as handle:
                json.dump(_written_records(dict(records)), handle, sort_keys=True)
            os.replace(partial, target)
        except Exception:
            return

    return write


def _tracer(prefix, records):
    places = {}

    def trace(frame, event, arg):
        try:
            code = frame.f_code
            path = _relative_place(prefix, places, code.co_filename)
            if path is None:
                return None
            record = records.get(code)
            if record is None:
                record = (path, bool(code.co_flags & CO_OPTIMIZED), tuple(code.co_names), set())
                records[code] = record
            lines = record[3]
        except Exception:
            return None

        def line(frame, event, arg):
            if event == "line":
                lines.add(frame.f_lineno)
            return line

        return line

    return trace


def _install():
    folder = os.environ.get(TRACE_FOLDER)
    root = os.environ.get(TRACE_ROOT)
    if not folder or not root:
        return
    prefix = os.path.join(os.path.realpath(root), "")
    records = {}
    trace = _tracer(prefix, records)
    atexit.register(_writer(folder, records))
    sys.settrace(trace)
    threading.settrace(trace)


def _chain():
    import importlib.machinery
    import importlib.util

    here = os.path.realpath(os.path.dirname(os.path.abspath(__file__)))
    positions = [
        index
        for index, entry in enumerate(sys.path)
        if os.path.realpath(entry or os.getcwd()) == here
    ]
    later = sys.path[positions[0] + 1:] if positions else []
    remaining = [entry for entry in later if os.path.realpath(entry or os.getcwd()) != here]
    spec = importlib.machinery.PathFinder.find_spec("sitecustomize", remaining)
    if spec is None or spec.loader is None:
        return
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)


try:
    _install()
except Exception:
    pass

try:
    _chain()
except Exception:
    pass
