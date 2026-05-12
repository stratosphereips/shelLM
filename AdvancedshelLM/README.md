# AdvancedShellm

AdvancedShellm is an SSH honeypot where an LLM pretends to be a Linux machine.

In simple terms:

- The `Worker` model plays the fake shell.
- The optional `Manager` model checks the Worker's draft before the attacker sees it.
- The fake filesystem lives inside the Worker prompt as JSON lines.
- No real shell commands are executed on the host machine.

## What The Project Does

Think of the system like a stage play:

- `worker.yml` is the script and the props.
- `advancedShellm.py` is the stage manager running the session.
- The Worker LLM says what the "Linux machine" should print.
- The Manager LLM can stop bad answers, patch the fake filesystem, and ask for a retry.
- The logs record the whole performance.

This gives you a stateful shell simulation without giving the attacker a real shell.

## Current Status

What works in the current codebase:

- Live SSH-style shell simulation through [`codebase/advancedShellm.py`](/home/lesley/Thesis/AdvancedShellm/codebase/advancedShellm.py)
- Persona generation from a reference personality plus a scenario JSON through [`codebase/initialize.py`](/home/lesley/Thesis/AdvancedShellm/codebase/initialize.py)
- Manager supervision of normal command output
- Manager supervision of the initial startup banner/prompt
- Filesystem patching during both `APPROVED` and `REPROMPT` reviews
- Permission-aware handling of protected files like `/etc/shadow`
- A live integration test runner in [`codebase/tests/run_tests.py`](/home/lesley/Thesis/AdvancedShellm/codebase/tests/run_tests.py)

Important caveats:

- The supported runtime path today is `--personality ...`.
- `advancedShellm.py --manager_config ...` is currently not reliable in this checkout. The runtime calls `ManagerAgent.generate_worker_persona(...)`, but the manager module only defines `generate_worker_persona_custom(...)`.
- `openai` and `einfra` are the only providers with actual client implementations in this repo.
- The CLI still lists `ollama` and `localqlora`, but [`codebase/LLM_Plugins/api_clients/ollama_client.py`](/home/lesley/Thesis/AdvancedShellm/codebase/LLM_Plugins/api_clients/ollama_client.py) is empty and the `local_hfqlora_client.py` file is not present.
- Token counting uses `tiktoken` if it is installed. It is optional and is not listed in `requirements.txt`.
- Every run creates a fresh timestamped log directory, so there is no true built-in "resume the same session later" flow right now even though personalities still contain a `continuation` block.

## Repository Layout

```text
AdvancedShellm/
├── README.md
├── requirements.txt
├── .env.template
├── SCENARIO_PERSONALITY_MAPPING.md
└── codebase/
    ├── advancedShellm.py
    ├── initialize.py
    ├── logs/
    ├── logs_init/
    ├── model_listers/
    │   ├── list_openai_models.py
    │   └── list_einfra_models.py
    ├── tests/
    │   ├── run_tests.py
    │   └── ssh_tests_definitions.py
    └── LLM_Plugins/
        ├── manager_agent.py
        ├── log_manager.py
        ├── api_clients/
        │   ├── openai_client.py
        │   ├── einfra_client.py
        │   └── ollama_client.py
        ├── personalities/
        │   ├── baseline.yml
        │   ├── kodalabs.yml
        │   ├── worker.yml
        │   ├── worker_backup.yml
        │   ├── worker_tests.yml
        │   ├── worker_tests_backup.yml
        │   ├── worker_scenario1.yml
        │   └── worker_scenario2.yml
        └── scenarios/
            ├── scenario1.json
            └── scenario2.json
```

## The Codebase Explained Simply

### `advancedShellm.py`

This is the main runtime.

It does these jobs:

1. Loads a personality YAML file.
2. Builds the first `system` message for the Worker.
3. Seeds local readline history from the fake `.bash_history` if one exists.
4. Accepts user input in a loop.
5. Sends the conversation to the Worker LLM.
6. Optionally asks the Manager to review the draft first.
7. Applies fake filesystem changes back into the personality.
8. Prints the final shell output.
9. Writes logs.

It also contains the small runtime tricks that make the shell feel more real:

- prompt extraction and prompt repair
- stripping leaked chain-of-thought text
- special handling for `ping`
- special handling for REPLs like `python3`
- `Ctrl+C` and `Ctrl+D` behavior

### `manager_agent.py`

This is the Manager brain.

It has two main responsibilities:

1. Generate a Worker personality from a reference prompt plus a scenario JSON
2. Review Worker drafts and either approve them or reprompt them

The review logic is prompt-based. The Manager receives:

- the active Worker persona
- recent session history
- the current user command, or startup context for the first output
- the Worker's draft
- current real UTC time for time-sensitive commands

The Manager can answer in three practical ways:

- `APPROVED`
- `APPROVED` plus `ADD:` / `REMOVE:` filesystem patch lines
- `REPROMPT: ...` plus optional `ADD:` / `REMOVE:` patch lines

### `log_manager.py`

This file creates and writes the session logs.

There are three logger classes:

- `BaseLogManager`: common log file setup and helpers
- `ShelLMLogManager`: normal honeypot runtime logs
- `InitializationLogManager`: logs for `initialize.py`

The runtime logger creates a new folder under `codebase/logs/<timestamp>/` for every process start.

### `initialize.py`

This is the persona factory.

It takes:

- a reference personality, such as `baseline.yml`
- a scenario JSON, such as `scenario1.json`

Then it:

1. extracts only the prompt text from the reference YAML
2. asks the Manager to rewrite that prompt body for the new scenario
3. injects the `SUPERVISOR INSTRUCTION` rule if it is missing
4. replaces `{hostname}` with the scenario `asset_id`
5. strips leaked `asset_id` meta-lines if the model emitted them
6. writes the result back as clean YAML block scalars

### `api_clients/`

These are thin wrappers around provider HTTP APIs.

- [`openai_client.py`](/home/lesley/Thesis/AdvancedShellm/codebase/LLM_Plugins/api_clients/openai_client.py): implemented
- [`einfra_client.py`](/home/lesley/Thesis/AdvancedShellm/codebase/LLM_Plugins/api_clients/einfra_client.py): implemented
- [`ollama_client.py`](/home/lesley/Thesis/AdvancedShellm/codebase/LLM_Plugins/api_clients/ollama_client.py): empty in this checkout

The OpenAI client has one extra detail:

- normal chat models go through `v1/chat/completions`
- codex-style models go through `v1/responses`

### `personalities/`

These YAML files define fake hosts.

The most important one is [`codebase/LLM_Plugins/personalities/worker.yml`](/home/lesley/Thesis/AdvancedShellm/codebase/LLM_Plugins/personalities/worker.yml), which is the default active machine for many runs.

### `scenarios/`

These JSON files are high-level host blueprints for persona generation.

They describe things like:

- hostname (`asset_id`)
- tactical role
- deception objective
- bait strategy
- embedded credentials or tokens

### `tests/`

The tests are live process tests, not pure unit tests.

[`codebase/tests/run_tests.py`](/home/lesley/Thesis/AdvancedShellm/codebase/tests/run_tests.py) launches `advancedShellm.py` as a child process, sends commands through stdin, waits for prompts, and checks the terminal output against assertions from [`codebase/tests/ssh_tests_definitions.py`](/home/lesley/Thesis/AdvancedShellm/codebase/tests/ssh_tests_definitions.py).

## Setup

### Requirements

- Python 3.10 or newer
- `requests`
- `PyYAML`
- `python-dotenv`

Install them with:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Optional:

- `tiktoken` if you want token-reset estimation to work

### Environment File

Create the root `.env` file from the template:

```bash
cp .env.template .env
```

Then fill in the keys you need:

```dotenv
OPENAI_API_KEY=
EINFRA_API_KEY=
```

Notes:

- The main runtime and `initialize.py` both look for the root `.env` at `AdvancedShellm/.env`.
- There is also a `codebase/.env` file in the repo, but the main runtime does not use it.
- If you later add a real Ollama client, you would also need something like `OLLAMA_BASE_URL=...`.

## Running The Honeypot

Recommended basic command:

```bash
python3 codebase/advancedShellm.py \
  --provider openai \
  --model gpt-4o \
  --personality worker
```

Recommended supervised command:

```bash
python3 codebase/advancedShellm.py \
  --provider openai \
  --model gpt-4o \
  --personality worker \
  --supervise \
  --manager_provider openai \
  --manager_model gpt-4o-mini \
  --trace
```

If you want to wipe old runtime logs before starting:

```bash
python3 codebase/advancedShellm.py \
  --provider openai \
  --model gpt-4o \
  --personality worker \
  --cleaned
```

### Runtime CLI Flags

| Flag | Required | Meaning |
| --- | --- | --- |
| `--provider` | yes | Worker provider. Choices in code: `openai`, `einfra`, `ollama`, `localqlora`. In practice, only `openai` and `einfra` are implemented in this checkout. |
| `--model` | yes | Worker model name. |
| `--personality` | usually yes | Personality name without `.yml`, loaded from `codebase/LLM_Plugins/personalities/`. |
| `--manager_provider` | no | Manager provider for live supervision. Defaults to `--provider`. |
| `--manager_model` | no | Manager model for live supervision. Defaults to `--model`. |
| `--supervise` | no | Turns on the Manager review loop before output is shown to the attacker. |
| `--trace` | no | Writes full HTTP request/response traces for implemented providers. |
| `--cleaned` | no | Deletes everything inside `codebase/logs/` before the new run starts. |
| `--manager_config` | no | Intended to generate a persona inline from a scenario JSON, but this path is currently not reliable in this checkout. Use `initialize.py` instead. |

## What Happens During A Session

### Startup

At startup the runtime:

1. loads the personality prompt
2. creates a fresh log directory
3. seeds local readline history from the fake `.bash_history` file if present
4. asks the Worker for the first visible output
5. if `--supervise` is enabled, asks the Manager to review that first output too

### For Each User Command

For every later command, the runtime roughly does this:

```text
User types command
    -> append user message
    -> refresh system prompt from worker.yml
    -> ask Worker for a draft
    -> if supervision is on, ask Manager to review it
    -> apply any filesystem ADD/REMOVE patch lines
    -> maybe retry the Worker up to 3 times
    -> print final output
    -> log everything
```

### Important Runtime Details

- The system prompt is re-read from `worker.yml` before every Worker call.
- That means manual edits to `worker.yml` take effect on the next turn.
- Manager filesystem patches are also persisted back into `worker.yml`.
- `ping` output is streamed line by line for realism.
- If the user enters an interactive sub-program like `python3`, the runtime tries to stay inside that REPL until exit.

## Supervision Loop

When `--supervise` is enabled, the Manager acts like a strict reviewer.

It checks for things like:

- command echoing
- wrong prompts
- shell logic mistakes
- wrong `ls -a` / `ls -la` visibility
- wrong current time
- broken REPL behavior
- leaked internal reasoning text
- fake missing standard Linux files
- permission mistakes for protected files

### Startup Review

The first banner/prompt is now supervised too.

This matters because a bad first line is very visible to an attacker. The Manager can reject that initial output and ask the Worker to regenerate it before it is printed.

### Lazy Filesystem Materialization

One important newer behavior is lazy materialization of standard system files.

Example:

- the attacker asks for `/etc/shadow`
- the Worker says it does not exist
- the Manager decides that on a normal Ubuntu-like host it should exist
- the Manager adds it to the fake filesystem and reprompts the Worker
- the Worker retries from the updated state

The Manager is also instructed to respect normal Unix permissions, so a non-root `admin` user should get `Permission denied` for protected files like `/etc/shadow`.

For lazily added system files, the Manager is supposed to use plausible past timestamps instead of pretending the file was just created now.

## Filesystem Model

The fake filesystem lives inside the Worker prompt under:

```text
FILESYSTEM LISTING (JSON-lines):
```

Each line is one JSON object.

### Common Fields

Minimum metadata:

- `p`: absolute path
- `k`: kind, usually `d` for directory or `f` for file
- `mime`
- `sz`
- `ctime`
- `mtime`

Common content fields:

- `x`: text file contents
- `blob`: binary placeholder

Optional ownership and permission fields:

- `uid`
- `gid`
- `uname`
- `gname`
- `mode`

### How Patching Works

[`_apply_fs_patch()` in `advancedShellm.py`](/home/lesley/Thesis/AdvancedShellm/codebase/advancedShellm.py) does the runtime patching.

Current behavior:

- `REMOVE:` deletes entries by matching path
- `ADD:` upserts by path inside the main `FILESYSTEM LISTING`
- if an `ADD` entry already includes `ctime` and `mtime`, those timestamps are preserved
- missing timestamps are filled with the real current UTC time
- the updated prompt is immediately written back to the personality YAML

So the prompt file itself becomes the current source of truth.

### `.bash_history` vs `command_history.txt`

These are different things:

- `command_history.txt`: local readline history for your own terminal session
- fake `.bash_history` inside the personality: what the attacker sees inside the simulated machine

What the code does today:

- on startup, local readline history is seeded from the fake `.bash_history` if present
- on session close or crash, the session's user commands are appended back into the fake `.bash_history` entry in the personality prompt

## Personality Files

The usual structure is:

```yaml
personality:
  prompt: |
    ... rules ...
    FILESYSTEM LISTING (JSON-lines):
    {"p":"/","k":"d",...}
    {"p":"/etc/passwd","k":"f",...}

  continuation: |
    ... text for resumed sessions ...
```

### Important Personality Files

- [`codebase/LLM_Plugins/personalities/worker.yml`](/home/lesley/Thesis/AdvancedShellm/codebase/LLM_Plugins/personalities/worker.yml): active worker file used in many runs
- [`codebase/LLM_Plugins/personalities/worker_backup.yml`](/home/lesley/Thesis/AdvancedShellm/codebase/LLM_Plugins/personalities/worker_backup.yml): reset copy for tests or manual restores
- [`codebase/LLM_Plugins/personalities/worker_tests.yml`](/home/lesley/Thesis/AdvancedShellm/codebase/LLM_Plugins/personalities/worker_tests.yml): test-oriented worker
- [`codebase/LLM_Plugins/personalities/baseline.yml`](/home/lesley/Thesis/AdvancedShellm/codebase/LLM_Plugins/personalities/baseline.yml): general template/reference
- [`codebase/LLM_Plugins/personalities/kodalabs.yml`](/home/lesley/Thesis/AdvancedShellm/codebase/LLM_Plugins/personalities/kodalabs.yml): another reference personality

Practical note:

- supervised runs can mutate the active personality file because filesystem patches are persisted
- this is why the test harness restores personalities from backup files before runs

## Generating A New Personality

Use [`codebase/initialize.py`](/home/lesley/Thesis/AdvancedShellm/codebase/initialize.py), not the runtime `--manager_config` shortcut.

Example:

```bash
python3 codebase/initialize.py \
  baseline \
  scenario1 \
  --provider openai \
  --model gpt-4o \
  --output worker_generated
```

What the arguments mean:

- first positional argument: reference personality name, with or without `.yml`
- second positional argument: scenario name, with or without `.json`
- `--output`: output name or full output path

Output behavior:

- if `--output` is omitted, the file is written to `codebase/LLM_Plugins/personalities/worker.yml`
- if `--output` is a plain name like `worker_generated`, it becomes `codebase/LLM_Plugins/personalities/worker_generated.yml`
- if `--output` is a full or relative path with separators, that path is used directly

### `initialize.py` CLI Flags

| Flag | Required | Meaning |
| --- | --- | --- |
| `reference_personality` | yes | Reference YAML name such as `baseline` or `baseline.yml` |
| `scenario_blueprint` | yes | Scenario JSON name such as `scenario1` or `scenario1.json` |
| `--provider` | no | Manager provider. Defaults to `openai`. |
| `--model` | no | Manager model. Defaults to `gpt-4o`. |
| `--output` | no | Output file path or output name. Defaults to `worker.yml`. |

Notes:

- `initialize.py` currently turns HTTP tracing on for its initialization logger unconditionally.
- It writes its logs to `codebase/logs_init/`.

## Logs

### Runtime Logs

Every run gets a fresh folder:

```text
codebase/logs/YYYY-MM-DD_HH-MM-SS/
```

Common runtime files:

- `history.txt`: plain conversation log
- `history_ts.txt`: same thing with timestamps
- `command_history.txt`: readline history file
- `trace_log.txt`: Worker HTTP trace when `--trace` is enabled
- `manager_trace_log.txt`: Manager HTTP trace when `--trace` is enabled

Files that appear only in some sessions:

- `internal_monologue.txt`: Manager review attempts and applied FS patches
- `fs_history.txt`: compact list of `ADD:` / `REMOVE:` patch activity
- `manager_trace.txt`: higher-level manager actions, mainly persona-generation related
- `error.log`: per-session crash log

One more crash file exists outside session folders:

- `codebase/logs/error.log`: top-level last-resort crash log

Small but important detail:

- `trace_log.txt` and `manager_trace_log.txt` are created with a header for each session even when `--trace` is off
- the detailed HTTP request and response dumps are only written when `--trace` is enabled

### Initialization Logs

`initialize.py` writes into:

```text
codebase/logs_init/
```

Files there:

- `manager_init_trace.txt`
- `init_trace.txt`
- `generated_personality_backup.yml`

## Tests

Run the live test harness like this:

```bash
python3 codebase/tests/run_tests.py \
  --provider openai \
  --model gpt-4o \
  --personality worker \
  --mode per-test
```

Useful flags:

- `--mode per-test`: default, starts a fresh session per test
- `--mode single`: runs all tests in one shared session
- `--supervise`: run the honeypot with Manager supervision enabled
- `--manager_provider` and `--manager_model`: choose a separate manager
- `--trace`: pass tracing through to the child honeypot
- `--timeout`: seconds to wait per command

How the test runner keeps state clean:

- it restores `<personality>_backup.yml` to `<personality>.yml` before test runs
- in `per-test` mode it does that before every individual test

## Model Listing Scripts

There are two small helper scripts:

- [`codebase/model_listers/list_openai_models.py`](/home/lesley/Thesis/AdvancedShellm/codebase/model_listers/list_openai_models.py)
- [`codebase/model_listers/list_einfra_models.py`](/home/lesley/Thesis/AdvancedShellm/codebase/model_listers/list_einfra_models.py)

Examples:

```bash
python3 codebase/model_listers/list_openai_models.py
python3 codebase/model_listers/list_einfra_models.py
```

## Provider Summary

### OpenAI

Status: implemented

Used for:

- Worker runtime
- Manager supervision
- Personality generation

Special behavior:

- codex-like model names are routed to the Responses API automatically

### E-INFRA

Status: implemented

Used for:

- Worker runtime
- Manager supervision
- Personality generation

### Ollama

Status: not usable in this checkout

Reason:

- the CLI mentions it, but [`codebase/LLM_Plugins/api_clients/ollama_client.py`](/home/lesley/Thesis/AdvancedShellm/codebase/LLM_Plugins/api_clients/ollama_client.py) currently has no implementation

### LocalQLoRA

Status: not usable in this checkout

Reason:

- the CLI mentions it, but the expected `local_hfqlora_client.py` client file is not present

## Short Mental Model

If you only remember four things, remember these:

1. The "machine" is mostly just a big prompt plus a fake filesystem listing.
2. The Worker writes the shell output.
3. The Manager can reject bad output and patch the fake filesystem.
4. The YAML personality file is not static during supervised runs; it can be updated as the session evolves.
