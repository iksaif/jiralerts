#!/usr/bin/env python
import os
import setuptools


# allow setup.py to be run from any path
os.chdir(os.path.normpath(os.path.join(os.path.abspath(__file__), os.pardir)))


def _read_reqs(relpath):
    fullpath = os.path.join(os.path.dirname(__file__), relpath)
    with open(fullpath) as f:
        return [s.strip() for s in f.readlines()
                if (s.strip() and not s.startswith("#"))]


_REQUIREMENTS_TXT = _read_reqs("requirements.txt")
_TESTS_REQUIREMENTS_TXT = _read_reqs("tests-requirements.txt")
_DEPENDENCY_LINKS = [l for l in _REQUIREMENTS_TXT if "://" in l]
_INSTALL_REQUIRES = [l for l in _REQUIREMENTS_TXT if "://" not in l]
_TEST_REQUIRE = [l for l in _TESTS_REQUIREMENTS_TXT if "://" not in l]


setuptools.setup(
    name='jiralerts',
    version='0.1-criteo-1',
    include_package_data=True,
    install_requires=_INSTALL_REQUIRES,
    dependency_links=_DEPENDENCY_LINKS,
    tests_require=_TEST_REQUIRE,
    entry_points={
        'console_scripts': [
            'jiralerts = jiralerts.main:main',
        ],
    },
    packages=setuptools.find_packages())
