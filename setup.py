import os
from glob import glob

from setuptools import Extension, setup
from setuptools.command.build_ext import build_ext


class BuildExtension(build_ext):
    # the flags are the ones of the compiler which builds it, not of the platform. An
    # optimization level given through CFLAGS - the sanitizer and coverage builds - wins,
    # the flags here come after it and would override it otherwise
    def build_extensions(self) -> None:
        optimized: bool = "-O" not in os.environ.get("CFLAGS", "")
        flags: list[str] = (
            ["/O2", "/W3"]
            if self.compiler.compiler_type == "msvc"
            else [*(["-O2"] if optimized else []), "-Wall", "-Wextra", "-Wno-unused-parameter"]
        )
        for extension in self.extensions:
            extension.extra_compile_args = flags

        super().build_extensions()


setup(
    cmdclass={"build_ext": BuildExtension},
    ext_modules=[
        Extension(
            "haiway._native._nstruct",
            ["src/haiway/_native/_nstruct.c"],
            # the headers are parts of the one source file - a change of one is a change of it
            depends=sorted(glob("src/haiway/_native/*.h")),
        )
    ],
)
