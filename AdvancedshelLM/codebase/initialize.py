#!/usr/bin/env python3
import argparse
import os
import sys
import json
import yaml
from dotenv import dotenv_values, load_dotenv

# Add codebase to path to ensure imports work
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from LLM_Plugins.manager_agent import ManagerAgent
from LLM_Plugins.log_manager import LogManager, InitializationLogManager

# Client imports
# We import these inside main or try block to handle missing deps if necessary, 
# but for this script we assume env is set up.
from LLM_Plugins.api_clients.openai_client import OpenAIClient
from LLM_Plugins.api_clients.einfra_client import EinfraClient

def extract_prompt_from_yaml(filepath):
    """
    Extracts the 'prompt' string from a personality YAML file.
    If parsing fails or structure doesn't match, returns the raw content.
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        raw = f.read()
        
    try:
        data = yaml.safe_load(raw)
        if isinstance(data, dict):
            if "personality" in data and isinstance(data["personality"], dict):
                return data["personality"].get("prompt", raw).strip()
            elif "prompt" in data:
                return str(data["prompt"]).strip()
    except Exception:
        pass
    
    return raw.strip()



def main():
    parser = argparse.ArgumentParser(description="Initialize Worker Personality using Manager Agent")
    parser.add_argument("reference_personality", help="Name of the reference personality (e.g. baseline or baseline.yml)")
    parser.add_argument("scenario_blueprint", help="Name of the scenario (e.g. scenario1 or scenario1.json)")
    parser.add_argument("--provider", default="openai", help="LLM Provider for Manager")
    parser.add_argument("--model", default="gpt-4o", help="LLM Model for Manager")
    parser.add_argument("--output", help="Name or full path to save the generated personality file")
    
    args = parser.parse_args()
    
    # Resolve base directory (where initialize.py lives)
    base_dir = os.path.dirname(os.path.abspath(__file__))
    personalities_dir = os.path.join(base_dir, "LLM_Plugins", "personalities")
    scenarios_dir    = os.path.join(base_dir, "LLM_Plugins", "scenarios")
    
    # Resolve personality path: accept name with or without .yml extension
    personality_name = args.reference_personality
    if not personality_name.endswith(".yml"):
        personality_name += ".yml"
    args.reference_personality = os.path.join(personalities_dir, personality_name)
    
    # Resolve scenario path: accept name with or without .json extension
    scenario_name = args.scenario_blueprint
    if not scenario_name.endswith(".json"):
        scenario_name += ".json"
    args.scenario_blueprint = os.path.join(scenarios_dir, scenario_name)
    
    # Resolve output path: defaults to worker.yml; if a plain name is given, place it in personalities dir
    if not args.output:
        args.output = os.path.join(personalities_dir, "worker.yml")
    elif not os.sep in args.output and not args.output.startswith("."):
        if not args.output.endswith(".yml"):
            args.output += ".yml"
        args.output = os.path.join(personalities_dir, args.output)
        
    env_path = os.path.join(base_dir, "..", ".env")
    
    config = {}
    if os.path.exists(env_path):
        load_dotenv(env_path)
        config = dotenv_values(env_path)
    
    # Setup initialization logger
    logs_dir = os.path.join(base_dir, "logs_init")
    os.makedirs(logs_dir, exist_ok=True)
    logger = InitializationLogManager(log_dir=logs_dir, enable_trace=True, provider=args.provider, model=args.model)
    
    # Initialize Client
    client = None
    if args.provider.lower() == "einfra":
        api_key = config.get("EINFRA_API_KEY")
        if not api_key:
             # Fallback to os.environ if not in .env file directly but loaded via load_dotenv
             api_key = os.environ.get("EINFRA_API_KEY")
        client = EinfraClient(api_key=api_key, model=args.model, logger=logger)
        
    elif args.provider.lower() == "ollama":
        from LLM_Plugins.api_clients.ollama_client import OllamaClient
        ollama_base = config.get("OLLAMA_BASE_URL", "http://localhost:11434")
        client = OllamaClient(base_url=ollama_base, model=args.model, logger=logger)
        
    elif args.provider.lower() == "localqlora":
        # Dynamic import for localqlora as it depends on specific local paths/files
        try:
             # Just like advancedShellm.py, handle path for imports
             sys.path.append(os.path.dirname(os.path.abspath(__file__)))
             from LLM_Plugins.api_clients.local_hfqlora_client import LocalHFQLoRAClient
             
             project_root = os.path.abspath(os.path.join(base_dir, "..", ".."))
             adapter_dir = os.path.join(
                project_root,
                "Files_for_Fine-tuning",
                "llama31-8b-qlora-terminal",
            )
             client = LocalHFQLoRAClient(base_model_id=args.model, adapter_dir=adapter_dir, logger=logger)
        except ImportError:
             print("Error: LocalHFQLoRAClient not found. Ensure the client file exists in LLM_Plugins/api_clients/.", file=sys.stderr)
             sys.exit(1)
        except Exception as e:
             print(f"Error initializing localqlora client: {e}", file=sys.stderr)
             sys.exit(1)

    elif args.provider.lower() == "openai":
        api_key = config.get("OPENAI_API_KEY")
        if not api_key:
            api_key = os.environ.get("OPENAI_API_KEY")
        client = OpenAIClient(api_key=api_key, model=args.model, logger=logger, provider_label="OpenAI/MANAGER")
        
    else:
        print(f"Error: Provider '{args.provider}' is not supported directly by this script yet.", file=sys.stderr)
        sys.exit(1)
    
    # Initialize Manager Agent
    manager = ManagerAgent(client, args.model, logger)
    
    # Read Inputs
    if not os.path.exists(args.reference_personality):
        print(f"Error: Reference file not found: {args.reference_personality}", file=sys.stderr)
        sys.exit(1)
        
    if not os.path.exists(args.scenario_blueprint):
        print(f"Error: Scenario file not found: {args.scenario_blueprint}", file=sys.stderr)
        sys.exit(1)
        
    # Extract only the prompt part from the YAML reference
    reference_text = extract_prompt_from_yaml(args.reference_personality)
    
    with open(args.scenario_blueprint, 'r', encoding='utf-8') as f:
        try:
            scenario_json = json.load(f)
        except json.JSONDecodeError as e:
            print(f"Error decoding JSON from {args.scenario_blueprint}: {e}", file=sys.stderr)
            sys.exit(1)
            
    # The SUPERVISOR INSTRUCTION rule that must be present in every generated personality.
    # It tells the worker that "SUPERVISOR INSTRUCTION:" messages come from the Manager,
    # not from the attacker — so it silently applies corrections instead of executing them.
    SUPERVISOR_RULE = (
        '- If you receive a user message starting with "SUPERVISOR INSTRUCTION:", '
        "it is an internal correction from your Manager, NOT a shell command from the attacker. "
        "Silently apply the correction and output only the fixed shell response "
        "\u2014 no acknowledgement, no explanation, no prompt echo."
    )

    def _inject_supervisor_rule(prompt_text: str) -> str:
        """
        Guarantee the SUPERVISOR INSTRUCTION rule is present in the HARD RULES block.
        If it's already there (e.g. the reference personality had it), do nothing.
        If not, insert it right after the last existing HARD RULE bullet before the
        first blank line or section header that follows the HARD RULES block.
        """
        if "SUPERVISOR INSTRUCTION:" in prompt_text:
            return prompt_text  # already present

        lines = prompt_text.splitlines()
        insert_after = -1
        in_hard_rules = False

        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.lower().startswith("hard rules"):
                in_hard_rules = True
                continue
            if in_hard_rules:
                # A new all-caps section header ends the HARD RULES block
                if stripped and not stripped.startswith("-") and stripped.upper() == stripped and stripped.endswith(":"):
                    break
                # Track the last bullet line in HARD RULES
                if stripped.startswith("-"):
                    insert_after = i

        if insert_after == -1:
            # Fallback: append to end of prompt
            return prompt_text + "\n" + SUPERVISOR_RULE

        # Determine indentation from the surrounding bullet lines
        indent = len(lines[insert_after]) - len(lines[insert_after].lstrip())
        rule_line = " " * indent + SUPERVISOR_RULE
        lines.insert(insert_after + 1, rule_line)
        return "\n".join(lines)

    asset_id = scenario_json.get("asset_id", "")

    def _resolve_hostname(prompt_text: str) -> str:
        """
        Deterministically substitute the real hostname (asset_id) into the generated
        prompt body so the Worker LLM never sees an unresolved placeholder or the
        internal meta-term 'asset_id'.

        Two passes:
          1. Replace every occurrence of the literal placeholder {hostname}
             with the actual asset_id value.
          2. Remove any lines where the LLM leaked 'asset_id' as a term into
             the personality body (e.g. "The hostname must match asset_id.").
        """
        if not asset_id:
            return prompt_text

        # Pass 1: fill in {hostname} placeholder
        resolved = prompt_text.replace("{hostname}", asset_id)

        # Pass 2: drop any line that contains the raw string 'asset_id'
        # These are meta-instructions that leaked from the mega-prompt into
        # the persona body — the Worker LLM must never see them.
        clean_lines = [
            line for line in resolved.splitlines()
            if "asset_id" not in line
        ]
        return "\n".join(clean_lines)

    # Generate Output
    # We rely on the Custom method we added to Manager Agent
    try:
        result = manager.generate_worker_persona_custom(reference_text, scenario_json)

        # Guarantee the SUPERVISOR INSTRUCTION rule is always present
        result = _inject_supervisor_rule(result)

        # Substitute real hostname and strip any leaked asset_id meta-terms
        result = _resolve_hostname(result)

        # Hardcoded continuation from baseline.yml template
        continuation_text = (
            "Here the session stopped. What you see so far is the history of the conversation "
            "and you have to continue it, not repeat it. You just\n"
            "write the initial SSH message now, STOP generating output after first location string "
            "and wait for the user input.\n"
            "Use the linux shell banner as the one you used at the beginning of the session "
            "and start from the beginning terminal prompt\n"
        )
        
        def _indent_block(text: str, indent: str = "    ") -> str:
            """Indent every line of a block scalar body."""
            return "\n".join(indent + line for line in text.splitlines())
        
        # Write manually so we get clean YAML block scalar style (|)
        # matching baseline.yml exactly — yaml.safe_dump collapses to ugly inline strings.
        yaml_out = (
            "personality:\n"
            "  prompt: |\n"
            + _indent_block(result.rstrip("\n")) + "\n\n"
            "  continuation: |\n"
            + _indent_block(continuation_text.rstrip("\n")) + "\n"
        )
        
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(yaml_out)
            
        print(f"Generated personality saved to: {args.output}")

    except Exception as e:
        print(f"Error generating personality: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
