import os
from setuptools import setup

from zenrows.__version__ import __version__


def read(fname):
    return open(os.path.join(os.path.dirname(__file__), fname)).read()


setup(
    name="zenrows",
    version=__version__,
    author="Ander Rodriguez",
    author_email="ander@zenrows.com",
    description="Python client for ZenRows API",
    license="MIT",
    keywords="zenrows scraper scraping",
    url="https://github.com/ZenRows/zenrows-python-sdk",
    project_urls={
        "Bug Tracker": "https://github.com/ZenRows/zenrows-python-sdk/issues",
        "Documentation": "https://www.zenrows.com/documentation",
    },
    packages=["zenrows"],
    long_description=read("README.md"),
    long_description_content_type="text/markdown",
    classifiers=[
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Topic :: Internet :: Proxy Servers",
        "Topic :: Internet :: WWW/HTTP",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    python_requires=">=3.6",
    install_requires=["requests"],
)
