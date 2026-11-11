# The gate holds an MSP only on a test that fails against the work

Status: accepted. Supersedes 0008. Amended by 0028.

The gate used to stop an MSP for any test it could not certify: `weak` when no altered copy of the work failed it, `inert` when it passed without the work, `inconclusive` when a run could not judge it. Every measured stop came from those verdicts and none showed wrong code, so builds that the reference planner finished ended unshipped.

The gate now holds an MSP only when a test it names exits 1 with the finished work present, because that is the one verdict about the code, and the owning Lane gets one repair first. Mutation stays because on real bugs it caught what the shipped tests and the scoped suite missed, but it runs as a finding for reviewers and never touches a file in a test location. A surviving mutant no longer runs the whole suite, since no decision depends on the answer.

Recovery follows the same rule everywhere: a Lane or check-author that fails gets one retry told why, and a lock refused twice lets the build continue with its tests reported instead of stopping the MSP. A Worker that reports failure twice still ships nothing, which is the case the reference planner gets wrong.

A retried Lane may write outside its write-set, and the gate judges that write, because a retry told it may do so and a gate blind to it would fail the same work again. It is still reported as undeclared, so a reviewer sees it. A repair is told only to change the work, so its outside writes stay out of the gate as any first attempt's do.

The repository's pre-commit hook runs once per MSP in a throwaway checkout with HEAD at the base and the index at the finished work. A run killed during the hook then leaves the branch where it was, and a hook's edits cannot differ from what the gate judged. The index and new objects a hook stages live in a quarantine beside that checkout, so a confined hook can stage without being able to write anything under the repository's `.git`. While a sandbox is in force, every git command mitosis runs outside it turns repository hooks and fsmonitor off, because the Workers' files can sit behind them.

The cost is that a test with no teeth can ship. Judging that belongs to review, which reads the Testing table, and not to a stage that stops a build it cannot repair.
