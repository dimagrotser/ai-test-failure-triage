.PHONY: lab
lab:
	uv run python -m evals.lab.build

.PHONY: lab-mutants
lab-mutants:
	uv run python -m evals.lab.mutate
	$(MAKE) lab
