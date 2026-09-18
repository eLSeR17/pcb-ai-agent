"""Bundled compile-check stubs (package data, not a Python module).

This directory holds the minimal C/C++ headers used by
:func:`pcbai.firmware.compile_check.compile_check` to syntax-check
generated firmware with the system ``gcc``/``g++`` without vendoring the
real vendor toolchains. It is a package so setuptools ships the ``.h``
files in wheels (see the ``[tool.setuptools.package-data]`` section of
``pyproject.toml``).
"""
