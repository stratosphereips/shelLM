# shelLM

The `shelLM` honeypot suite creates interactive, dynamic, and realistic honeypots through the use of Large Language Models (LLMs). The `shelLM` tool was created from a research project to show the effectiveness of dynamic fake file systems and command responses to keep attackers trapped longer, thus increasing the intelligence collected.

The extension of shelLM to a larger deception framework we call VelLMes can be found here: https://github.com/stratosphereips/VelLMes-AI-Honeypot/tree/main

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

## FAQ

**What services does shelLM uses?**

This version of shelLM can simulate an SSH honeypot.

**Are you planning on supporting other services?**

Yes. This is part of ongoing research focused on more services.

**Is this just a wrapper for Open AI?**

No. The core of the tool are the Prompts, that have been engineered specially to guarantee a correct behavior. Also shelLM provides other features like session management, error handling, log storage, and other key features needed in honeypots.

# About

This tool was developed at the Stratosphere Laboratory at the Czech Technical University in Prague.
