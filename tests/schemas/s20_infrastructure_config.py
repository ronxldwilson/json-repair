from pydantic import BaseModel


class Taint(BaseModel):
    key: str
    value: str
    effect: str


class NodePool(BaseModel):
    name: str
    instance_type: str
    min_nodes: int
    max_nodes: int
    auto_scaling: bool
    labels: dict[str, str]
    taints: list[Taint]


class DnsConfig(BaseModel):
    provider: str
    zone: str
    ttl: int


class Networking(BaseModel):
    vpc_cidr: str
    subnet_cidrs: list[str]
    enable_nat: bool
    enable_vpn: bool
    dns: DnsConfig


class PrometheusConfig(BaseModel):
    enabled: bool
    retention_days: int
    storage_gb: int


class GrafanaConfig(BaseModel):
    enabled: bool
    admin_email: str
    dashboards: list[str]


class AlertChannel(BaseModel):
    type: str
    webhook: str | None = None
    api_key: str | None = None
    severity: list[str]


class AlertingConfig(BaseModel):
    enabled: bool
    channels: list[AlertChannel]


class Monitoring(BaseModel):
    prometheus: PrometheusConfig
    grafana: GrafanaConfig
    alerting: AlertingConfig


class Cluster(BaseModel):
    name: str
    provider: str
    region: str
    kubernetes_version: str
    node_pools: list[NodePool]
    networking: Networking
    monitoring: Monitoring


class InfraConfig(BaseModel):
    cluster: Cluster
