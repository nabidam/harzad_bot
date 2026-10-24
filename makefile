.PHONY: start
start:
	uv run python main.py

.PHONY: shell
shell:
	uv shell

.PHONY: sync
sync:
	uv sync

.PHONY: lock
lock:
	uv lock
