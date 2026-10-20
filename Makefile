# =====================================================================
# Paylane AI Support Agent - Developer Makefile
# =====================================================================

.PHONY: test eval serve clean help

help:
	@echo "Paylane Support Agent Commands:"
	@echo "  make serve    - Start the FastAPI server and UI on port 8000"
	@echo "  make test     - Run deterministic unit and regression tests"
	@echo "  make eval     - Execute live model evaluation across 12 scenarios"
	@echo "  make clean    - Remove bytecode and cache artifacts"

serve:
	uvicorn paylane_api.main:app --host 0.0.0.0 --port 8000 --reload

test:
	pytest -v

eval:
	python -m eval.run_eval

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
