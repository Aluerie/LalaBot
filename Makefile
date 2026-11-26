# Recipes are PowerShell, on every OS.
ifeq ($(OS),Windows_NT)
    SHELL := pwsh.exe
else
    SHELL := pwsh
endif
.SHELLFLAGS := -NoProfile -Command

# SSH destination of the VPS: a `Host` alias from ~/.ssh/config or user@host.
# Override it per command, e.g. make scp VPS=ubuntu@1.2.3.4, or with a VPS environment variable.
VPS ?= vps

default: help

.PHONY: help
.SILENT: help
# Prints every target that has a `# description` comment, under the `##@ Section` line above it.
# Make joins these lines into one PowerShell command; `\#` is a literal `#` (a bare one would start a make comment).
HELP_SCRIPT = \
	Write-Host 'Usage: make <command>'; \
	foreach ($$line in Get-Content '$(firstword $(MAKEFILE_LIST))') { \
		if ($$line -match '^\#\#@ (.+)') { \
			Write-Host ([Environment]::NewLine + $$Matches[1] + ':') -ForegroundColor Magenta \
		} elseif ($$line -match '^([\w-]+):[^=\#]*\#\s*(.+)') { \
			Write-Host ('    {0,-12}{1}' -f $$Matches[1], $$Matches[2]) \
		} \
	}

help:  # Show this help
	$(HELP_SCRIPT)


##@ Home database

.PHONY: db
.SILENT: db
db:  # Start the home PostgreSQL (dev-db/compose.yml)
	docker compose -f dev-db/compose.yml up -d --wait


.PHONY: db-down
.SILENT: db-down
db-down:  # Stop the home PostgreSQL (the data stays)
	docker compose -f dev-db/compose.yml down


##@ VPS

.PHONY: scp
.SILENT: scp
scp:  # Copy config.py to the VPS
	scp config.py $(VPS):~/LalaBot/config.py


.PHONY: monitoring
.SILENT: monitoring
monitoring:  # Check every bot's outage alerts setup on the VPS
	ssh $(VPS) sh LalaBot/scripts/check_monitoring.sh
