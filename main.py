#!/usr/bin/env python3

import argparse
import yaml
import curses
import locale
from db import Database
import os
from tui import ChatTUI

DEFAULT_CONFIG_PATH = os.path.expanduser("~/.config/aitui/config.yaml")
DEFAULT_DB_PATH = os.path.expanduser("~/.config/aitui/chats.db")

def main():
    # Set locale for UTF-8 support
    locale.setlocale(locale.LC_ALL, '')
    # Set ESCDELAY before curses initialization for better responsiveness
    os.environ.setdefault('ESCDELAY', '25')
    parser = argparse.ArgumentParser(description="Minimalist TUI Chat Bot")
    parser.add_argument("-c", "--config", default=DEFAULT_CONFIG_PATH, help="Path to config file")
    parser.add_argument("-p", "--provider", help="LLM Provider (openrouter/ollama/openai)")
    parser.add_argument("-m", "--model", help="Model name")
    parser.add_argument("-d", "--db", default=DEFAULT_DB_PATH, help="Path to SQLite database")
    args = parser.parse_args()

    db_inst = Database(args.db)

    try:
        with open(args.config, 'r') as f:
            config = yaml.safe_load(f)
    except FileNotFoundError:
        config = {}

    provider = args.provider or config.get('provider', 'openrouter')
    provider_config = config.get(provider, {})
    
    if args.model:
        provider_config['model'] = args.model

    if not provider_config.get('model'):
        # Fallback defaults if config is missing
        if provider == "ollama":
            provider_config['model'] = "gemma3"
            provider_config['base_url'] = "http://localhost:11434"
        elif provider == "openai":
            provider_config['model'] = "gpt-4o"
            provider_config['api_url'] = "https://api.openai.com/v1"
        else:
            print(f"Error: No model specified for provider {provider}")
            return

    db_inst.init_db()
    
    def start_tui(stdscr):
        app = ChatTUI(stdscr, provider, provider_config['model'], config, db_inst)
        app.run()

    try:
        curses.wrapper(start_tui)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
