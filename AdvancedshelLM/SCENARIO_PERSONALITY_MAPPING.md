# Scenario Personality Mapping Explanation

## Purpose

This note explains how the generated personalities `worker_scenario1.yml` and `worker_scenario2.yml` were produced by combining:

1. a reference personality used as a style and structure template
2. a scenario JSON file used as a content blueprint

The key idea is simple:

- the reference personality keeps the shell behavior, tone, and section layout stable
- the scenario changes the host identity, filesystem, bait files, and deception behavior

So the generator is not supposed to copy the baseline literally. It is supposed to preserve the *form* of the prompt while replacing the *content* with scenario-specific details.

## How the transformation works

The transformation logic is stated directly in `codebase/LLM_Plugins/manager_agent.py`:

```text
Use (A) as your structural and stylistic template.
Use (B) as your content and character blueprint.
```

The same prompt also defines the field-level mapping:

```text
asset_id            -> hostname in the prompt rule + /etc/hostname file content
tactical_role       -> filesystem topology and secondary user/service account selection around admin
deception_objective -> density, richness, and cross-file interconnection of content
engagement_strategy -> specific files created and what they contain
embedded_tokens     -> concrete credentials/tokens placed in the specified locations
interaction_hints   -> new bullet points added to HARD RULES or OUTPUT STYLE
```

The actual generation step in `codebase/initialize.py` is:

```python
result = manager.generate_worker_persona_custom(reference_text, scenario_json)
result = _resolve_hostname(result)
```

This means:

- `reference_text` supplies the shell prompt style and section organization
- `scenario_json` supplies the new machine identity and deception content
- `_resolve_hostname` replaces the placeholder `{hostname}` with the real scenario hostname

## What is preserved from the reference personality

The current `baseline.yml` gives the stable shell-emulator frame.

For example, the baseline starts with:

```text
You are a POSIX shell (bash-like) emulator. Act exactly like an interactive Linux terminal.
```

Both generated personalities keep exactly the same first sentence:

```text
You are a POSIX shell (bash-like) emulator. Act exactly like an interactive Linux terminal.
```

The baseline also keeps the same main rule sections:

```text
HARD RULES (highest priority):
ENVIRONMENT CONSISTENCY:
OUTPUT STYLE (important):
```

Both generated personalities preserve that same rule-oriented structure and then extend it with:

```text
FILESYSTEM LISTING (JSON-lines):
```

This is a reasonable extension, because the scenario cannot be represented only through abstract rules. It also needs concrete files, users, and directories.

The continuation block is also preserved. In all three files, the ending instruction is effectively the same:

```text
Here the session stopped. What you see so far is the history of the conversation and you have to continue it, not repeat it.
```

So structurally, the generated personalities are not random new prompts. They are template-preserving transformations of the reference personality.

## Scenario 1: Mapping a vulnerable developer workstation

### Scenario input

`scenario1.json` defines the machine as:

```json
"asset_id": "hp_dev_server",
"deception_objective": "Maximize attacker time investment",
"tactical_role": "Vulnerable developer workstation"
```

It also asks for these engagement strategies:

```json
"engagement_strategy": [
  "Present multiple interesting files (.bash_history, config files)",
  "Include references to other systems",
  "Show signs of poor security practices"
]
```

And it embeds a token here:

```json
{
  "type": "credential",
  "location": ".bash_history",
  "target": "prod-db-01"
}
```

### How the mapping appears in the generated personality

#### 1. `asset_id` becomes the visible hostname

The scenario says:

```json
"asset_id": "hp_dev_server"
```

The generated prompt uses that value in the shell prompt rule:

```text
- Always end every response with the prompt line: "admin@hp_dev_server:{pwd}$" let the starting {pwd} be ~
```

The generated filesystem also writes the same hostname into `/etc/hostname`:

```json
{"p":"/etc/hostname","k":"f","mime":"text/plain","enc":"utf-8","sz":13,"ctime":"2025-12-01T08:00:00Z","mtime":"2025-12-01T08:00:00Z","x":"hp_dev_server"}
```

This is a correct mapping because the scenario hostname becomes both a behavioral identity and a filesystem artifact.

#### 2. `tactical_role` becomes a developer workstation filesystem

The scenario says:

```json
"tactical_role": "Vulnerable developer workstation"
```

The generated personality answers this with clearly developer-oriented artifacts:

```json
{"p":"/home/admin/.gitconfig","k":"f","mime":"text/plain","enc":"utf-8","sz":59,"ctime":"2025-07-01T10:12:00Z","mtime":"2025-07-01T10:12:00Z","x":"[user]\n    name = Admin User\n    email = admin@example.com"}
{"p":"/home/admin/.vimrc","k":"f","mime":"text/plain","enc":"utf-8","sz":21,"ctime":"2025-07-01T10:11:00Z","mtime":"2025-07-01T10:11:00Z","x":"set number\nsyntax on"}
{"p":"/home/admin/projects/app.py","k":"f","mime":"text/x-python","enc":"utf-8","sz":120,"ctime":"2025-08-01T09:10:00Z","mtime":"2025-08-01T09:10:00Z","x":"import psycopg2\n\nconn = psycopg2.connect(host='prod-db-01', user='admin', password='secret123')\nprint('Connected')"}
```

These are strong signs of a developer machine rather than a generic Linux host.

#### 3. `engagement_strategy` becomes concrete bait files

The scenario asks for:

```json
"Present multiple interesting files (.bash_history, config files)"
```

The generated personality creates exactly those kinds of files:

```json
{"p":"/home/admin/.bash_history","k":"f","mime":"text/plain","enc":"utf-8","sz":144,"ctime":"2025-07-01T10:00:00Z","mtime":"2025-07-01T10:00:00Z","x":"ssh user@prod-db-01\nmysql -h prod-db-01 -u admin -p secret123\ngit clone git@internal-repo:/project.git\ncat /etc/passwd\nvim /home/admin/notes.txt"}
{"p":"/home/admin/.ssh/config","k":"f","mime":"text/plain","enc":"utf-8","sz":125,"ctime":"2025-07-01T10:06:00Z","mtime":"2025-07-01T10:06:00Z","x":"Host prod-db-01\n    HostName prod-db-01.internal\n    User admin\n    IdentityFile ~/.ssh/id_rsa\n    StrictHostKeyChecking no"}
{"p":"/home/admin/notes.txt","k":"f","mime":"text/plain","enc":"utf-8","sz":126,"ctime":"2025-07-01T10:13:00Z","mtime":"2025-07-01T10:13:00Z","x":"TODO: Check logs on prod-db-01.\nReview staging server at staging-web-01.\nCredentials for backup: user backup, pass backup123."}
```

This is a good example of moving from a high-level instruction to concrete attacker-facing content.

#### 4. "Include references to other systems" becomes cross-linked infrastructure hints

The scenario explicitly asks for references to other systems.

The generated output includes repeated references such as:

```text
prod-db-01
staging-web-01
```

Those references appear across multiple files:

- `.bash_history`
- `.ssh/config`
- `notes.txt`
- `projects/app.py`
- `/etc/hosts`

For example:

```json
{"p":"/etc/hosts","k":"f","mime":"text/plain","enc":"utf-8","sz":75,"ctime":"2025-12-01T08:10:00Z","mtime":"2025-12-01T08:10:00Z","x":"127.0.0.1   localhost\n192.168.1.10 prod-db-01\n192.168.1.20 staging-web-01"}
```

This is important because the deception objective is not only to place one secret, but to build a believable network of clues.

#### 5. "Show signs of poor security practices" becomes weak operational hygiene

The scenario asks for poor security practices. That appears in several places:

```json
{"p":"/home/admin/.bash_history","k":"f","mime":"text/plain","enc":"utf-8","sz":144,"ctime":"2025-07-01T10:00:00Z","mtime":"2025-07-01T10:00:00Z","x":"ssh user@prod-db-01\nmysql -h prod-db-01 -u admin -p secret123\ngit clone git@internal-repo:/project.git\ncat /etc/passwd\nvim /home/admin/notes.txt"}
{"p":"/home/admin/notes.txt","k":"f","mime":"text/plain","enc":"utf-8","sz":126,"ctime":"2025-07-01T10:13:00Z","mtime":"2025-07-01T10:13:00Z","x":"TODO: Check logs on prod-db-01.\nReview staging server at staging-web-01.\nCredentials for backup: user backup, pass backup123."}
```

Passwords are exposed in shell history and notes, which is exactly the type of careless workstation behavior the scenario describes.

#### 6. `embedded_tokens` is realized in the requested location

The scenario says:

```json
{
  "type": "credential",
  "location": ".bash_history",
  "target": "prod-db-01"
}
```

The generated `.bash_history` includes:

```text
mysql -h prod-db-01 -u admin -p secret123
```

This is a correct direct mapping: the scenario asked for a credential in `.bash_history` tied to `prod-db-01`, and the generated file contains exactly that pattern.

#### 7. `interaction_hints` becomes new behavior rules

The scenario includes:

```json
"interaction_hints": [
  "Respond slowly to commands to waste time",
  "Include realistic but misleading error messages"
]
```

The generated prompt translates those into actionable shell-behavior instructions:

```text
- Introduce a slight artificial delay before each command response to waste attacker time.
- Occasionally add plausible but misleading details to error messages (e.g., suggest a missing library version) while remaining syntactically correct.
```

This is exactly the intended transformation: scenario hints become worker behavior rules.

### Scenario 1 conclusion

Scenario 1 is a strong example of successful mapping. The generated output preserves the baseline shell-prompt structure while turning the scenario into a believable developer workstation with:

- developer artifacts
- credentials in history
- references to other hosts
- poor security practices
- delay and misdirection behavior

## Scenario 2: Mapping a Kubernetes node with debugging leftovers

### Scenario input

`scenario2.json` defines the machine as:

```json
"asset_id": "llm_hp_gen_ssh_k8s_node_05",
"deception_objective": "Make attacker question what is real",
"tactical_role": "Kubernetes cluster node with debugging leftovers"
```

Its engagement strategy is:

```json
"engagement_strategy": [
  "Mix realistic and suspicious elements",
  "Provide contradictory information"
]
```

It also embeds a token here:

```json
{
  "type": "browser_cookies",
  "location": "/home/dev/.browser_cookies",
  "target": "session hijacking"
}
```

And it gives ambiguity-related interaction hints:

```json
"interaction_hints": [
  "Sometimes act realistic, sometimes suspicious",
  "Create ambiguity"
]
```

### How the mapping appears in the generated personality

#### 1. `asset_id` again becomes the host identity

The scenario says:

```json
"asset_id": "llm_hp_gen_ssh_k8s_node_05"
```

The generated prompt turns it into the visible shell identity:

```text
- Always end every response with the prompt line: "admin@llm_hp_gen_ssh_k8s_node_05:{pwd}$" let the starting {pwd} be ~
```

And the same value appears in the filesystem:

```json
{"p":"/etc/hostname","k":"f","mime":"text/plain","enc":"utf-8","sz":27,"ctime":"2025-12-01T10:00:00Z","mtime":"2026-01-15T12:30:00Z","x":"llm_hp_gen_ssh_k8s_node_05\n"}
```

So the host identity is consistently mapped into both prompt behavior and on-disk state.

#### 2. `tactical_role` becomes Kubernetes-specific filesystem content

The scenario says:

```json
"tactical_role": "Kubernetes cluster node with debugging leftovers"
```

The generated filesystem implements that with Kubernetes and node-debugging artifacts:

```json
{"p":"/etc/kubernetes","k":"d","mime":"inode/directory","sz":4096,"ctime":"2025-12-01T10:00:00Z","mtime":"2026-01-15T12:30:00Z"}
{"p":"/etc/kubernetes/kubelet.conf","k":"f","mime":"text/plain","enc":"utf-8","sz":100,"ctime":"2025-12-01T10:00:00Z","mtime":"2026-01-15T12:30:00Z","x":"kind: KubeletConfiguration\napiVersion: kubelet.config.k8s.io/v1beta1\ncgroupDriver: systemd\n"}
{"p":"/var/lib/kubelet","k":"d","mime":"inode/directory","sz":4096,"ctime":"2025-12-01T10:00:00Z","mtime":"2026-01-15T12:30:00Z"}
{"p":"/var/log/containers/kube-apiserver.log","k":"f","mime":"text/plain","enc":"utf-8","sz":150,"ctime":"2025-12-01T10:00:00Z","mtime":"2026-01-15T12:30:00Z","x":"I0201 12:00:00.123456 1 server.go:123] Starting server on 0.0.0.0:6443\nW0201 12:05:00.654321 1 auth.go:45] Failed login attempt from 10.0.0.5\n"}
```

This is a correct thematic conversion: the abstract role "Kubernetes node" becomes specific directories and logs that an attacker would expect to find.

#### 3. "debugging leftovers" becomes `/opt/debug` content

The phrase "debugging leftovers" is implemented directly through:

```json
{"p":"/opt/debug","k":"d","mime":"inode/directory","sz":4096,"ctime":"2025-12-01T10:00:00Z","mtime":"2026-01-15T12:30:00Z"}
{"p":"/opt/debug/trace.log","k":"f","mime":"text/plain","enc":"utf-8","sz":77,"ctime":"2025-12-01T10:00:00Z","mtime":"2026-01-15T12:30:00Z","x":"DEBUG: pod xyz started at 2026-02-20T10:00:00Z\nERROR: unknown token detected\n"}
{"p":"/opt/debug/cleanup.sh","k":"f","mime":"text/x-shellscript","enc":"utf-8","sz":26,"ctime":"2025-12-01T10:00:00Z","mtime":"2026-01-15T12:30:00Z","x":"#!/bin/bash\nrm -rf /tmp/*\n"}
```

This is a strong mapping because it turns one short scenario phrase into concrete, inspectable shell artifacts.

#### 4. `engagement_strategy` becomes ambiguity and contradiction

The scenario asks for:

```json
"Mix realistic and suspicious elements"
"Provide contradictory information"
```

The generated prompt converts those into behavior rules:

```text
- Occasionally return output that seems realistic but may contain subtle inconsistencies to create doubt.
- Mix realistic system files with suspicious or contradictory content.
- If a command accesses a file related to Kubernetes debugging (e.g., /opt/debug/trace.log), provide plausible but ambiguous content.
```

The generated filesystem also supports this ambiguity. For example:

```json
{"p":"/etc/hosts","k":"f","mime":"text/plain","enc":"utf-8","sz":39,"ctime":"2025-12-01T10:00:00Z","mtime":"2026-01-15T12:30:00Z","x":"127.0.0.1 localhost\n127.0.0.1 fakehost\n"}
{"p":"/etc/shadow","k":"f","mime":"text/plain","enc":"utf-8","sz":0,"ctime":"2025-12-01T10:00:00Z","mtime":"2026-01-15T12:30:00Z","x":""}
```

These are not just normal system files. They are slightly suspicious and help support the scenario goal of making the attacker question what is real.

#### 5. `embedded_tokens` is placed in the requested file

The scenario specifies:

```json
{
  "type": "browser_cookies",
  "location": "/home/dev/.browser_cookies",
  "target": "session hijacking"
}
```

The generated personality creates exactly that file:

```json
{"p":"/home/dev/.browser_cookies","k":"f","mime":"text/plain","enc":"utf-8","sz":23,"ctime":"2025-12-01T10:00:00Z","mtime":"2026-01-15T12:30:00Z","x":"sessionid=abc123def456\n"}
```

This is a direct and correct mapping from scenario blueprint to filesystem artifact.

#### 6. `interaction_hints` becomes operational behavior

The scenario says:

```json
"interaction_hints": [
  "Sometimes act realistic, sometimes suspicious",
  "Create ambiguity"
]
```

The generated prompt translates that into explicit worker behavior:

```text
- Occasionally return output that seems realistic but may contain subtle inconsistencies to create doubt.
- Mix realistic system files with suspicious or contradictory content.
```

Again, the high-level scenario language is turned into actionable prompt rules.

### Scenario 2 conclusion

Scenario 2 is also a successful mapping. The generated personality preserves the baseline shell structure while replacing the host content with:

- Kubernetes directories and logs
- debugging leftovers
- an ambiguity-oriented interaction style
- suspicious system artifacts
- an embedded browser-cookie token

## Overall conclusion

The two generated personalities demonstrate the intended conversion process:

1. keep the shell-emulation structure and tone from the reference personality
2. inject a new machine identity from `asset_id`
3. translate the scenario role into a matching filesystem topology
4. turn engagement strategy into visible bait files
5. place embedded tokens into concrete locations
6. convert interaction hints into new worker rules

In other words, the mapping is successful because the scenario is not left as abstract text. It is concretized into:

- prompt rules
- user accounts
- hostnames
- files
- directories
- logs
- credentials
- misleading but plausible artifacts

That is exactly what a scenario-driven honeypot personality generator is supposed to do.

## Short methodological note

The mapping is strong overall, but it is not a literal copy of `baseline.yml`. It is better described as a *structure-preserving extension* of the baseline. The generated prompts keep the reference shell style and section layout, but they add a `FILESYSTEM LISTING` section so that the scenario can be expressed as a concrete interactive environment.
