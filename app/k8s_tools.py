import logging
from kubernetes import client, config
from kubernetes.client.rest import ApiException

log = logging.getLogger(__name__)

# Namespaces the bot is allowed to look at — keeps blast radius small
ALLOWED_NAMESPACES = {"default", "monitoring", "kube-system"}


def _load_config():
    """Use in-cluster config when running as a pod, fall back to local kubeconfig for dev."""
    try:
        config.load_incluster_config()
        log.info("Loaded in-cluster Kubernetes config")
    except config.ConfigException:
        config.load_kube_config()
        log.info("Loaded local kubeconfig")


_load_config()
core_v1 = client.CoreV1Api()
apps_v1 = client.AppsV1Api()


def _check_namespace(namespace: str) -> None:
    if namespace not in ALLOWED_NAMESPACES:
        raise PermissionError(
            f"Namespace '{namespace}' is not in the allowed list: {sorted(ALLOWED_NAMESPACES)}"
        )


def list_pods(namespace: str = "default") -> list[dict]:
    """List pods in a namespace with status, restarts, and node."""
    _check_namespace(namespace)
    pods = core_v1.list_namespaced_pod(namespace)
    result = []
    for pod in pods.items:
        restarts = sum(cs.restart_count for cs in (pod.status.container_statuses or []))
        result.append({
            "name": pod.metadata.name,
            "status": pod.status.phase,
            "restarts": restarts,
            "node": pod.spec.node_name,
            "created": pod.metadata.creation_timestamp.isoformat() if pod.metadata.creation_timestamp else None,
        })
    return result


def get_pod_logs(namespace: str, pod_name: str, tail_lines: int = 50) -> str:
    """Get the last N lines of logs for a pod."""
    _check_namespace(namespace)
    try:
        return core_v1.read_namespaced_pod_log(
            name=pod_name, namespace=namespace, tail_lines=tail_lines
        )
    except ApiException as e:
        return f"Could not fetch logs: {e.reason}"


def describe_pod(namespace: str, pod_name: str) -> dict:
    """Get pod status, container states, and recent events — use to debug unhealthy pods."""
    _check_namespace(namespace)
    pod = core_v1.read_namespaced_pod(name=pod_name, namespace=namespace)
    containers = []
    for cs in pod.status.container_statuses or []:
        state = "running" if cs.state.running else "waiting" if cs.state.waiting else "terminated"
        reason = (cs.state.waiting.reason if cs.state.waiting else
                  cs.state.terminated.reason if cs.state.terminated else None)
        containers.append({
            "name": cs.name,
            "ready": cs.ready,
            "restart_count": cs.restart_count,
            "state": state,
            "reason": reason,
        })

    events = core_v1.list_namespaced_event(
        namespace, field_selector=f"involvedObject.name={pod_name}"
    )
    recent_events = [
        {"reason": e.reason, "message": e.message, "type": e.type}
        for e in events.items[-5:]
    ]

    return {
        "name": pod.metadata.name,
        "phase": pod.status.phase,
        "node": pod.spec.node_name,
        "containers": containers,
        "recent_events": recent_events,
    }


def list_deployments(namespace: str = "default") -> list[dict]:
    """List deployments with replica status."""
    _check_namespace(namespace)
    deployments = apps_v1.list_namespaced_deployment(namespace)
    return [
        {
            "name": d.metadata.name,
            "ready_replicas": d.status.ready_replicas or 0,
            "desired_replicas": d.spec.replicas,
            "available": d.status.available_replicas or 0,
        }
        for d in deployments.items
    ]


def get_events(namespace: str = "default") -> list[dict]:
    """Get the most recent events in a namespace."""
    _check_namespace(namespace)
    events = core_v1.list_namespaced_event(namespace)
    sorted_events = sorted(
        events.items, key=lambda e: e.last_timestamp or e.event_time or "", reverse=True
    )[:15]
    return [
        {
            "object": e.involved_object.name,
            "reason": e.reason,
            "message": e.message,
            "type": e.type,
        }
        for e in sorted_events
    ]


def list_namespaces() -> list[str]:
    """List namespaces the bot is permitted to see."""
    return sorted(ALLOWED_NAMESPACES)