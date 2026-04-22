#!/usr/bin/env python3
"""
OpenShift Operator Channel Checker
- Shows OpenShift cluster version
- Lists installed Operators with current channel/version
- Detects if a newer version is available in another channel
"""

import sys
import argparse
import json
import urllib3
import re
from packaging import version as pkg_version
from kubernetes import client, config, dynamic
from kubernetes.client.exceptions import ApiException

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def extract_version(text: str) -> str:
    if not text:
        return "0.0.0"
    match = re.search(r'(\d+\.\d+(?:\.\d+)?)', text)
    return match.group(1) if match else "0.0.0"


def is_newer_version(current: str, candidate: str) -> bool:
    try:
        return pkg_version.parse(candidate) > pkg_version.parse(current)
    except Exception:
        return False


def get_openshift_version(dyn_client) -> str:
    """Get OpenShift cluster version from ClusterVersion resource"""
    try:
        cv_api = dyn_client.resources.get(
            api_version="config.openshift.io/v1",
            kind="ClusterVersion"
        )
        cv = cv_api.get(name="version")
        return getattr(cv.status, "desired", {}).get("version", "Unknown")
    except Exception:
        return "Unknown"


def truncate(text: str, length: int = 48) -> str:
    return (text[:length] + "…") if len(text) > length else text


def main():
    parser = argparse.ArgumentParser(description="OpenShift Operator Channel Checker")
    parser.add_argument("--kubeconfig", help="Path to kubeconfig file")
    parser.add_argument("--output", choices=["table", "json"], default="table")
    args = parser.parse_args()

    # Load config
    try:
        if args.kubeconfig:
            config.load_kube_config(config_file=args.kubeconfig)
        else:
            config.load_kube_config()
    except Exception:
        try:
            config.load_incluster_config()
            print("ℹ️  Running inside the cluster")
        except Exception:
            print("❌ Could not load Kubernetes config. Run 'oc login' first.")
            sys.exit(1)

    client.Configuration.get_default_copy().verify_ssl = False

    k8s_client = client.ApiClient()
    dyn_client = dynamic.DynamicClient(k8s_client)

    # Get OpenShift version
    openshift_version = get_openshift_version(dyn_client)
    print(f"OpenShift Version: {openshift_version}\n")

    sub_api = dyn_client.resources.get(api_version="operators.coreos.com/v1alpha1", kind="Subscription")

    try:
        pm_api = dyn_client.resources.get(api_version="packages.operators.coreos.com/v1", kind="PackageManifest")
    except Exception:
        pm_api = dyn_client.resources.get(api_version="packages.operators.coreos.com/v1alpha1", kind="PackageManifest")

    core_v1 = client.CoreV1Api()
    namespaces = [ns.metadata.name for ns in core_v1.list_namespace().items]

    operators = []
    print("🔍 Scanning installed Operators...\n")

    for ns in namespaces:
        try:
            subs = sub_api.get(namespace=ns)
            if not getattr(subs, 'items', None):
                continue

            for sub in subs.items:
                if not sub.spec or not getattr(sub.spec, 'name', None):
                    continue

                package_name = sub.spec.name
                current_channel = getattr(sub.spec, 'channel', 'default') or 'default'
                sub_ns = sub.metadata.namespace

                current_csv = getattr(sub.status, 'currentCSV', 'Unknown')
                current_version = extract_version(current_csv)

                try:
                    pm = pm_api.get(name=package_name, namespace="openshift-marketplace")
                    channel_list = getattr(pm.status, 'channels', []) or []
                except Exception:
                    channel_list = []

                other_info = []
                has_newer = False

                for ch in channel_list:
                    ch_name = getattr(ch, 'name', '')
                    if ch_name == current_channel:
                        continue

                    ch_csv = getattr(ch, 'currentCSV', '') if hasattr(ch, 'currentCSV') else ''
                    ch_version = extract_version(ch_csv)

                    if is_newer_version(current_version, ch_version):
                        has_newer = True

                    other_info.append(f"{ch_name} ({ch_version})")

                operators.append({
                    "operator": package_name,
                    "namespace": sub_ns,
                    "current_channel": current_channel,
                    "current_version": current_version,
                    "other_channels": ", ".join(other_info) if other_info else "—",
                    "newer_available": "✅ Yes" if has_newer else "❌ No"
                })

        except Exception:
            continue

    if not operators:
        print("⚠️  No Operators found.")
        return

    if args.output == "json":
        print(json.dumps({"openshift_version": openshift_version, "operators": operators}, indent=2))
        return

    # ====================== TABLE WITH EXTRA SPACING ======================
    w_operator   = 38
    w_namespace  = 26
    w_channel    = 26
    w_version    = 12
    w_other      = 48
    w_upgrade    = 18

    header = (f"{'Operator':<{w_operator}} "
              f"{'Namespace':<{w_namespace}} "
              f"{'Current Channel':<{w_channel}} "
              f"{'Version':<{w_version}} "
              f"{'Other Channels':<{w_other}} "
              f"{'Upgrade Available'}")

    separator = "─" * (w_operator + w_namespace + w_channel + w_version + w_other + w_upgrade + 6)

    print(header)
    print(separator)

    for op in sorted(operators, key=lambda x: x['operator'].lower()):
        other_str = truncate(op['other_channels'], w_other - 2)

        print(f"{op['operator']:<{w_operator}} "
              f"{op['namespace']:<{w_namespace}} "
              f"{op['current_channel']:<{w_channel}} "
              f"{op['current_version']:<{w_version}} "
              f"{other_str:<{w_other}} "
              f"{op['newer_available']:<{w_upgrade}}")

    print(separator)
    print("\n✅ Done!")
    print("   ✅ Yes  = Newer version available in another channel")
    print("   ❌ No   = Current version is the latest among available channels")


if __name__ == "__main__":
    main()