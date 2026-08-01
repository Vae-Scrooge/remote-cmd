from setuptools import find_packages, setup

with open("README.md", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="remote_cmd_manager",
    version="1.1.1",
    author="Vae-Scrooge",
    author_email="vaescrooge@gmail.com",
    description="A Python-based SSH remote server management tool",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/Vae-Scrooge/remote-cmd",
    download_url="https://pypi.org/project/remote_cmd_manager/",
    project_urls={
        "Source Code": "https://github.com/Vae-Scrooge/remote-cmd",
        "Bug Tracker": "https://github.com/Vae-Scrooge/remote-cmd/issues",
        "Documentation": "https://github.com/Vae-Scrooge/remote-cmd#readme",
    },
    packages=find_packages(),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Intended Audience :: System Administrators",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Topic :: System :: Systems Administration",
        "Topic :: Utilities",
        "Environment :: Console",
    ],
    python_requires=">=3.8",
    install_requires=[
        "paramiko>=3.0.0",
        "click>=8.0.0",
        "pyyaml>=6.0",
        "colorama>=0.4.4; platform_system=='Windows'",
        "aiofiles>=3.0.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.0.0",
            "pytest-cov>=4.0.0",
            "black>=22.0.0",
            "flake8>=5.0.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "remote-cmd=remote_cmd.cli.main:main",
        ],
    },
    include_package_data=True,
    zip_safe=False,
)
