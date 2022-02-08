from __future__ import annotations

import abc
import datetime
from dataclasses import dataclass, field
from itertools import product
import enum
import glob
import os
from typing import Callable, ClassVar, Dict, List, Literal, Optional, Union

import aiofiles

from bci_build.data import SUPPORTED_SLE_SERVICE_PACKS
from bci_build.templates import DOCKERFILE_TEMPLATE, KIWI_TEMPLATE, SERVICE_TEMPLATE


@enum.unique
class ReleaseStage(enum.Enum):
    BETA = "beta"
    RELEASED = "released"

    def __str__(self) -> str:
        return self.value


@enum.unique
class ImageType(enum.Enum):
    SLE_BCI = "sle-bci"
    APPLICATION = "application"

    def __str__(self) -> str:
        return self.value


@enum.unique
class BuildType(enum.Enum):
    DOCKER = "docker"
    KIWI = "kiwi"

    def __str__(self) -> str:
        return self.value


@enum.unique
class PackageType(enum.Enum):
    DELETE = "delete"
    BOOTSTRAP = "bootstrap"
    IMAGE = "image"

    def __str__(self) -> str:
        return self.value


@dataclass
class Package:
    name: str
    pkg_type: PackageType = PackageType.IMAGE

    def __str__(self) -> str:
        return self.name


@dataclass
class Replacement:
    """Represents a replacement via the `obs-service-replace_using_package_version
    <https://github.com/openSUSE/obs-service-replace_using_package_version>`_.

    """

    #: regex to be replaced in the Dockerfile
    regex_in_dockerfile: str

    #: package name to be queried for the version
    package_name: str

    #: specify how the version should be formated, see
    #: `https://github.com/openSUSE/obs-service-replace_using_package_version#usage`_
    #: for further details
    parse_version: Optional[
        Literal["major", "minor", "patch", "patch_update", "offset"]
    ] = None


@dataclass
class BaseContainerImage(abc.ABC):
    name: str

    #: Human readable name that will be inserted into the image title and description
    pretty_name: str

    #: the name of the package on IBS in ``SUSE:SLE-15-SP$ver:Update:BCI``
    ibs_package: str

    #: the SLE service pack to which this package belongs
    sp_version: SUPPORTED_SLE_SERVICE_PACKS

    #: this container images release stage
    release_stage: ReleaseStage

    #: The container from which this one is derived. defaults to
    #: ``suse/sle15:15.$SP`` when an empty string is used.
    #: When from image is None, then no ``FROM`` or ``derived from=""`` entry is
    #: created in the image description.
    from_image: Optional[str] = ""

    #: an optional entrypoint for the image, is omitted if empty or ``None``
    entrypoint: Optional[str] = None

    #: Extra environment variables to be set in the container
    env: Union[Dict[str, Union[str, int]], Dict[str, str], Dict[str, int]] = field(
        default_factory=dict
    )

    replacements_via_service: List[Replacement] = field(default_factory=list)

    tech_preview: bool = True

    #: additional labels that should be added to the image
    extra_labels: Dict[str, str] = field(default_factory=dict)

    #: Packages to be installed inside the container
    package_list: Union[List[str], List[Package]] = field(default_factory=list)

    #: This string is appended to the automatically generated dockerfile and can
    #: contain arbitrary instructions valid for a :file:`Dockerfile`
    custom_end: str = ""

    #: A bash script that is put into :file:`config.sh` if a kiwi image is
    #: created.
    config_sh_script: str = ""
    #: the maintainer of this image, defaults to SUSE
    maintainer: str = "SUSE LLC (https://www.suse.com/)"

    #: additional files that belong into this container-package
    #: the key is the filename, the value are the file contents
    extra_files: Dict[str, Union[str, bytes]] = field(default_factory=dict)

    #: additional names under which this image should be published alongside
    #: :py:attr:`~BaseContainerImage.name`.
    #: These names are only inserted into the
    #: :py:attr:`~BaseContainerImage.build_tags`
    additional_names: List[str] = field(default_factory=list)

    #: by default the containers get the labelprefix
    #: ``com.suse.bci.{self.name}``. If this value is not an empty string, then
    #: it is used instead of the name after ``com.suse.bci.``.
    custom_labelprefix_end: str = ""

    #: Provide a custom description instead of the automatically generated one
    custom_description: str = ""

    #: Define whether this container image is built using docker or kiwi
    build_recipe_type: BuildType = BuildType.DOCKER

    URL: ClassVar[str] = "https://www.suse.com/products/server/"

    VENDOR: ClassVar[str] = "SUSE LLC"

    def __post_init__(self) -> None:
        if not self.package_list:
            raise ValueError(f"No packages were added to {self.pretty_name}.")
        if self.config_sh_script and self.custom_end:
            raise ValueError(
                "Cannot specify both a custom_end and a config.sh script! Use just config_sh_script."
            )

    @property
    @abc.abstractmethod
    def nvr(self) -> str:
        pass

    @property
    @abc.abstractmethod
    def version_label(self) -> str:
        pass

    @property
    def ibs_project(self) -> str:
        return f"SUSE:SLE-15-SP{self.sp_version}:Update:BCI"

    @property
    def dockerfile_custom_end(self) -> str:
        if self.custom_end:
            return self.custom_end
        if self.config_sh_script:
            return f"RUN {self.config_sh_script}"
        return ""

    @property
    def config_sh(self) -> str:
        if not self.config_sh_script:
            if self.custom_end:
                raise ValueError(
                    "This image cannot be build as a kiwi image, it has a `custom_end` set."
                )
            return ""
        return f"""#!/bin/bash -e

# Copyright (c) {datetime.datetime.now().date().strftime("%Y")} SUSE LLC, Nuernberg, Germany.
#
# All modifications and additions to the file contributed by third parties
# remain the property of their copyright owners, unless otherwise agreed
# upon. The license for this file, and modifications and additions to the
# file, is the same license as for the pristine package itself (unless the
# license for the pristine package is not an Open Source License, in which
# case the license is the MIT License). An "Open Source License" is a
# license that conforms to the Open Source Definition (Version 1.9)
# published by the Open Source Initiative.

test -f /.kconfig && . /.kconfig
test -f /.profile && . /.profile

echo "Configure image: [$kiwi_iname]..."

#======================================
# Setup baseproduct link
#--------------------------------------
if [ ! -e /etc/products.d/baseproduct ]; then
    suseSetupProduct
fi

#======================================
# Import repositories' keys
#--------------------------------------
suseImportBuildKey

{self.config_sh_script}

exit 0
"""

    @property
    def packages(self) -> str:
        return " ".join(str(pkg) for pkg in self.package_list)

    @property
    def kiwi_packages(self) -> str:
        def create_pkg_filter_func(
            pkg_type: PackageType,
        ) -> Callable[[Union[str, Package]], bool]:
            def pkg_filter_func(p: Union[str, Package]) -> bool:
                if isinstance(p, str):
                    return pkg_type == PackageType.IMAGE
                return p.pkg_type == pkg_type

            return pkg_filter_func

        PKG_TYPES = (
            PackageType.DELETE,
            PackageType.BOOTSTRAP,
            PackageType.IMAGE,
        )
        delete_packages, bootstrap_packages, image_packages = (
            list(filter(create_pkg_filter_func(pkg_type), self.package_list))
            for pkg_type in PKG_TYPES
        )

        res = ""
        for (pkg_list, pkg_type) in zip(
            (delete_packages, bootstrap_packages, image_packages), PKG_TYPES
        ):
            if len(pkg_list) > 0:
                res += (
                    f"""  <packages type="{pkg_type}">
    """
                    + """
    """.join(
                        f'<package name="{pkg}"/>' for pkg in pkg_list
                    )
                    + """
  </packages>
"""
                )
        return res

    @property
    def env_lines(self) -> str:
        return "\n".join(f'ENV {k}="{v}"' for k, v in self.env.items())

    @property
    def kiwi_env_entry(self) -> str:
        if not self.env:
            return ""
        return (
            """        <environment>
          """
            + """
          """.join(
                f'<env name="{k}" value="{v}"/>' for k, v in self.env.items()
            )
            + """
        </environment>
"""
        )

    @property
    @abc.abstractmethod
    def image_type(self) -> ImageType:
        pass

    @property
    @abc.abstractmethod
    def build_tags(self) -> List[str]:
        pass

    @property
    def reference(self) -> str:
        return f"registry.suse.com/{self.build_tags[0]}"

    @property
    def description(self) -> str:
        return (
            self.custom_description
            or f"Image containing {self.pretty_name} based on the SLE Base Container Image."
        )

    @property
    def title(self) -> str:
        return f"SLE BCI {self.pretty_name} Container Image"

    @property
    def extra_label_lines(self) -> str:
        return "\n".join(f'LABEL {k}="{v}"' for k, v in self.extra_labels.items())

    @property
    def extra_label_xml_lines(self) -> str:
        return "\n".join(
            f'            <label name="{k}" value="{v}"/>'
            for k, v in self.extra_labels.items()
        )

    @property
    def labelprefix(self) -> str:
        return f"com.suse.bci.{self.custom_labelprefix_end or self.name}"

    @property
    def kiwi_additional_tags(self) -> Optional[str]:
        extra_tags = []
        for buildtag in self.build_tags[1:]:
            path, tag = buildtag.split(":")
            if path.endswith(self.name):
                extra_tags.append(tag)

        return ",".join(extra_tags) if extra_tags else None

    async def write_files_to_folder(self, dest: str) -> List[str]:
        files = ["_service"]

        if self.build_recipe_type == BuildType.DOCKER:
            fname = "Dockerfile"
            async with aiofiles.open(os.path.join(dest, fname), "w") as kiwi_file:
                await kiwi_file.write(DOCKERFILE_TEMPLATE.render(image=self))
            files.append(fname)

        elif self.build_recipe_type == BuildType.KIWI:
            fname = f"{self.ibs_package}.kiwi"
            async with aiofiles.open(os.path.join(dest, fname), "w") as kiwi_file:
                await kiwi_file.write(KIWI_TEMPLATE.render(image=self))
            files.append(fname)

            if self.config_sh:
                async with aiofiles.open(
                    os.path.join(dest, "config.sh"), "w"
                ) as config_sh:
                    await config_sh.write(self.config_sh)
                files.append("config.sh")

        async with aiofiles.open(os.path.join(dest, "_service"), "w") as service:
            await service.write(SERVICE_TEMPLATE.render(image=self))

        if not glob.glob(f"{dest}/*.changes"):
            changes_file_name = self.ibs_package + ".changes"
            async with aiofiles.open(
                os.path.join(dest, changes_file_name), "w"
            ) as changesfile:
                await changesfile.write("")
            files.append(changes_file_name)

        for fname, contents in self.extra_files.items():
            mode = "w" if isinstance(contents, str) else "b"
            files.append(fname)
            with open(os.path.join(dest, fname), mode) as f:
                f.write(contents)

        return files


@dataclass
class LanguageStackContainer(BaseContainerImage):
    #: the primary version of the language or application inside this container
    version: Union[str, int] = ""

    #: additional versions that should be added as tags to this container
    additional_versions: List[str] = field(default_factory=list)

    _registry_prefix: str = "bci"

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("A language stack container requires a version")

    @property
    def image_type(self) -> ImageType:
        return ImageType.SLE_BCI

    @property
    def version_label(self) -> str:
        return str(self.version)

    @property
    def nvr(self) -> str:
        return f"{self.name}-{self.version}"

    @property
    def build_tags(self) -> List[str]:
        tags = []
        for name in [self.name] + self.additional_names:
            tags += (
                [f"{self._registry_prefix}/{name}:{self.version_label}"]
                + [f"{self._registry_prefix}/{name}:{self.version_label}-%RELEASE%"]
                + [
                    f"{self._registry_prefix}/{name}:{ver}"
                    for ver in self.additional_versions
                ]
            )
        return tags


@dataclass
class ApplicationStackContainer(LanguageStackContainer):
    def __post_init__(self) -> None:
        self._registry_prefix = "suse"
        super().__post_init__()

    @property
    def image_type(self) -> ImageType:
        return ImageType.APPLICATION


@dataclass
class OsContainer(BaseContainerImage):
    @property
    def nvr(self) -> str:
        return self.name

    @property
    def version_label(self) -> str:
        return "%OS_VERSION_ID_SP%.%RELEASE%"

    @property
    def image_type(self) -> ImageType:
        return ImageType.SLE_BCI

    @property
    def build_tags(self) -> List[str]:
        tags = []
        for name in [self.name] + self.additional_names:
            tags += [
                f"bci/bci-{name}:%OS_VERSION_ID_SP%",
                f"bci/bci-{name}:{self.version_label}",
            ]
        return tags


(PYTHON_3_6_SP3, PYTHON_3_6_SP4) = (
    LanguageStackContainer(
        release_stage=ReleaseStage.BETA,
        env={"PYTHON_VERSION": "%%py3_ver%%", "PIP_VERSION": "%%pip_ver%%"},
        replacements_via_service=[
            Replacement(regex_in_dockerfile="%%py3_ver%%", package_name="python3-base"),
            Replacement(regex_in_dockerfile="%%pip_ver%%", package_name="python3-pip"),
        ],
        ibs_package=ibs_package,
        sp_version=sp_version,
        name="python",
        pretty_name="Python",
        version="3.6",
        package_list=[
            "python3",
            "python3-pip",
            "python3-wheel",
            "curl",
            "git-core",
        ],
    )
    for (sp_version, ibs_package) in ((3, "python-3.6"), (4, "python-3.6-image"))
)

_python_kwargs = {
    "name": "python",
    "pretty_name": "Python 3.9",
    "version": "3.9",
    "env": {"PYTHON_VERSION": "%%py39_ver%%", "PIP_VERSION": "%%pip_ver%%"},
    "package_list": [
        "python39",
        "python39-pip",
        "curl",
        "git-core",
    ],
    "replacements_via_service": [
        Replacement(regex_in_dockerfile="%%py39_ver%%", package_name="python39-base"),
        Replacement(regex_in_dockerfile="%%pip_ver%%", package_name="python39-pip"),
    ],
    "custom_end": r"""RUN rpm -e --nodeps $(rpm -qa|grep libpython3_6) python3-base && \
    ln -s /usr/bin/python3.9 /usr/bin/python3 && \
    ln -s /usr/bin/pip3.9 /usr/bin/pip3 && \
    ln -s /usr/bin/pip3.9 /usr/bin/pip""",
}

PYTHON_3_9_SP3 = LanguageStackContainer(
    release_stage=ReleaseStage.RELEASED,
    ibs_package="python-3.9",
    sp_version=3,
    **_python_kwargs,
)

_ruby_kwargs = {
    "name": "ruby",
    "ibs_package": "ruby-2.5-image",
    "pretty_name": "Ruby 2.5",
    "version": "2.5",
    "env": {
        # upstream does this
        "LANG": "C.UTF-8",
        "RUBY_VERSION": "%%rb_ver%%",
        "RUBY_MAJOR": "%%rb_maj%%",
    },
    "replacements_via_service": [
        Replacement(regex_in_dockerfile="%%rb_ver%%", package_name="ruby2.5"),
        Replacement(
            regex_in_dockerfile="%%rb_maj%%",
            package_name="ruby2.5",
            parse_version="minor",
        ),
    ],
    "package_list": [
        "ruby2.5",
        "ruby2.5-rubygem-bundler",
        "ruby2.5-devel",
        "curl",
        "git-core",
        "distribution-release",
        # additional dependencies to build rails, ffi, sqlite3 gems -->
        "gcc-c++",
        "sqlite3-devel",
        "make",
        "awk",
        # additional dependencies supplementing rails
        "timezone",
    ],
    # as we only ship one ruby version, we want to make sure that binaries belonging
    # to our gems get installed as `bin` and not as `bin.ruby2.5`
    "custom_end": "RUN sed -i 's/--format-executable/--no-format-executable/' /etc/gemrc",
}
RUBY_CONTAINERS = [
    LanguageStackContainer(
        sp_version=3, release_stage=ReleaseStage.RELEASED, **_ruby_kwargs
    ),
    LanguageStackContainer(
        sp_version=4, release_stage=ReleaseStage.BETA, **_ruby_kwargs
    ),
]


def _get_golang_kwargs(ver: Literal["1.16", "1.17"], sp_version: int):
    return {
        "sp_version": sp_version,
        "ibs_package": f"golang-{ver}" + ("-image" if sp_version == 4 else ""),
        "release_stage": ReleaseStage.RELEASED if sp_version < 4 else ReleaseStage.BETA,
        "name": "golang",
        "pretty_name": f"Golang {ver}",
        "version": ver,
        "env": {
            "GOLANG_VERSION": ver,
            "PATH": "/go/bin:/usr/local/go/bin:/root/go/bin/:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        },
        "package_list": [f"go{ver}", "distribution-release", "make"],
        "extra_files": {
            # the go binaries are huge and will ftbfs on workers with a root partition with 4GB
            "_constraints": """<constraints>
  <hardware>
    <disk>
      <size unit="G">6</size>
    </disk>
  </hardware>
</constraints>
"""
        },
    }


GOLANG_IMAGES = [
    LanguageStackContainer(**_get_golang_kwargs(ver, sp_version))
    for ver, sp_version in product(("1.16", "1.17"), (3, 4))
]


def _get_node_kwargs(ver: Literal[12, 14, 16], sp_version: SUPPORTED_SLE_SERVICE_PACKS):
    return {
        "name": "nodejs",
        "sp_version": sp_version,
        "release_stage": ReleaseStage.RELEASED
        if sp_version < 4
        else ReleaseStage.RELEASED,
        "ibs_package": f"nodejs-{ver}" + ("-image" if sp_version == 4 else ""),
        "additional_names": ["node"],
        "version": str(ver),
        "pretty_name": f"Node.js {ver}",
        "package_list": [
            f"nodejs{ver}",
            # devel dependencies:
            f"npm{ver}",
            "git-core",
            # dependency of nodejs:
            "update-alternatives",
            "distribution-release",
        ],
        "env": {
            "NODE_VERSION": ver,
            "NPM_VERSION": "%%npm_ver%%",
        },
        "replacements_via_service": [
            Replacement(regex_in_dockerfile="%%npm_ver%%", package_name=f"npm{ver}"),
        ],
    }


NODE_CONTAINERS = [
    LanguageStackContainer(**_get_node_kwargs(ver, sp_version))
    for ver, sp_version in product((12, 14, 16), (3, 4))
]


def _get_openjdk_kwargs(sp_version: int, devel: bool):
    JAVA_ENV = {
        "JAVA_BINDIR": "/usr/lib64/jvm/java/bin",
        "JAVA_HOME": "/usr/lib64/jvm/java",
        "JAVA_ROOT": "/usr/lib64/jvm/java",
        "JAVA_VERSION": "11",
    }

    comon = {
        "env": JAVA_ENV,
        "version": 11,
        "sp_version": sp_version,
        "release_stage": ReleaseStage.RELEASED if sp_version < 4 else ReleaseStage.BETA,
        "ibs_package": "openjdk-11"
        + ("-devel" if devel else "")
        + ("-image" if sp_version >= 4 else ""),
    }

    if devel:
        return {
            **comon,
            "name": "openjdk-devel",
            "custom_labelprefix_end": "openjdk.devel",
            "pretty_name": "OpenJDK 11 development",
            "package_list": ["java-11-openjdk-devel", "git-core", "maven"],
            "entrypoint": "jshell",
            "from_image": "bci/openjdk:11",
        }
    else:
        return {
            **comon,
            "name": "openjdk",
            "pretty_name": "OpenJDK 11",
            "package_list": ["java-11-openjdk"],
        }


OPENJDK_CONTAINERS = [
    LanguageStackContainer(**_get_openjdk_kwargs(sp_version, devel))
    for sp_version, devel in product((3, 4), (True, False))
]


THREE_EIGHT_NINE_DS = ApplicationStackContainer(
    release_stage=ReleaseStage.BETA,
    ibs_package="389-ds-container",
    sp_version=4,
    name="389-ds",
    maintainer="wbrown@suse.de",
    pretty_name="389 Directory Server",
    package_list=["389-ds", "timezone", "openssl"],
    version="1.4",
    custom_end=r"""EXPOSE 3389 3636

RUN mkdir -p /data/config && \
    mkdir -p /data/ssca && \
    mkdir -p /data/run && \
    mkdir -p /var/run/dirsrv && \
    ln -s /data/config /etc/dirsrv/slapd-localhost && \
    ln -s /data/ssca /etc/dirsrv/ssca && \
    ln -s /data/run /var/run/dirsrv

VOLUME /data

HEALTHCHECK --start-period=5m --timeout=5s --interval=5s --retries=2 \
    CMD /usr/lib/dirsrv/dscontainer -H

CMD [ "/usr/lib/dirsrv/dscontainer", "-r" ]
""",
)

INIT_CONTAINERS = [
    OsContainer(
        ibs_package=ibs_package,
        sp_version=sp_version,
        custom_description="Image containing systemd based on the SLE Base Container Image.",
        release_stage=release_stage,
        name="init",
        pretty_name="Init",
        package_list=["systemd"],
        entrypoint="/usr/lib/systemd/systemd",
        extra_labels={
            "usage": "This container should only be used to build containers for daemons. Add your packages and enable services using systemctl."
        },
    )
    for (sp_version, release_stage, ibs_package) in (
        (3, ReleaseStage.RELEASED, "init"),
        (4, ReleaseStage.BETA, "init-image"),
    )
]


with open(
    os.path.join(os.path.dirname(__file__), "mariadb", "entrypoint.sh")
) as entrypoint:
    MARIADB_CONTAINERS = [
        LanguageStackContainer(
            ibs_package="mariadb-image",
            sp_version=4,
            release_stage=ReleaseStage.BETA,
            name="mariadb",
            maintainer="bruno.leon@suse.de",
            version="10.6",
            pretty_name="MariaDB Server",
            custom_description="Image containing MariaDB server for RMT, based on the SLE Base Container Image.",
            package_list=["mariadb", "mariadb-tools", "gawk", "timezone", "util-linux"],
            entrypoint='["docker-entrypoint.sh"]',
            extra_files={"docker-entrypoint.sh": entrypoint.read(-1)},
            custom_end=r"""RUN mkdir /docker-entrypoint-initdb.d

VOLUME /var/lib/mysql

# docker-entrypoint from https://github.com/MariaDB/mariadb-docker.git
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod 755 /usr/local/bin/docker-entrypoint.sh
RUN ln -s usr/local/bin/docker-entrypoint.sh / # backwards compat

RUN sed -i 's#gosu mysql#su mysql -s /bin/bash -m#g' /usr/local/bin/docker-entrypoint.sh

# Ensure all logs goes to stdout
RUN sed -i 's/^log/#log/g' /etc/my.cnf

# Disable binding to localhost only, doesn't make sense in a container
RUN sed -i -e 's|^\(bind-address.*\)|#\1|g' /etc/my.cnf

RUN mkdir /run/mysql

EXPOSE 3306
CMD ["mariadbd"]
""",
        )
    ]


with open(
    os.path.join(os.path.dirname(__file__), "rmt", "entrypoint.sh")
) as entrypoint:
    RMT_CONTAINER = ApplicationStackContainer(
        name="rmt-server",
        ibs_package="suse-rmt-server-container",
        sp_version=4,
        release_stage=ReleaseStage.BETA,
        maintainer="bruno.leon@suse.de",
        pretty_name="RMT Server",
        version="2.7",
        package_list=["rmt-server", "catatonit"],
        entrypoint="/usr/local/bin/entrypoint.sh",
        env={"RAILS_ENV": "production", "LANG": "en"},
        extra_files={"entrypoint.sh": entrypoint.read(-1)},
        custom_end="""COPY entrypoint.sh /usr/local/bin/entrypoint.sh
CMD ["/usr/share/rmt/bin/rails", "server", "-e", "production"]
""",
    )


with open(
    os.path.join(os.path.dirname(__file__), "postgres", "entrypoint.sh")
) as entrypoint:
    _POSTGRES_ENTRYPOINT = entrypoint.read(-1)

with open(
    os.path.join(os.path.dirname(__file__), "postgres", "LICENSE")
) as license_file:
    _POSTGRES_LICENSE = license_file.read(-1)


_POSTGRES_MAJOR_VERSIONS = [14, 13, 12, 10]
POSTGRES_CONTAINERS = [
    ApplicationStackContainer(
        ibs_package=f"postgres-{ver}-image",
        sp_version=4,
        release_stage=ReleaseStage.BETA,
        name="postgres",
        pretty_name=f"PostgreSQL {ver}",
        package_list=[f"postgresql{ver}-server", "distribution-release"],
        version=ver,
        additional_versions=[f"%%pg_version%%"],
        entrypoint='["docker-entrypoint.sh"]',
        env={
            "LANG": "en_US.utf8",
            "PG_MAJOR": f"{ver}",
            "PG_VERSION": f"%%pg_version%%",
            "PGDATA": "/var/lib/postgresql/data",
        },
        extra_files={
            "docker-entrypoint.sh": _POSTGRES_ENTRYPOINT,
            "LICENSE": _POSTGRES_LICENSE,
        },
        replacements_via_service=[
            Replacement(
                regex_in_dockerfile="%%pg_version%%",
                package_name=f"postgresql{ver}-server",
                parse_version="minor",
            )
        ],
        custom_end=rf"""
VOLUME /var/lib/postgresql/data

COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh && \
    ln -s su /usr/bin/gosu && \
    mkdir /docker-entrypoint-initdb.d && \
    sed -ri "s|^#?(listen_addresses)\s*=\s*\S+.*|\1 = '*'|" /usr/share/postgresql{ver}/postgresql.conf.sample

STOPSIGNAL SIGINT
EXPOSE 5432
CMD ["postgres"]
""",
    )
    for ver in _POSTGRES_MAJOR_VERSIONS
]


_NGINX_FILES = {}
for filename in (
    "docker-entrypoint.sh",
    "LICENSE",
    "10-listen-on-ipv6-by-default.sh",
    "20-envsubst-on-templates.sh",
    "30-tune-worker-processes.sh",
    "index.html",
):
    with open(os.path.join(os.path.dirname(__file__), "nginx", filename)) as cursor:
        _NGINX_FILES[filename] = cursor.read(-1)


NGINX = ApplicationStackContainer(
    ibs_package="rmt-nginx-image",
    sp_version=4,
    release_stage=ReleaseStage.BETA,
    name="rmt-nginx",
    pretty_name="RMT Nginx",
    version="1.19",
    package_list=["nginx", "distribution-release"],
    entrypoint='["/docker-entrypoint.sh"]',
    extra_files=_NGINX_FILES,
    custom_end="""
RUN mkdir /docker-entrypoint.d
COPY 10-listen-on-ipv6-by-default.sh /docker-entrypoint.d/
COPY 20-envsubst-on-templates.sh /docker-entrypoint.d/
COPY 30-tune-worker-processes.sh /docker-entrypoint.d/
COPY docker-entrypoint.sh /
RUN chmod +x /docker-entrypoint.d/10-listen-on-ipv6-by-default.sh
RUN chmod +x /docker-entrypoint.d/20-envsubst-on-templates.sh
RUN chmod +x /docker-entrypoint.d/30-tune-worker-processes.sh
RUN chmod +x /docker-entrypoint.sh

COPY index.html /srv/www/htdocs/

RUN ln -sf /dev/stdout /var/log/nginx/access.log
RUN ln -sf /dev/stderr /var/log/nginx/error.log

EXPOSE 80

STOPSIGNAL SIGQUIT

CMD ["nginx", "-g", "daemon off;"]
""",
)


# PHP_VERSIONS = [7, 8]
# (PHP_7, PHP_8) = (
#     LanguageStackContainer(
#         name="php",
#         pretty_name=f"PHP {ver}",
#         package_list=[
#             f"php{ver}",
#             f"php{ver}-composer",
#             f"php{ver}-zip",
#             f"php{ver}-zlib",
#             f"php{ver}-phar",
#             f"php{ver}-mbstring",
#             "curl",
#             "git-core",
#             "distribution-release",
#         ],
#         version=ver,
#         env={
#             "PHP_VERSION": {7: "7.4.25", 8: "8.0.10"}[ver],
#             "COMPOSER_VERSION": "1.10.22",
#         },
#     )
#     for ver in PHP_VERSIONS
# )


RUST_CONTAINERS = [
    LanguageStackContainer(
        name="rust",
        ibs_package="rust-{ver}-image",
        release_stage=ReleaseStage.BETA,
        sp_version=4,
        pretty_name=f"Rust {rust_version}",
        package_list=[
            f"rust{rust_version}",
            f"cargo{rust_version}",
            "distribution-release",
        ],
        version=rust_version,
        env={"RUST_VERSION": rust_version},
    )
    for rust_version in ("1.56", "1.57")
]
