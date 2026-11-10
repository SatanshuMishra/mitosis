import os

RECON_CONTRACT = """You are doing RECON for a parallel build planner. You write NO code.

Read the request below and return the work-items it decomposes into. Return ONE
line of JSON and nothing after it:

{"items":[{ ... }, ...]}

Each item is one LEAF - the smallest piece one agent can build in one sitting:

  name            short kebab-case id, unique
  task            what to DO, specific enough for an agent with NO other
                  context to act on. This is the entire brief it will get.
  files           every file the work will EDIT. Include whatever this project
                  makes new work register itself in - the file that lists,
                  exports, wires or enumerates things of that kind. Nothing in
                  the request will mention it, and it is the file two unrelated
                  additions most often collide in. Find it by looking at how an
                  EXISTING thing of the same kind is hooked up, rather than by
                  guessing a name: whatever that one had to touch, yours will
                  too.
  file_notes      {path: what changes in that file}, for each file above
  type            fix | feature | port | design | sweep | contract
  complexity      simple  (mechanical, fully specified)
                | complex (needs judgement, unfamiliar, or easy to get wrong)
  acceptance      one observable that proves it works
  after           names of items that must be BUILT first (optional)
  contract_group  shared id for two halves of one interface (optional)

Rules that decide the plan, so get them right:

- SPLIT the request into independent leaves wherever you honestly can - leaves
  that do not depend on each other run in parallel. Never fake independence: a
  dependency you can see belongs in `after`, where it costs one sequential step
  instead of a wrong build.
- `files` is what merge safety is computed from. Two leaves that edit one file
  are forced to one agent, so an under-predicted list is the one error that
  breaks the plan rather than slowing it.
- Two halves of one interface (an endpoint and its caller) share a
  `contract_group`. If you also emit a `type: contract` leaf they both declare
  `after`, they build in PARALLEL against a pinned shape; without it they are
  serialized under one agent.

HOW TO FIND THE FILES - in this order, and stop at the first that answers:

  1. THE GRAPH. If a graphify graph.json is named below, query it FIRST. It
     already knows what renders a surface, what imports what, and which other
     files sit next to the one you found. Guessing when the graph is there is
     the most expensive mistake available to you: an under-predicted file list
     is what makes the planner run two colliding leaves in parallel.
  2. THE CODEBASE. No graph, or the graph does not cover it - grep for the
     surface the request names: the label, the route, the component, the
     column. Follow the imports out from what you hit.
  3. THE STRUCTURE. Net-new work has nothing to grep, because the thing does
     not exist yet. Read the directory and module layout and place the work
     where its neighbours live - then add the registries that neighbours are
     registered in."""

LINES = tuple(RECON_CONTRACT.splitlines())

SPEC_LINES = LINES[12:20] + LINES[30:37] + LINES[42:]

COMPLEXITY_LINES = LINES[22:24]

GRAPH_LINE = (
    "A graphify graph IS available at %s - query it FIRST, before "
    "grepping. It is the difference between a predicted file list "
    "and a guessed one."
)

NO_GRAPH_LINE = (
    "No graphify graph is available, so fall back to the codebase: grep "
    "for the surface the request names, then follow imports; for net-new "
    "work read the directory structure and place it beside its "
    "neighbours."
)


def source_line(graph_path):
    if graph_path and os.path.exists(graph_path):
        return GRAPH_LINE % graph_path
    return NO_GRAPH_LINE


def prompt(ask, graph_path=None, template=None, contract=RECON_CONTRACT):
    source = source_line(graph_path)
    if template:
        return (
            template.replace("{ask}", ask)
            .replace("{graph}", graph_path or "")
            .replace("{source}", source)
            .replace("{contract}", contract or "")
        )
    return "%s\n\n%s\n\nRequest:\n%s" % (contract, source, ask)


LONGEST_PASSABLE_TEXT = 100000


def passable_text(text):
    if text is None or "\0" in text or len(text.encode("utf-8")) > LONGEST_PASSABLE_TEXT:
        return ""
    return text


def environment(ask, graph_path=None):
    return {
        "MITOSIS_RECON": "1",
        "MITOSIS_RECON_ASK": passable_text(ask),
        "MITOSIS_RECON_GRAPH": graph_path or "",
    }
