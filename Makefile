.PHONY: ui poll dry backfill offmarket digest test auth gmail-setup secrets

ui:
	uv run uvicorn ui.app:app --port 8765 --reload --reload-dir ui --reload-dir src

poll:
	uv run python -m src.run --once

dry:
	uv run python -m src.run --once --dry-run

backfill:
	uv run python -m src.run --once --backfill 7

offmarket:
	uv run python -m src.offmarket.build_map

digest:
	uv run python -m src.digest

test:
	uv run pytest -q

auth:
	uv run python scripts/gmail_auth.py $(SECRET)

gmail-setup:
	uv run python scripts/setup_gmail.py

secrets:
	bash scripts/push_secrets.sh
