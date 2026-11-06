import os
import sys
from glob import glob

from setuptools import Extension, setup

setup(
    ext_modules=[
        Extension(
            "haiway._native._nstruct",
            ["src/haiway/_native/_nstruct.c"],
            # the headers are parts of the one source file - a change of one is a change of it
            depends=sorted(glob("src/haiway/_native/*.h")),
            extra_compile_args=(
                ["/O2", "/W3"]
                if sys.platform == "win32"
                else ["-O2", "-Wall", "-Wextra", "-Wno-unused-parameter"]
            ),
            # a failed build is silent unless the native implementation is demanded
            optional=os.environ.get("HAIWAY_NATIVE") != "1",
        )
    ],
)
