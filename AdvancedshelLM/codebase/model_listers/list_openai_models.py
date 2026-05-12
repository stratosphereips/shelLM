"""
Script to list available models from the OpenAI API.

Usage:
    # Execute with the virtual environment python:
    ./venv/bin/python model_listers/list_openai_models.py

    # Or if your venv is already active:
    python model_listers/list_openai_models.py
"""
import os
import requests
from dotenv import dotenv_values


def main():
    # Load .env variables
    base_dir = os.path.dirname(os.path.abspath(__file__))

    # Try current directory first, then parent, then grandparent
    possible_env_paths = [
        os.path.join(base_dir, ".env"),
        os.path.join(base_dir, "..", ".env"),
        os.path.join(base_dir, "..", "..", ".env"),
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
    api_key = config.get("OPENAI_API_KEY")

    if not api_key:
        print("Error: OPENAI_API_KEY not found in .env file")
        return

    url = "https://api.openai.com/v1/models"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.get(url, headers=headers, timeout=15)

        if response.status_code == 200:
            models_data = response.json()
            data = models_data.get("data", [])

            if not data:
                print("No models returned by the API.")
                return

            # Sort alphabetically by id for readability
            data.sort(key=lambda m: m.get("id", ""))

            print(f"Available OpenAI Models ({len(data)} total):")
            for model in data:
                model_id = model.get("id", "unknown")
                owned_by = model.get("owned_by", "")
                if owned_by:
                    print(f"  - {model_id}  (owned_by: {owned_by})")
                else:
                    print(f"  - {model_id}")
        else:
            print(f"Error: Received status code {response.status_code}")
            print(response.text)

    except requests.RequestException as e:
        print(f"Request failed: {e}")


if __name__ == "__main__":
    main()
