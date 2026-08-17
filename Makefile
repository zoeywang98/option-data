.PHONY: test validate-example

test:
	python -m unittest discover -s tests -v

validate-example:
	python -m option_data validate examples/runs/2026-08-14/SPX/155800
