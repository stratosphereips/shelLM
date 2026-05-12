"""
Script to list available models from the E-INFRA API.

Usage:
    # Execute with the virtual environment python:
    ./venv/bin/python list_einfra_models.py
    
    # Or if your venv is already active:
    python list_einfra_models.py
"""
import os
import requests
from dotenv import dotenv_values
import json
import re

def main():
    # Load .env variables
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Try current directory first, then parent
    possible_env_paths = [
        os.path.join(base_dir, ".env"),
        os.path.join(base_dir, "..", ".env")
    ]
    
    env_path = None
    for p in possible_env_paths:
        if os.path.exists(p):
            env_path = p
            break
            
    if not env_path:
        print(f"Error: .env file not found in {possible_env_paths}")
        return

    config = dotenv_values(env_path)
    api_key = config.get("EINFRA_API_KEY")

    if not api_key:
        print("Error: EINFRA_API_KEY not found in .env file")
        return

    # Endpoint URL (guessing standard OpenAI compatible /models endpoint)
    # The chat endpoint is https://chat.ai.e-infra.cz/api/chat/completions
    # So models should be https://chat.ai.e-infra.cz/api/models
    url = "https://chat.ai.e-infra.cz/api/models"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            models_data = response.json()
            
            # Print a list of available models
            print("Available Models:")
            data = models_data.get('data', [])
            if data:
                for model in data:
                    fmt_id = model.get('id', 'unknown')
                    name = model.get('name', '')
                    
                    # Attempt to extract size (e.g., 7b, 120b) from ID or Name
                    size_match = re.search(r'(?:^|[-_: ])(\d+(?:\.\d+)?[bB])(?:[-_: ]|$)', fmt_id, re.IGNORECASE)
                    if not size_match:
                        size_match = re.search(r'(?:^|[-_: ])(\d+(?:\.\d+)?[bB])(?:[-_: ]|$)', name, re.IGNORECASE)
                        
                    if size_match:
                        print(f"- {fmt_id} (Size: {size_match.group(1).upper()})")
                    else:
                        print(f"- {fmt_id}")
            else:
                print("No models found in 'data' key.")
                
        else:
            print(f"Error: Received status code {response.status_code}")
            print(response.text)

    except requests.RequestException as e:
        print(f"Request failed: {e}")

if __name__ == "__main__":
    main()
