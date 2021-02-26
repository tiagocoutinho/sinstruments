# -*- coding: utf-8 -*-
#
# This file is part of the sinstruments project
#
# Copyright (c) 2018-present Tiago Coutinho
# Distributed under the GPLv3 license. See LICENSE for more info.

"""The setup script."""

from setuptools import setup, find_packages

with open('README.md') as readme_file:
    readme = readme_file.read()

requirements = ['gevent', 'click>=7.1']

extras = {
    'yaml': ['PyYAML'],
    'toml': ['toml'],
}

extras["all"] = list(set.union(*(set(i) for i in extras.values())))

setup_requirements = ['pytest-runner']

test_requirements = ['pytest']

setup(
    author="Tiago Coutinho",
    author_email='coutinhotiago@gmail.com',
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: GNU General Public License v3 (GPLv3)',
        'Natural Language :: English',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
    ],
    description="A simulator for real hardware which is accessible via TCP, UDP or serial line",
    install_requires=requirements,
    long_description=readme,
    long_description_content_type="text/markdown",
    include_package_data=True,
    keywords='sinstruments',
    name='sinstruments',
    packages=find_packages(include=['sinstruments']),
    setup_requires=setup_requirements,
    test_suite='tests',
    tests_require=test_requirements,
    extras_require=extras,
    python_requires=">=3.5",
    url='https://github.com/tiagocoutinho/sinstruments',
    version='1.3.2',
    zip_safe=False,
        entry_points={
        'console_scripts': [
            'sinstruments-server = sinstruments.simulator:main',
        ]
    },
)
