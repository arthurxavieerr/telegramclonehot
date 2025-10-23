from setuptools import setup, find_packages

setup(
    name="ScriptClone",
    version="1.0.0",
    description="Ferramenta para clonar álbuns de grupos do Telegram usando Telethon",
    author="SarfxxFx",
    packages=find_packages(),
    install_requires=[
        "telethon==1.37.0",
        "requests>=2.32.0",
        "rich>=14.0.0",
        "msgpack>=1.1.0",
        "setuptools>=70.0.0",
        "tomli>=2.2.0; python_version<'3.11'",
        "packaging>=25.0",
        "pygments>=2.19.0",
        "platformdirs>=4.3.0",
        "distlib>=0.4.0",
        "distro>=1.9.0",
        "pyproject-hooks>=1.2.0",
        "resolvelib>=1.2.0",
        "truststore>=0.10.0",
        "dependency-groups>=1.3.0",
        "CacheControl>=0.14.0"
    ],
    python_requires=">=3.8",
)