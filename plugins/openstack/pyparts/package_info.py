from core.plugins.openstack import (
    OpenstackPackageChecksBase,
    OpenstackDockerImageChecksBase,
    OpenstackPackageBugChecksBase,
)

YAML_PRIORITY = 1


class OpenstackPackageChecks(OpenstackPackageChecksBase):

    def __call__(self):
        # require at least one core package to be installed to include
        # this in the report.
        if self.apt_check.core:
            self._output["dpkg"] = self.apt_check.all_formatted


class OpenstackDockerImageChecks(OpenstackDockerImageChecksBase):

    def __call__(self):
        # require at least one core image to be in-use to include
        # this in the report.
        if self.core:
            self._output["docker-images"] = self.all_formatted


class OpenstackPackageBugChecks(OpenstackPackageBugChecksBase):
    pass
