@echo off
REM Leadfinder terminal runner -- no venv, no pip install required.
REM Runs the agent directly with the system Python.
python "%~dp0agent.py" %*
