# -*- coding: utf-8 -*-
name = "ilp_gallery"

version = "0.1.0"

description = "Reusable Gallery Widget to build apps"

authors = ["pagp"]


requires = [
    "PySide6",
    "td_utils-0",
    "ilp_docs",
]

variants = [
    ["python-3.13"],
    ["python-3.11"],
    ["python-3.10"],
]


def commands():
    env.PATH.append("{root}/bin")
    env.PYTHONPATH.append("{root}/python")
    # FIXME: do this only if houdini is in the requiremenets
    # env.HOUDINI_PATH.append("{root}/houdini")
