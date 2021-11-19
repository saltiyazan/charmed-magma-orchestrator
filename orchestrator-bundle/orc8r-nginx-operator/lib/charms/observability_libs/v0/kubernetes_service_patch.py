# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

"""# KubernetesServicePatch Library.

This library is designed to enable developers to more simply patch the Kubernetes Service created
by Juju during the deployment of a sidecar charm. When sidecar charms are deployed, Juju creates a
service named after the application in the namespace (named after the Juju model). This service by
default contains a "placeholder" port, which is 65536/TCP.

When modifying the default set of resources managed by Juju, one must consider the lifecycle of the
charm. In this case, any modifications to the default service (created during deployment), will
be overwritten during a charm upgrade.

When intialised, this library binds a handler to the parent charm's `install` and `upgrade_charm`
events which applies the patch to the cluster. This should ensure that the service ports are
correct throughout the charm's life.

The constructor simply takes a reference to the parent charm, and a list of tuples that each define
a port for the service, where each tuple contains:

- a name for the port
- port for the service to listen on
- optionally: a targetPort for the service (the port in the container!)
- optionally: a nodePort for the service (for NodePort or LoadBalancer services only!)

## Getting Started

To get started using the library, you just need to fetch the library using `charmcraft`. **Note
that you also need to add `lightkube` and `lightkube-models` to your charm's `requirements.txt`.**

```shell
cd some-charm
charmcraft fetch-lib charms.observability_libs.v0.kubernetes_service_patch
echo <<-EOF >> requirements.txt
lightkube
lightkube-models
EOF
```

Then, to initialise the library:

For ClusterIP services:
```python
# ...
from charms.observability_libs.v0.kubernetes_service_patch import KubernetesServicePatch

class SomeCharm(CharmBase):
  def __init__(self, *args):
    # ...
    self.service_patcher = KubernetesServicePatch(self, [(f"{self.app.name}", 8080)])
    # ...
```

For LoadBalancer/NodePort services:
```python
# ...
from charms.observability_libs.v0.kubernetes_service_patch import KubernetesServicePatch

class SomeCharm(CharmBase):
  def __init__(self, *args):
    # ...
    self.service_patcher = KubernetesServicePatch(
        self, [(f"{self.app.name}", 443, 443, 30666)], "LoadBalancer"
    )
    # ...
```

Additionally, you may wish to use mocks in your charm's unit testing to ensure that the library
does not try to make any API calls, or open any files during testing that are unlikely to be
present, and could break your tests. The easiest way to do this is during your test `setUp`:

```python
# ...

@patch("charm.KubernetesServicePatch", lambda x, y: None)
def setUp(self, *unused):
    self.harness = Harness(SomeCharm)
    # ...
```
"""

import logging
from types import MethodType
from typing import Literal, Sequence, Tuple, Union

from lightkube import ApiError, Client  # type: ignore[import]
from lightkube.models.core_v1 import ServicePort, ServiceSpec  # type: ignore[import]
from lightkube.models.meta_v1 import ObjectMeta  # type: ignore[import]
from lightkube.resources.core_v1 import Service  # type: ignore[import]
from lightkube.types import PatchType  # type: ignore[import]
from ops.charm import CharmBase
from ops.framework import Object

logger = logging.getLogger(__name__)

# The unique Charmhub library identifier, never change it
LIBID = "0042f86d0a874435adef581806cddbbb"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 4

PortDefinition = Union[Tuple[str, int], Tuple[str, int, int], Tuple[str, int, int, int]]
ServiceType = Literal["ClusterIP", "LoadBalancer"]


class KubernetesServicePatch(Object):
    """A utility for patching the Kubernetes service set up by Juju."""

    def __init__(
        self,
        charm: CharmBase,
        ports: Sequence[PortDefinition],
        service_type: ServiceType = "ClusterIP",
    ):
        """Constructor for KubernetesServicePatch.

        Args:
            charm: the charm that is instantiating the library.
            ports: a list of tuples (name, port, targetPort, nodePort) for every service port.
            service_type: desired type of K8s service. Default value is in line with ServiceSpec's
                default value.
        """
        super().__init__(charm, "kubernetes-service-patch")
        self.charm = charm
        self.service = self._service_object(ports, service_type)

        # Make mypy type checking happy that self._patch is a method
        assert isinstance(self._patch, MethodType)
        # Ensure this patch is applied during the 'install' and 'upgrade-charm' events
        self.framework.observe(charm.on.install, self._patch)
        self.framework.observe(charm.on.upgrade_charm, self._patch)

    def _service_object(
        self, ports: Sequence[PortDefinition], service_type: ServiceType = "ClusterIP"
    ) -> Service:
        """Creates a valid Service representation for Alertmanager.

        Args:
            ports: a list of tuples of the form (name, port) or (name, port, targetPort)
                or (name, port, targetPort, nodePort) for every service port. If the 'targetPort'
                is omitted, it is assumed to be equal to 'port', with the exception of NodePort
                and LoadBalancer services, where all port numbers have to be specified.
            service_type: desired type of K8s service. Default value is in line with ServiceSpec's
                default value.

        Returns:
            Service: A valid representation of a Kubernetes Service with the correct ports.
        """
        return Service(
            apiVersion="v1",
            kind="Service",
            metadata=ObjectMeta(
                namespace=self._namespace,
                name=self._app,
                labels={"app.kubernetes.io/name": self._app},
            ),
            spec=ServiceSpec(
                selector={"app.kubernetes.io/name": self._app},
                ports=[
                    ServicePort(
                        name=p[0],
                        port=p[1],
                        targetPort=p[2] if len(p) > 2 else p[1],  # type: ignore[misc]
                        nodePort=p[3] if len(p) > 3 else None,  # type: ignore[misc]
                    )
                    for p in ports
                ],
                type=service_type,
            ),
        )

    def _patch(self, _) -> None:
        """Patch the Kubernetes service created by Juju to map the correct port.

        Raises:
            PatchFailed: if patching fails due to lack of permissions, or otherwise.
        """
        if not self.charm.unit.is_leader():
            return

        client = Client()
        try:
            client.patch(Service, self._app, self.service, patch_type=PatchType.MERGE)
        except ApiError as e:
            if e.status.code == 403:
                logger.error("Kubernetes service patch failed: `juju trust` this application.")
            else:
                logger.error("Kubernetes service patch failed: %s", str(e))
        else:
            logger.info("Kubernetes service '%s' patched successfully", self._app)

    def is_patched(self) -> bool:
        """Reports if the service patch has been applied.

        Returns:
            bool: A boolean indicating if the service patch has been applied.
        """
        client = Client()
        # Get the relevant service from the cluster
        service = client.get(Service, name=self._app, namespace=self._namespace)
        # Construct a list of expected ports, should the patch be applied
        expected_ports = [(p.port, p.targetPort) for p in self.service.spec.ports]
        # Construct a list in the same manner, using the fetched service
        fetched_ports = [(p.port, p.targetPort) for p in service.spec.ports]
        return expected_ports == fetched_ports

    @property
    def _app(self) -> str:
        """Name of the current Juju application.

        Returns:
            str: A string containing the name of the current Juju application.
        """
        return self.charm.app.name

    @property
    def _namespace(self) -> str:
        """The Kubernetes namespace we're running in.

        Returns:
            str: A string containing the name of the current Kubernetes namespace.
        """
        with open("/var/run/secrets/kubernetes.io/serviceaccount/namespace", "r") as f:
            return f.read().strip()
