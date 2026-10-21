#!/usr/bin/env bash
# Leadfinder terminal runner -- no venv, no pip install required.
# Runs the agent directly with the system Python.
cd "$(dirname "$0")"
exec python agent.py "$@"
