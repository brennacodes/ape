# APE

**Agentic Process Engine** — an XML markup language for defining structured workflows that LLM agents execute directly, without a system prompt.

APE files are self-contained. The document declares who does what, what tools to use, when to stop and wait, and what to do on success or failure. Hand it to an agent; it runs.

## Why

LLM workflows today live in system prompts, scattered markdown files, or code that's opaque to the agents executing it. APE makes the workflow a first-class artifact:

- **Portable.** An `.ape` file works with any agent that can read XML and call tools.
- **Inspectable.** The workflow is the document. No hidden state, no prompt engineering tricks.
- **Enforceable.** Gates, prerequisites, and failure handlers are structural, not suggestions.
- **Authorable.** Tags say what they mean. Two primitives: things you *do* (`<command>`) and things you *need* (`<resource>`). Everything else is flow control and metadata.

## Project Structure

```
spec/
  ape-spec.md        Full language specification
  ape-llms.md        LLM execution contract (include when an agent runs a workflow)
  ape-authoring.md   Guide for writing APE workflows
schema/
  ape.xsd            XML Schema (validates shape; semantics are validator-enforced)
```

## Using APE

**To write a workflow:** Read [`spec/ape-authoring.md`](spec/ape-authoring.md). Start with commands and resources, add steps and gates, then layer in constraints and templates.

**To run a workflow:** Include [`spec/ape-llms.md`](spec/ape-llms.md) alongside the `.ape` file when handing it to an LLM agent. The contract tells the agent how to interpret and execute the document.

**To validate a workflow:** Use `schema/ape.xsd` for structural validation. Semantic validation (reference resolution, scope rules, flow-control constraints) requires a validator — see section 23 of the spec.

## Spec Version

**0.2.2-draft** — APE is under active development. The schema namespace is pinned to the major version (`https://ape-lang.dev/schema/2`); minor versions are expected to be broadly compatible.

## Benchmarks

The benchmark suite tests how workflow instructions in different formats perform against real apps with realistic prompts. Each case runs in an isolated workspace with a scrubbed environment.

### Running

```bash
python3 benchmark/run_benchmark.py              # run all cases (4 parallel workers)
python3 benchmark/run_benchmark.py --dry-run     # list cases without executing
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--model` | `claude-opus-4-6` | Model to use |
| `--workers` | `4` | Parallel workers (`1` for sequential) |
| `--delay` | `0` | Rate-limit delay between cases (seconds) |
| `--timeout` | `15` | Per-case timeout (minutes) |
| `--max-turns` | unlimited | Max CLI turns per case |
| `--dry-run` | off | Show discovered cases without executing |
| `--legacy-output` | off | Write legacy JSON summaries |
| `--no-enrich-tokens` | on | Skip token/cost enrichment from session logs |
| `-v, --verbose` | on | Debug-level logging |

### Filtering

You can narrow the case matrix by combining dimension filters:

| Filter | Example | Description |
|--------|---------|-------------|
| `--app` | `--app claude-bot` | Filter by app/fixture name |
| `--workflow` | `--workflow centminmod` | Filter by workflow stem |
| `--format-filter` | `--format-filter plain-text` | Filter by workflow format |
| `--category` | `--category bugs` | Filter by prompt category |
| `--item` | `--item some-id` | Filter by app-config item ID |

```bash
# Run a single fixture
python3 benchmark/run_benchmark.py --app claude-bot

# Preview what matches before running
python3 benchmark/run_benchmark.py --app claude-bot --format-filter plain-text --dry-run
```

## License

TBD
