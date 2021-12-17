## Development

Useful commands for development and publishing.

### Build

`make build` generates the distribution packages. It will not delete previous builds. Remember to change the `__version__` before publishing or it will fail.

### Clean

`make clean` removes previous builds and cache files.

### Lint

`make lint` runs the linter (`flake8`) on the source and test files.

### Test

`make test` runs all the tests.

### Upload to PyPI

`python -m twine upload dist/*` uploads the latest build to PyPI. It will upload the whole `dist` folder, failing if there was a previous version. Run the `clean` command on those cases. Upload attempts of existing versions will fail with a `File already exists` error.

For uploading to the test repository, use `python -m twine upload --repository testpypi dist/*`. The same restrictions apply.
