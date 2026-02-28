.PHONY: build clean install

PYTHON ?= python3

build: omenfan.pyz

omenfan.pyz: omenfan/__main__.py omenfan/__init__.py omenfan/*.py
	$(PYTHON) -m zipapp omenfan -o omenfan.pyz -p "/usr/bin/env python3"
	@echo "Built omenfan.pyz ($$(wc -c < omenfan.pyz) bytes)"

install: omenfan.pyz
	install -m 755 omenfan.pyz /usr/local/bin/omenfan
	@echo "Installed to /usr/local/bin/omenfan"

clean:
	rm -f omenfan.pyz
