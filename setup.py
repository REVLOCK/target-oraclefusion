#!/usr/bin/env python

from setuptools import find_packages, setup


setup(
    name="target-oracle-fusion",
    version="0.0.1",
    description="Singer target: journal CSV to Oracle Fusion GL upload and ESS polling.",
    author="Aravindh Balakrishnan",
    url="https://github.com/REVLOCK/target-oraclefusion",
    classifiers=["Programming Language :: Python :: 3 :: Only"],
    install_requires=[
        "target-hotglue @ git+https://gitlab.com/hotglue/target-hotglue-sdk.git",
        "requests>=2.31.0,<3.0.0",
        "PyJWT[crypto]>=2.8.0,<3.0.0",
        "boto3>=1.28.0,<2.0.0",
    ],
    entry_points={
        "console_scripts": [
            "target-oracle-fusion=target_oracle_fusion:main",
        ],
    },
    packages=find_packages(exclude=["tests"]),
    include_package_data=True,
)
