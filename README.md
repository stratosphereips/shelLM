# shelLM

The `shelLM` honeypot suite creates interactive, dynamic, and realistic honeypots through the use of Large Language Models (LLMs). The `shelLM` tool was created from a research project to show the effectiveness of dynamic fake file systems and command responses to keep attackers trapped longer, thus increasing the intelligence collected.

## Features

`shelLM` was developed in Python and currently uses Open AI GPT models. Among its key features are:

1. The content from a previous session is carried over to a new session to ensure consistency.
2. Uses a combination of techniques for prompt engineering, including Chain-of-thought.
3. Uses prompts with precise instructions to address common LLMs problems.
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
~$
~$ # Run shelLM
~$ python3 LinuxSSHbot.py 
```

## Usage

TBD

## FAQ

# About

This tool was developed at the Stratosphere Laboratory at the Czech Technical University in Prague.
