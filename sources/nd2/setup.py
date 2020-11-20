#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
from setuptools import setup, find_packages


def prerelease_local_scheme(version):
    """
    Return local scheme version unless building on master in CircleCI.

    This function returns the local scheme version number
    (e.g. 0.0.0.dev<N>+g<HASH>) unless building on CircleCI for a
    pre-release in which case it ignores the hash and produces a
    PEP440 compliant pre-release version number (e.g. 0.0.0.dev<N>).
    """
    from setuptools_scm.version import get_local_node_and_date

    if os.getenv('CIRCLE_BRANCH') in ('master', ):
        return ''
    else:
        return get_local_node_and_date(version)


setup(
    name='large-image-source-nd2',
    use_scm_version={'root': '../..', 'local_scheme': prerelease_local_scheme},
    setup_requires=['setuptools-scm'],
    description='An nd2 (NIS Elements) tilesource for large_image',
    long_description='See the large-image package for more details.',
    author='Kitware, Inc.',
    author_email='kitware@kitware.com',
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'License :: OSI Approved :: Apache Software License',
        'Programming Language :: Python :: 2',
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
    ],
    install_requires=[
        'large-image>=1.0.0',
        'nd2reader>=3.2',
    ],
    extras_require={
        'girder': 'girder-large-image>=1.0.0',
    },
    license='Apache Software License 2.0',
    keywords='large_image, tile source',
    packages=find_packages(exclude=['test', 'test.*']),
    url='https://github.com/girder/large_image',
    python_requires='>=2.7, !=3.0.*, !=3.1.*, !=3.2.*, !=3.3.*, !=3.4.*',
    entry_points={
        'large_image.source': [
            'nd2 = large_image_source_nd2:ND2FileTileSource'
        ],
        'girder_large_image.source': [
            'nd2 = large_image_source_nd2.girder_source:ND2GirderTileSource'
        ]
    },
)