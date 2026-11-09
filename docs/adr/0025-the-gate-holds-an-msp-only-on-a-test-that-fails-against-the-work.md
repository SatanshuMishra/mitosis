# The gate holds an MSP only on a test that fails against the work

Status: accepted. Supersedes 0008.

The gate used to stop an MSP for any test it could not certify: `weak` when no altered copy of the work failed it, `inert` when it passed without the work, `inconclusive` when a run could not judge it. Every measured stop came from those verdicts and none showed wrong code, so builds that the reference planner finished ended unshipped.

The gate now holds an MSP only when a test it names exits 1 with the finished work present, because that is the one verdict about the code, and the owning Lane gets one repair first. Mutation stays because on real bugs it caught what the shipped tests and the scoped suite missed, but it runs as a finding for reviewers and never touches a file in a test location. A surviving mutant no longer runs the whole suite, since no decision depends on the answer.

Recovery follows the same rule everywhere: a Lane or check-author that fails gets one retry told why, and a lock refused twice lets the build continue with its tests reported instead of stopping the MSP. A Worker that reports failure twice still ships nothing, which is the case the reference planner gets wrong.

The cost is that a test with no teeth can ship. Judging that belongs to review, which reads the Testing table, and not to a stage that stops a build it cannot repair.
