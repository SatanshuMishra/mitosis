# The Worker sandbox is opt-in

Status: accepted

`--worker-sandbox` runs every Worker under sandbox-exec with a generated profile that refuses file writes except to its clone, a private temp folder passed as TMPDIR, its session transcript folder, /dev/null, /dev/tty, /dev/fd and each `--worker-write` path. A host without sandbox-exec refuses a sandboxed run with exit 3 before any Worker starts, and the JSON summary reports sandbox as sandbox-exec or off, so an unsandboxed run is never mistaken for a sandboxed one. We made it opt-in rather than default because Linux has no sandbox-exec and the existing build tests use stub Workers that write a log beside themselves, outside any clone. The transcript folder is allowed because a Worker confined to its clone wrote no session transcript, which the per-Lane pricing reads. Without the flag, the clone still keeps git state apart, but a Worker can write any path the caller's account can.
