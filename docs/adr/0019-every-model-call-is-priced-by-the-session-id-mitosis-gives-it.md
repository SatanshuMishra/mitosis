# Every model call is priced by the session id mitosis gives it

Status: accepted. Supersedes ADR 0003, keeping the amendment of ADR 0016 on model replies.

mitosis gives every first call of a Worker dispatch, structure sample or brief call, retries included, a fresh session id through the {session_id} placeholder, and a re-ask reuses its first call's id; it reads each id's spend from that id's transcript when the run summary is written, priced then with the price table's date. A Lane's retries run in the same clone path and Lanes run at once, so a transcript cannot be matched to its call by path or by time. A call whose transcript is gone is counted as missing rather than as zero spend.
