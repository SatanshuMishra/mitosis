# The gate adds no defence against a run that forges its result

Status: accepted.

An acceptance run executes the work it judges, in one process with the test, and the gate reads that run's exit status and what it leaves in the clean checkout. The security review of 0.20.0 found three ways a run can turn that against the gate, each already present in 0.19.1. A run can replace a dependency folder the gate linked with a real folder of its name, which 0029 records. A run can leave a file the gate cannot delete, so the next reset fails, the gate ends in an error with no verdict, and the MSP ships with the gate named as not run. And every run of one gate shares one temp folder, so a file one run leaves there is still present for the next.

Each was measured with a stub Worker on 0.19.1 and on 0.20.0. Work that fails its test is held with exit 21 on both. The same work, with a test that sets the user-immutable flag on a file it writes, ships with exit 0 and no finding on both. Work that ends the process with status 0 as the test imports it is recorded as a pass and ships on both, with the reverted run and mutation still in place on 0.19.1. None of the 206 MSP records kept from past runs that reached the gate holds a gate error.

No defence is added for any of the three. A Worker that sets out to forge a pass has the exit status itself, which no check that shares a run with the work can take away, so closing the undeletable file or the shared temp folder would leave the simpler forgery open and add code that no recorded run has needed. What the gate guarantees is for work built in good faith: a named test that fails against the finished work holds the MSP. A forgery is written in the diff, and reading the diff is review's, as 0028 says of whether a test has teeth.

This reopens the first time a run on record ends in a gate error after one of its acceptance runs has started, or a review or a failure after merge shows a shipped MSP whose work or tests forged an acceptance result.
