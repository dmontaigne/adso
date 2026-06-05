from setuptools import find_packages, setup

setup(
    name="adso",
    version="0.1.0",
    description="Local-first Goodreads backup and personal library catalogue sync tool.",
    package_dir={"": "src"},
    packages=find_packages("src"),
    python_requires=">=3.9",
    entry_points={"console_scripts": ["adso=adso.cli:main"]},
    extras_require={
        "notion": ["requests>=2.31", "python-dotenv>=1.0"],
    },
)
