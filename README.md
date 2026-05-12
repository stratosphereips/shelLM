# shelLM

The `shelLM` honeypot suite creates interactive, dynamic, and realistic honeypots through the use of Large Language Models (LLMs). The `shelLM` tool was created from a research project to show the effectiveness of dynamic fake file systems and command responses to keep attackers trapped longer, thus increasing the intelligence collected.

The extension of shelLM to a larger deception framework we call VelLMes can be found here: https://github.com/stratosphereips/VelLMes-AI-Honeypot/tree/main

## Update May 2026

The shelLM was extended and improved. Updates to the shelLM are in the shelLMv2 directory. For those wanting to use older version it is still available here as described below.
\\
New version of shelLM was created. AdvancedshelLM and is available in its respective directory in this repository. Readme for AdvancedshelLM is in the AdvancedshelLM directory.

## Features

`shelLM` was developed in Python and currently uses Open AI GPT models. Among its key features are:

1. The content from a previous session is carried over to a new session to ensure consistency.
2. It uses a combination of techniques for prompt engineering, including chain-of-thought.
3. Uses prompts with precise instructions to address common LLM problems.
4. More creative file and directory names
5. Allows users to "move" through folders
6. Response is correct also for non-commands.
7. sudo command not allowed

## Installation

The installation steps are as follows:

```bash
~$ # Install requirements
~$ pip install -r requirements.txt
~$
~$ # Create env file
~$ cp env_TEMPLATE .env
~$ # Edit env file to add OPEN AI API KEY
~$ vim .env
```

## Usage

Run `shelLM` with the following command:
```
~$ python3 LinuxSSHbot.py 
```
![image](https://github.com/stratosphereips/shelLM/assets/2458879/021bcd0d-93ae-49aa-b3a3-dea345dffc7c)

# Local Setup Guide for ShelLM v2

Prerequisites and Setup

Before running the application, you must ensure you have the necessary dependencies and configuration files.

1. Prerequisites

    Python 3 installed on your system.

    Required Python packages installed (pip install -r requirements.txt).

2. Configuration (.env File)

The application requires several API keys and server addresses to connect with the necessary language models. These must be defined in a .env file located in the root directory of your project.
Variable	Description	Example Value
```
OPENAI_API_KEY=
OLLAMA_BASE_URL="http://localhost:11434" // or anywhere ollama is hosted
EINFRA_API_KEY=
SSH_BOT_PATH=/path/to/project/Linux_Terminal_Chatbot_MS/Honeypots - separate/SSH/LinuxSSHbot.py  //change this based on the location of where it is on your device. It should point to LinuxSSHbot.py
PUBLICAI_API_KEY=
PUBLICAI_USER_AGENT=ShellM/1.0
```

Local Execution

To run the shelLM SSH honeypot locally, follow these steps:

1. Navigate to the Script Folder

The main execution file, LinuxSSHbot.py, is located in the Honeypots - separate directory.
```
cd "Honeypots - separate/SSH"
```
2. Run the Code

Execute the script using the Python 3 interpreter:
```
python3 LinuxSSHbot.py  --provider einfra --model gpt-oss-120b   --personality Eman_v1   --trace --cleaned
```

## Command-line Parameter Descriptions

Below is a description of each command-line parameter used in the example:

### `--provider`

Selects which backend to use for the LLM.

Typical values:

- `einfra` – uses the E-INFRA/CESNET endpoint (requires `EINFRA_API_KEY`).
- `openai` – uses the OpenAI API (requires `OPENAI_API_KEY`).
- `ollama` – uses a local/remote Ollama instance (uses `OLLAMA_BASE_URL`).
- `publicai` – uses the PublicAI endpoint (requires `PUBLICAI_API_KEY` and `PUBLICAI_USER_AGENT`).

---

### `--model`

Model identifier for the chosen provider.

Examples:

- `gpt-oss-120b` (E-INFRA model)
- `llama3.1:8b` (Ollama model)
- `qwen3:1.7b` (Ollama or other)
- `ft:gpt-3.5-turbo-1106:stratosphere-laboratory::8KS2seKA` (OpenAI fine-tuned model)

Must match the models supported by the selected `--provider`.

---

### `--personality`

Name of a personality file stored in:

```text
Linux_Terminal_Chatbot_MS/Honeypots - separate/SSH/personalities
```

Examples: `Eman_v1`, `Eman_v2`, `Muris_v1`, `Muris_v2`, `Muris_v3` etc.  
Controls the initial system prompt and behavior style of the honeypot.

---

### `--trace`

When present, enables detailed tracing.

- Logs will contain a `trace_log` with information about requests/responses between ShelLM and the model provider (useful for debugging and analysis).

---

### `--cleaned`

When present, cleans/removes previous logs before starting a new run.

The application will start, initializing the LLM-based SSH honeypot service according to the configuration you provided in the root .env file.

## FAQ

**What services does shelLM uses?**

This version of shelLM can simulate an SSH honeypot.

**Are you planning on supporting other services?**

Yes. This is part of ongoing research focused on more services.

**Is this just a wrapper for Open AI?**

No. The core of the tool are the Prompts, that have been engineered specially to guarantee a correct behavior. Also shelLM provides other features like session management, error handling, log storage, and other key features needed in honeypots.

# About

This tool was developed at the Stratosphere Laboratory at the Czech Technical University in Prague.
