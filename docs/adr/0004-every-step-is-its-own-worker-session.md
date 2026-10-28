# Every Step is its own Worker session

Status: accepted

mitosis gives every Step of a Lane its own Worker session, run one after another on the Lane's branch, and lands each session as its own commit, so an MSP branch holds one commit per Step in Step order. Steps share a Lane exactly when they share files, so splitting one finished Lane's changes by file afterwards could not tell which Step wrote a shared file's lines, and Workers are forbidden to touch the git index or history, so they cannot commit their own Step. The cost is one session start-up per Step instead of per Lane; --session-tokens still sizes each Step's estimate but no longer groups Steps into one session.
