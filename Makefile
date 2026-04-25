.PHONY: build clean install

PYTHON ?= python3

build: omenfan.pyz

omenfan.pyz: omenfan/__main__.py omenfan/__init__.py omenfan/*.py
	rm -rf build
	mkdir -p build
	cp -r omenfan build/omenfan
	find build/omenfan -name __pycache__ -type d -exec rm -rf {} +
	$(PYTHON) -m zipapp build -o omenfan.pyz -m "omenfan.__main__:entry" -p "/usr/bin/env python3"
	rm -rf build
	@echo "Built omenfan.pyz ($$(wc -c < omenfan.pyz) bytes)"

install: omenfan.pyz
	install -m 755 omenfan.pyz /usr/local/bin/omenfan
	@echo "Installed to /usr/local/bin/omenfan"

clean:
	rm -f omenfan.pyz
