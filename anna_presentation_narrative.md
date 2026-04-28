# Presentation Narrative

**Presenter:** Anna Monsó Rodríguez
**Audience:** Gnosis Research Center — HPC & I/O group, which is actively working on agents
**Duration:** 15 minutes (graded)

---

## The framing problem

The audience sits at the intersection the thesis itself sits at: deep roots in HPC I/O, and active current work on agents. The talk should not argue that agents are relevant to the group — the group already knows — and should not treat agent observability as a foreign topic being introduced for the first time. What the talk must do is place Anna's specific contribution on a trajectory the group already recognizes as important, so that her work reads as a natural next step rather than a pivot.

The trajectory is a clean one. The HPC I/O community spent roughly two decades building out characterization tools for parallel applications — Darshan, Recorder, Dayu, and their relatives — because it became clear that distributed scientific workloads could not be optimized without being systematically traced, and that no single point of observation was sufficient. The tools matured together with the workloads they were meant to characterize. Agent systems today are roughly where parallel I/O was in the late 2000s: widely deployed, increasingly distributed, and almost entirely uncharacterized. The same methodological move that worked then — build the characterization infrastructure first, use it to produce real numbers on real workloads, let optimization follow from measurement — is the move Anna's thesis is making now.

That is the narrative. Everything else in the talk is a consequence of it.

---

## Narrative arc

Five movements, in order. The proportions given are guidance rather than strict time budgets.

### Movement 1 — The trajectory (roughly the first two minutes)

The opening should be short and declarative. Suggested shape:

> For two decades this community has built characterization tools for parallel scientific workloads. Darshan, Recorder, Dayu, and their successors exist because the community decided early that distributed I/O could not be optimized without being measured, and that no single layer of observation was sufficient. Multi-layer tracing, correlated across nodes, is now the default expectation for any serious HPC performance study.
>
> Agent systems today occupy the position parallel I/O occupied when that work began. They are widely deployed, increasingly distributed across hosts, and almost entirely uncharacterized. We do not have systematic measurements of where agents spend time, how much context they carry, what their memory behavior looks like, or how work flows between agents on different nodes. The optimization claims in the current literature — that tool execution dominates, that retries matter, that context management is expensive — are plausible but mostly unmeasured.
>
> This thesis is the characterization infrastructure for that gap. It is what Darshan and Dayu are for parallel I/O, built for multi-agent AI workloads.

The comparison is the whole opening. It does not need to be argued; the group already accepts the underlying methodology. It only needs to be stated clearly, and then the talk moves on.

### Movement 2 — What needs to be characterized

Before the design, the talk should name what the framework is actually trying to measure. This is where it connects most directly to the group's evaluation instincts. When this group traces a parallel application, they want numbers on I/O volume, memory usage, communication patterns, and the structural semantics of the computation. An agent observability framework needs the analogous set, one abstraction up:

- **I/O behavior.** Every tool call is an I/O operation — a web fetch, a database query, a filesystem read, a call to another service. Characterizing an agent means measuring the volume, latency, and pattern of these operations, the same way characterizing a scientific application means measuring its read/write behavior.
- **Memory and context usage.** Agents carry context — tokens, conversation history, tool outputs — and that context has a cost profile over time. How large does it grow, how does it get pruned, how much of it is resident at each step. This is the agent analogue of memory footprint characterization for parallel jobs.
- **Context semantics.** What is actually in the context at each turn, and how does the agent's attention to it change the trajectory of the run. This has no clean analogue in traditional I/O tracing, and it is part of what makes agent characterization genuinely new work rather than a straight port of existing tools.
- **Cross-node work patterns.** When agents delegate to other agents on other hosts, the shape of that delegation — who calls whom, how often, with what payload — is the kind of communication pattern the group already knows how to reason about, transplanted to a higher layer.

Framing the evaluation goals this way, early in the talk, does two things. It tells the audience that Anna knows what it means to characterize a distributed system in the sense they care about, and it sets up the later evaluation section as producing the kind of measurements they expect from this kind of work.

### Movement 3 — Why one observation point is not enough

With the trajectory and the evaluation goals established, the talk narrows to the concrete design problem. A single user query to an agent produces two HTTP calls to the LLM provider. Between them, the agent performs a web search, fetches a page, summarizes it, and possibly retries. A transparent HTTP proxy — the obvious first instrumentation move — sees the two network calls and the wall time between them. It does not see the tool work, which is often where most of the time goes.

The structural point for this audience is immediate: the proxy's blind spot is the same shape as Darshan's blind spot with respect to computation between I/O calls. The network boundary is one useful observation point, not a sufficient one. This is why the framework has two capture channels — a transparent proxy for the network boundary, and a semantic span library that agents import to self-report the internal steps the network cannot see. The two channels land in one backend, presented through one interface, for the same reason the group argues that I/O and compute traces belong on a common timeline: separate tools force the analyst to reconstruct the join, and the reconstruction is where the errors are.

That is the single-agent design, stated as briefly as it can be stated.

### Movement 4 — The design, in enough detail to be credible

This is where the talk slows down and earns its technical weight. Three design elements deserve airtime, in order of how much they matter for this audience.

**The two-channel architecture, concretely.** The HTTP proxy is a transparent reverse proxy in front of the LLM provider. Agents point their base URL at it with a single environment variable; no client-library changes are needed. The proxy records request and response bodies, reconstructs streaming responses, persists to SQLite keyed by session ID, and is provider-aware for OpenAI, Anthropic, and Ollama. The semantic channel is a small span library, available in Python and Go with an identical JSON schema enforced by golden tests, that agents use to emit spans around their internal operations — `tool.fetch`, `tool.summarize`, `llm.generate`, `llm.retry_wait`, and so on. Spans are forwarded over HTTP to the same backend that holds the proxy's capture, land in a parallel table, and are joined with the network events by session ID. Both channels share one Flask process, one SQLite file, and one React interface.

**The distributed extension.** When agents run on different hosts, three things have to happen. Each agent's own timeline must be captured (the two channels already handle this). The host identity of each interaction must be recorded correctly, which turns out to be harder than it sounds because the agent CLI cannot set custom HTTP headers per request — the solution is to encode host, scenario, and session as a URL prefix that the dispatcher parses and stamps onto the record. And inter-agent calls, which often do not touch the LLM provider at all, must be captured through a separate explicit channel: the calling agent (or a relay wrapping it) posts start and done events to an ingest endpoint, and the pair is stitched into a single edge. The stitching uses a caller-chosen correlation identifier, which gives the framework a clean way to track cross-host causality without depending on any particular property of the hosts' clocks.

**The unified interface.** The framework exposes three tabs over the same backend. A per-agent workspace view for drilling into the timeline of a single run. A scenarios view that draws host rectangles, agent nodes within them, and inter-agent edges between them — this is what the group will recognize as the "shape of the job" view, analogous to how they would look at a parallel application's rank topology. And an analytics view built on the forwarded spans, where stage-level cost breakdowns across runs become legible. The argument for unifying these is the same argument the group makes for any other coordinated multi-layer tracing work: the value is in the join, and the join has to be something the user can see.

Throughout this section, the talk should resist the temptation to describe implementation details — schema versions, table names, storage internals, Flask route organization — that belong in a written document. The goal is to leave the audience convinced that the design is real and considered, not to walk them through the code.

### Movement 5 — Evaluation: producing the numbers the group expects

The talk closes on the evaluation, framed as the point of the whole exercise. The framework exists to produce characterization data; this is what that data looks like.

Two workloads. The first is a twenty-query cross-language sweep on FreshQA, with a Python agent and a Go agent running the same model, the same tools, and the same queries. This workload stresses span completeness, overhead, and schema quality, and it produces the measurements one would expect from a characterization framework: I/O-style statistics on tool calls (how many, how long, which dominate), token and context usage across the run, latency breakdowns by stage. The second is a two-host SLURM scenario in which a planner on one compute node delegates to an executor on another. This workload stresses cross-node attribution and inter-agent edge capture, and it produces the topology-style measurements that make agent coordination legible at all.

One result from the first workload deserves extra attention, because it validates the methodology rather than just the system. The initial cross-language measurement reported Go running roughly 1.43× slower than Python on the LLM stage. This was not physically plausible — both agents were calling the same model with the same inputs. Inspection of the span trace revealed that retry-wait intervals were being attributed to the generate span rather than represented as sibling spans, and Go's HTTP client retried more aggressively under rate limits. The fix was a schema-level change: retry waits became their own span type, siblings of the retried generation rather than children. No agent code was modified. The revised measurement showed Go at roughly 1.01× of Python — an honest tie.

The framing for the audience is the key move here. This is the kind of result the HPC tracing community knows well: the early versions of most major trace tools produced misleading attribution, and the field's response was to refine the event schemas until the attribution became honest. A framework that makes that refinement possible is doing its job. Anna should pause briefly after stating the corrected figure — the result is genuinely surprising, and the audience should be given a moment to absorb it.

The talk then returns to the trajectory it opened on. The characterization infrastructure for parallel I/O took two decades to mature into the set of tools the group uses daily. Agent systems are at the beginning of that curve, and this thesis is the beginning of that curve's work — the first serious attempt at a multi-layer, cross-node, semantically aware framework for characterizing what multi-agent AI workloads actually do. The methodology is the group's own; the workload is new; the evaluation goals are recognizable. That is the note to close on.

---

## Two rhetorical tasks, emphasized

The opening and the FreshQA result carry the most weight and the most risk.

The opening has to place the work on a trajectory the audience already respects, without overselling the analogy or getting lost in the comparison. Anna should state the trajectory plainly, name Darshan and Dayu as reference points, and move on within two minutes. The audience does not need convincing that characterization infrastructure matters; they need placing so they understand where this particular piece of infrastructure fits.

The FreshQA retry-split result has to land as a genuinely interesting measurement outcome, not as a bug story. The framing — that the framework caught an artifact in its own measurement by exposing a structural question the single-channel tools could not have asked — is what converts it from debugging anecdote to methodological validation. Anna should practice this passage until the distinction is natural.

## A note on what to leave out

The talk should not spend time on vendor comparisons with LangSmith, Arize Phoenix, or Langfuse. Those comparisons belong in the written thesis, not in a talk to this audience. The audience will not know the products well, and dwelling on them will make the work sound like a product evaluation rather than a research contribution. A single sentence acknowledging that existing LLM-observability platforms operate at one layer and on one host, earlier in the talk, is enough.

Similarly, implementation minutiae — SQLite schema versions, specific Flask routes, the exact structure of the `InteractionRecord` struct — should stay out. They are credibility furniture at best and time sinks at worst. The design section above is calibrated to leave the audience convinced the system is real without walking through its internals.
