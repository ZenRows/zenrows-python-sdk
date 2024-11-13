.PHONY: install build clean lint test

install:
	pip install -r requirements.txt

build:
	python setup.py sdist bdist_wheel

clean:
	python setup.py clean
	rm -rf dist build zenrows.egg-info .pytest_cache
	find . -name '__pycache__' -delete -o -name '*.pyc' -delete

lint:
	flake8 --config flake8 setup.py tests zenrows

test:
	python -m pytest tests
