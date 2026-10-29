import json
import os
import re
import uuid
from datetime import datetime, timezone

CHECK_ID = re.compile(r"^C[0-9]+\.[0-9]+$")
CHECK_STATUS = {
    "pass": "pass",
    "weak": "fail",
    "inert": "fail",
    "inconclusive": "could-not-run",
    "not-applicable": "could-not-run",
}


def _check_tags(prop):
    check = prop.get("check")
    if not isinstance(check, str) or CHECK_ID.match(check) is None:
        return ()
    return ("check:%s=%s" % (check, CHECK_STATUS.get(prop.get("outcome"), "could-not-run")),)


def _minutes(minutes):
    return "%.2f" % round(minutes, 2)


def entry(repo, run, msp, commit, prop, minutes):
    fixed = prop.get("outcome") == "pass"
    reason = prop.get("reason")
    symptom = {} if fixed or not isinstance(reason, str) else {"symptom": reason}
    tags = (
        ("kind:gate", "run:%s" % run, "msp:%s" % msp, "commit:%s" % commit)
        + _check_tags(prop)
        + ("usd:0", "minutes:%s" % _minutes(minutes))
    )
    return {
        "repo": repo,
        "surface": "%s::%s" % (prop["file"], prop["test"]),
        "surface_key": "%s/%s" % (run, msp),
        "outcome": "fixed" if fixed else "unverified-reasoned",
        **symptom,
        "files": [prop["file"]],
        "tags": list(tags),
    }


def _stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def append(path, entries):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    lines = "".join(
        json.dumps({"id": str(uuid.uuid4()), "ts": _stamp(), **item}, separators=(",", ":")) + "\n"
        for item in entries
    )
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(lines)
