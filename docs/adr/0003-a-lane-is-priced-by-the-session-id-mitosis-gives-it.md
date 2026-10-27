# A Lane is priced by the session id mitosis gives it

Status: accepted

mitosis gives every Worker dispatch, structure sample and brief call, retries included, a fresh session id, offered as the {session_id} placeholder, and reads that call's spend from the transcript of that id when the run summary is written, priced then with the price table's date. Lanes of one MSP share a worktree and run at once, so a transcript cannot be matched to its Lane by path or by time. A call whose template does not pass the id, or whose transcript is gone, is counted as missing rather than as zero spend.
