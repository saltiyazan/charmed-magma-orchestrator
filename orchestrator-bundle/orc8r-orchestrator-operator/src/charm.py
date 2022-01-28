#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from typing import List

from charms.observability_libs.v0.kubernetes_service_patch import KubernetesServicePatch
from lightkube import Client
from lightkube.models.core_v1 import SecretVolumeSource, Volume, VolumeMount
from lightkube.resources.apps_v1 import StatefulSet
from ops.charm import CharmBase
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus
from ops.pebble import ConnectionError, ExecError, Layer

logger = logging.getLogger(__name__)


class MagmaOrc8rOrchestratorCharm(CharmBase):
    def __init__(self, *args):
        """An instance of this object everytime an event occurs."""
        super().__init__(*args)
        self._container_name = self._service_name = "magma-orc8r-orchestrator"
        self._container = self.unit.get_container(self._container_name)
        self.framework.observe(
            self.on.magma_orc8r_orchestrator_pebble_ready,
            self._on_magma_orc8r_orchestrator_pebble_ready,
        )
        self.framework.observe(
            self.on.certifier_relation_changed, self._on_certifier_relation_changed
        )
        self.framework.observe(
            self.on.create_orchestrator_admin_user_action,
            self._create_orchestrator_admin_user_action,
        )
        self.framework.observe(self.on.set_log_verbosity_action, self._set_log_verbosity_action)
        self._service_patcher = KubernetesServicePatch(
            charm=self,
            ports=[("grpc", 9180, 9112), ("http", 8080, 10112)],
            additional_labels={
                "app.kubernetes.io/part-of": "orc8r-app",
                "orc8r.io/analytics_collector": "true",
                "orc8r.io/mconfig_builder": "true",
                "orc8r.io/metrics_exporter": "true",
                "orc8r.io/obsidian_handlers": "true",
                "orc8r.io/state_indexer": "true",
                "orc8r.io/stream_provider": "true",
                "orc8r.io/swagger_spec": "true",
            },
            additional_annotations={
                "orc8r.io/state_indexer_types": "directory_record",
                "orc8r.io/state_indexer_version": "1",
                "orc8r.io/stream_provider_streams": "configs",
                "orc8r.io/obsidian_handlers_path_prefixes": "/, "
                "/magma/v1/channels, "
                "/magma/v1/networks, "
                "/magma/v1/networks/:network_id,",
            },
        )

    def _create_orchestrator_admin_user_action(self, event):
        process = self._container.exec(
            [
                "/var/opt/magma/bin/accessc",
                "add-existing",
                "-admin",
                "-cert",
                "/var/opt/magma/certs/admin_operator.pem",
                "admin_operator",
            ],
            timeout=30,
            environment=self._environment_variables,
            working_dir="/",
        )
        try:
            stdout, error = process.wait_output()
            logger.info(f"Return message: {stdout}, {error}")
        except ExecError as e:
            logger.error("Exited with code %d. Stderr:", e.exit_code)
            for line in e.stderr.splitlines():
                logger.error("    %s", line)

    def _set_log_verbosity_action(self, event):
        process = self._container.exec(
            [
                "/var/opt/magma/bin/service303_cli",
                "log_verbosity",
                str(event.params["level"]),
                event.params["service"],
            ],
            timeout=30,
            environment=self._environment_variables,
            working_dir="/",
        )
        try:
            stdout, error = process.wait_output()
            logger.info(f"Return message: {stdout}, {error}")
        except ExecError as e:
            logger.error("Exited with code %d. Stderr:", e.exit_code)
            for line in e.stderr.splitlines():
                logger.error("    %s", line)

    def _on_magma_orc8r_orchestrator_pebble_ready(self, event):
        if not self._relations_ready:
            event.defer()
            return
        self._configure_orc8r(event)

    @property
    def _relations_ready(self) -> bool:
        """Checks whether required relations are ready."""
        required_relations = ["certifier"]
        missing_relations = [
            relation
            for relation in required_relations
            if not self.model.get_relation(relation)
            or len(self.model.get_relation(relation).units) == 0  # noqa: W503
        ]
        if missing_relations:
            msg = f"Waiting for relations: {', '.join(missing_relations)}"
            self.unit.status = BlockedStatus(msg)
            return False
        return True

    def _on_certifier_relation_changed(self, event):
        """Mounts certificates required by orc8r-orchestrator."""
        if not self._nms_certs_mounted:
            self.unit.status = MaintenanceStatus("Mounting NMS certificates...")
            self._mount_certifier_certs()

    @property
    def _nms_certs_mounted(self) -> bool:
        """Check to see if the NMS certs have already been mounted."""
        client = Client()
        statefulset = client.get(StatefulSet, name=self.app.name, namespace=self._namespace)
        return all(
            volume_mount in statefulset.spec.template.spec.containers[1].volumeMounts  # type: ignore[attr-defined]  # noqa: E501
            for volume_mount in self._magma_orc8r_orchestrator_volume_mounts
        )

    def _mount_certifier_certs(self) -> None:
        """Patch the StatefulSet to include NMS certs secret mount."""
        self.unit.status = MaintenanceStatus(
            "Mounting additional volumes required by the magma-orc8r-orchestrator container..."
        )
        client = Client()
        stateful_set = client.get(StatefulSet, name=self.app.name, namespace=self._namespace)
        stateful_set.spec.template.spec.volumes.extend(self._magma_orc8r_orchestrator_volumes)  # type: ignore[attr-defined]  # noqa: E501
        stateful_set.spec.template.spec.containers[1].volumeMounts.extend(  # type: ignore[attr-defined]  # noqa: E501
            self._magma_orc8r_orchestrator_volume_mounts
        )
        client.patch(StatefulSet, name=self.app.name, obj=stateful_set, namespace=self._namespace)
        logger.info("Additional K8s resources for magma-orc8r-orchestrator container applied!")

    @property
    def _magma_orc8r_orchestrator_volume_mounts(self) -> List[VolumeMount]:
        """Returns the additional volume mounts for the magma-orc8r-orchestrator container."""
        return [
            VolumeMount(
                mountPath="/var/opt/magma/certs/",
                name="certs",
                readOnly=True,
            )
        ]

    @property
    def _magma_orc8r_orchestrator_volumes(self) -> List[Volume]:
        """Returns the additional volumes required by the magma-orc8r-orchestrator container."""
        return [
            Volume(
                name="certs",
                secret=SecretVolumeSource(secretName="orc8r-certs"),
            ),
        ]

    def _configure_orc8r(self, event):
        """Adds layer to pebble config if the proposed config is different from the current one."""
        try:
            plan = self._container.get_plan()
            if plan.services != self._pebble_layer.services:
                self._container.add_layer(self._container_name, self._pebble_layer, combine=True)
                self._container.restart(self._service_name)
                logger.info(f"Restarted container {self._service_name}")
                self.unit.status = ActiveStatus()
        except ConnectionError:
            logger.error(
                f"Could not restart {self._service_name} -- Pebble socket does "
                f"not exist or is not responsive"
            )

    @property
    def _environment_variables(self) -> dict:
        return {
            "SERVICE_HOSTNAME": self._container_name,
            "SERVICE_REGISTRY_MODE": "k8s",
            "SERVICE_REGISTRY_NAMESPACE": self._namespace,
        }

    @property
    def _pebble_layer(self) -> Layer:
        return Layer(
            {
                "summary": f"{self._service_name} pebble layer",
                "services": {
                    self._service_name: {
                        "override": "replace",
                        "startup": "enabled",
                        "command": "/usr/bin/envdir "
                        "/var/opt/magma/envdir "
                        "/var/opt/magma/bin/orchestrator "
                        "-run_echo_server=true "
                        "-logtostderr=true -v=0",
                        "environment": self._environment_variables,
                    }
                },
            }
        )

    @property
    def _namespace(self) -> str:
        return self.model.name


if __name__ == "__main__":
    main(MagmaOrc8rOrchestratorCharm)
